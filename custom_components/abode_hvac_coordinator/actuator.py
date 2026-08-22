"""Actuation.

Turns a decision into service calls. The decision itself is made in `modes.py`;
nothing here chooses what should happen, only how it is carried out.

WHAT IS CALLED AND WHY
----------------------
**Climate — standard `climate.*` services only.** Versatile Thermostat exposes
no service for setting a temperature; its own `services.yaml` covers presence,
safety, regulation modes and presets, and the setpoint goes through the
standard `climate.set_temperature`. Using the standard services means this
works identically against a Versatile Thermostat wrapper or a bare
manufacturer entity, which is what keeps Layer 3 indifferent to Layer 1.

Every call is checked against the entity's advertised capabilities first. An
HVAC mode the unit does not support, or a fan mode it does not have, is a
rejection recorded in the trace rather than a service call that errors.

**Covers — standard `cover.set_cover_position`.** This integration owns cover
decisions; it does not delegate them. Sun geometry is worked out in `sun.py`
from the window direction and the sun's position, so there is nothing to hand
off to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_SWING_MODE,
    ATTR_SWING_MODES,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_SWING_MODE,
    SERVICE_SET_TEMPERATURE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
)
from homeassistant.components.cover import (
    DOMAIN as COVER_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    SERVICE_SET_COVER_POSITION,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError

from .const import LOGGER
from .models import ActuatorStep, DecisionTrace, RoomConfig

if TYPE_CHECKING:
    from .coordinator import HvacCoordinator

#: How far either side of the target a range-only unit is asked to straddle.
#: Units taking a low/high pair need a band, not a point.
RANGE_DEADBAND_C = 1.0

#: Cover positions. Fully closed blocks solar gain; fully open admits it.
COVER_BLOCK_GAIN = 0
COVER_ADMIT_GAIN = 100

#: Fan mode preferred when running the fan alone or drying, in order. Matched
#: case-insensitively against whatever the unit advertises; if none match, the
#: fan mode is left alone rather than guessed at.
QUIET_FAN_MODES = ("quiet", "silent", "low", "min", "level1", "level 1", "1")

#: Fan mode preferred when the compressor is working and the room is out of
#: band — mixing matters more than noise. Tried in order.
ACTIVE_FAN_MODES = ("auto", "medium", "mid", "level3", "level 3", "3", "high")

#: Swing mode preferred when actively conditioning. Moving air mixes the room,
#: which is what the comfort index is measuring. Tried in order; if the unit
#: advertises none of them the swing setting is left untouched.
MIXING_SWING_MODES = ("both", "3d auto", "3d", "vertical", "on", "swing", "auto")

#: Swing mode preferred when idling in fan or dry — quieter and less draughty.
SETTLED_SWING_MODES = ("off", "hold", "fixed", "middle", "center")

#: Cooling, in order of preference. A unit with no dedicated cool mode may
#: still cool in heat_cool or auto, so those are real fallbacks rather than
#: a failure. Same reasoning inverted for heating.
COOL_MODES = (HVACMode.COOL, HVACMode.HEAT_COOL, HVACMode.AUTO)
HEAT_MODES = (HVACMode.HEAT, HVACMode.HEAT_COOL, HVACMode.AUTO)
DRY_MODES = (HVACMode.DRY,)
FAN_MODES_ORDER = (HVACMode.FAN_ONLY,)
OFF_MODES = (HVACMode.OFF,)


def supported_hvac_modes(state: State) -> set[str]:
    """The HVAC modes a climate entity advertises."""
    return {str(mode) for mode in (state.attributes.get(ATTR_HVAC_MODES) or [])}


def resolve_hvac_mode(
    state: State, preferred: tuple[HVACMode, ...]
) -> HVACMode | None:
    """First preferred mode the unit actually advertises, or None."""
    available = supported_hvac_modes(state)
    return next((mode for mode in preferred if mode in available), None)


def _pick(available: list[str], preferred: tuple[str, ...]) -> str | None:
    """First preferred value present in a list, matched case-insensitively."""
    lowered = {value.lower(): value for value in available}
    return next((lowered[name] for name in preferred if name in lowered), None)


def mean_cover_position(hass: HomeAssistant, entity_ids: tuple[str, ...]) -> float | None:
    """Mean reported position across a room's covers, or None if none report."""
    positions = [
        float(position)
        for entity_id in entity_ids
        if (state := hass.states.get(entity_id)) is not None
        and (position := state.attributes.get(ATTR_CURRENT_POSITION)) is not None
    ]
    if not positions:
        return None
    return sum(positions) / len(positions)


class Actuator:
    """Carries out decisions. One instance per coordinator."""

    def __init__(self, hass: HomeAssistant, coordinator: HvacCoordinator) -> None:
        """Initialize the actuator."""
        self.hass = hass
        self.coordinator = coordinator
        #: Last command sent per room, so an unchanged decision is not re-sent
        #: every evaluation. Keyed by room, holding (hvac_mode, temperature).
        self._last_climate: dict[str, tuple[str, float | None]] = {}
        self._last_cover: dict[str, int] = {}

    async def async_apply(self, room: RoomConfig, trace: DecisionTrace) -> None:
        """Carry out one room's decision.

        `trace.hold_compressor` is set when the short-cycle guard refused to
        let the compressor stop. Every branch below that would stop it —
        commanding off, or switching to a mode that de-energises it — is
        skipped while it is set. The decision itself still stands and is still
        published; only the part that would have stopped the compressor early
        is deferred to a later cycle.
        """
        if trace.actuator is ActuatorStep.COVERS:
            await self._async_move_covers(room, trace)
            # Covers are the whole action this cycle: the point of trying them
            # first is not to spend compressor energy at the same time.
            if not trace.hold_compressor:
                await self._async_command(room, trace, OFF_MODES, None, active=False)
            return

        if trace.actuator is ActuatorStep.OFF:
            if not trace.hold_compressor:
                await self._async_command(room, trace, OFF_MODES, None, active=False)
            return

        if trace.actuator is ActuatorStep.NONE:
            # Deliberately nothing. The unit keeps the trimmed setpoint it was
            # last given and its own thermostat holds it until the next
            # evaluation thirty seconds from now. Which reasons stop the unit
            # and which leave it alone is decided in `modes.py`, not inferred
            # from the mode here — inferring it caught two of five stop paths
            # and silently missed the other three.
            return

        if trace.actuator is ActuatorStep.FAN:
            if trace.hold_compressor:
                return
            await self._async_command(room, trace, FAN_MODES_ORDER, None, active=False)
            return

        if trace.actuator is ActuatorStep.DRY:
            await self._async_command(room, trace, DRY_MODES, None, active=False)
            return

        preferred = HEAT_MODES if trace.demand == "heat" else COOL_MODES
        # The commanded setpoint, not the solved target. Layer 2 has learned
        # how far the unit's own sensor sits from the room's, and sending the
        # untrimmed target would give that difference straight back.
        await self._async_command(
            room,
            trace,
            preferred,
            trace.commanded_dry_bulb_c
            if trace.commanded_dry_bulb_c is not None
            else trace.target_dry_bulb_c,
            active=True,
        )

    async def _async_command(
        self,
        room: RoomConfig,
        trace: DecisionTrace,
        preferred: tuple[HVACMode, ...],
        temperature: float | None,
        *,
        active: bool,
    ) -> None:
        """Set the climate entity to the best mode it advertises.

        Nothing is sent that the entity has not said it can do. The mode is
        resolved against its own hvac_modes, the temperature against its
        supported features, and fan and swing against the lists it publishes.
        """
        state = self.hass.states.get(room.climate_entity_id)
        if state is None:
            trace.rejected.append(f"climate: {room.climate_entity_id} is unavailable")
            return

        hvac_mode = resolve_hvac_mode(state, preferred)
        if hvac_mode is None:
            wanted = ", ".join(str(mode) for mode in preferred)
            trace.rejected.append(
                f"climate: {room.climate_entity_id} advertises none of {wanted}"
            )
            return

        if hvac_mode is not preferred[0]:
            trace.reasons.append(
                f"{preferred[0]} unavailable, using {hvac_mode} instead"
            )

        if self._last_climate.get(room.room_id) == (hvac_mode, temperature):
            return

        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_ENTITY_ID: room.climate_entity_id, ATTR_HVAC_MODE: hvac_mode},
                blocking=True,
            )
            if temperature is not None:
                await self._async_set_temperature(
                    room, trace, state, hvac_mode, temperature
                )
            if hvac_mode is not HVACMode.OFF:
                await self._async_set_fan(room, state, active=active)
                await self._async_set_swing(room, state, active=active)
        except HomeAssistantError as err:
            # A failed actuation must not stop the other rooms being evaluated.
            trace.rejected.append(f"climate: {err}")
            LOGGER.warning(
                "Setting %s to %s failed: %s", room.climate_entity_id, hvac_mode, err
            )
            return

        self._last_climate[room.room_id] = (hvac_mode, temperature)
        trace.reasons.append(
            f"set {room.climate_entity_id} to {hvac_mode}"
            + (f" at {temperature:.1f} C" if temperature is not None else "")
        )

    async def _async_set_temperature(
        self,
        room: RoomConfig,
        trace: DecisionTrace,
        state: State,
        hvac_mode: HVACMode,
        temperature: float,
    ) -> None:
        """Set the target, as a point or a range depending on the unit.

        A unit in heat_cool or auto often takes a low/high pair rather than a
        single target. Sending a single temperature to one of those is either
        rejected or silently applied to only one side.
        """
        features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0)
        target = round(temperature, 1)

        single = bool(features & ClimateEntityFeature.TARGET_TEMPERATURE)
        ranged = bool(features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE)

        if single and not (ranged and hvac_mode in (HVACMode.HEAT_COOL, HVACMode.AUTO)):
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {ATTR_ENTITY_ID: room.climate_entity_id, ATTR_TEMPERATURE: target},
                blocking=True,
            )
            return

        if ranged:
            # Straddle the target by the deadband either side, so a range-only
            # unit is asked for something it can honour.
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {
                    ATTR_ENTITY_ID: room.climate_entity_id,
                    ATTR_TARGET_TEMP_LOW: round(target - RANGE_DEADBAND_C, 1),
                    ATTR_TARGET_TEMP_HIGH: round(target + RANGE_DEADBAND_C, 1),
                },
                blocking=True,
            )
            return

        trace.rejected.append(
            f"climate: {room.climate_entity_id} takes no temperature target"
        )

    async def _async_set_fan(
        self, room: RoomConfig, state: State, *, active: bool
    ) -> None:
        """Ask for a sensible fan mode, if the unit has fan control."""
        features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0)
        if not features & ClimateEntityFeature.FAN_MODE:
            return
        available = [str(mode) for mode in (state.attributes.get(ATTR_FAN_MODES) or [])]
        wanted = _pick(available, ACTIVE_FAN_MODES if active else QUIET_FAN_MODES)
        if wanted is None or state.attributes.get(ATTR_FAN_MODE) == wanted:
            return
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: room.climate_entity_id, ATTR_FAN_MODE: wanted},
            blocking=True,
        )

    async def _async_set_swing(
        self, room: RoomConfig, state: State, *, active: bool
    ) -> None:
        """Move the vanes, if the unit has them.

        Mixing the room matters to a comfort index measured at one sensor: a
        stratified room reads comfortable at the sensor and is not.
        """
        features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0)
        if not features & ClimateEntityFeature.SWING_MODE:
            return
        available = [
            str(mode) for mode in (state.attributes.get(ATTR_SWING_MODES) or [])
        ]
        wanted = _pick(available, MIXING_SWING_MODES if active else SETTLED_SWING_MODES)
        if wanted is None or state.attributes.get(ATTR_SWING_MODE) == wanted:
            return
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_SWING_MODE,
            {ATTR_ENTITY_ID: room.climate_entity_id, ATTR_SWING_MODE: wanted},
            blocking=True,
        )

    async def _async_move_covers(self, room: RoomConfig, trace: DecisionTrace) -> None:
        """Move the room's covers to block or admit solar gain."""
        position = COVER_BLOCK_GAIN if trace.demand == "cool" else COVER_ADMIT_GAIN
        if self._last_cover.get(room.room_id) == position:
            return

        try:
            await self.hass.services.async_call(
                COVER_DOMAIN,
                SERVICE_SET_COVER_POSITION,
                {
                    ATTR_ENTITY_ID: list(room.cover_entity_ids),
                    ATTR_POSITION: position,
                },
                blocking=True,
            )
        except HomeAssistantError as err:
            trace.rejected.append(f"covers: {err}")
            LOGGER.warning("Moving covers for %s failed: %s", room.room_id, err)
            return

        self._last_cover[room.room_id] = position
        trace.reasons.append(f"moved covers to {position}%")

    def forget(self, room_id: str) -> None:
        """Drop remembered commands for a room that is no longer configured."""
        self._last_climate.pop(room_id, None)
        self._last_cover.pop(room_id, None)
