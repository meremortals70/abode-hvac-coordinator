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

**Built from the room's stored profile (0.9.0, DR-048).** What a room's units
can do is read once, when the room is configured, and kept. Modes, fan speeds
and vane positions are chosen from that profile, not looked up again against
the live entity at command time. The live entity is still read, only to skip a
command that is already in effect.

**Every command is recorded (0.9.0, DR-052).** Each mode or setpoint command
sent is logged and kept in a short list for the diagnostics download, so a
change to a unit with no matching line was not sent by this component.

**Covers — standard `cover.set_cover_position`.** This integration owns cover
decisions; it does not delegate them. Sun geometry is worked out in `sun.py`
from the window direction and the sun's position, so there is nothing to hand
off to.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate.const import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_SWING_HORIZONTAL_MODE,
    ATTR_SWING_HORIZONTAL_MODES,
    ATTR_SWING_MODE,
    ATTR_SWING_MODES,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_SWING_HORIZONTAL_MODE,
    SERVICE_SET_SWING_MODE,
    SERVICE_SET_TEMPERATURE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
)
from homeassistant.components.cover.const import (
    DOMAIN as COVER_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    SERVICE_SET_COVER_POSITION,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .capabilities import RoomCapabilities, intersect
from .const import DOMAIN, LOGGER
from .models import ActuatorStep, DecisionTrace, RoomConfig
from .regulate import SetpointLimits, combine_limits

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

#: How many sent commands are kept for the diagnostics download (DR-052). The
#: Home Assistant log holds the rest.
COMMAND_LOG_SIZE = 200


def read_capabilities(state: State) -> RoomCapabilities | None:
    """What one climate entity offers right now, or None if it reports nothing.

    DR-048. Read once, at setup, from the entity's advertised features and
    lists. An entity that publishes no HVAC modes is not reporting: there is
    nothing to build a profile from, and the caller must not store an empty
    one as though the unit could do nothing.
    """
    modes = state.attributes.get(ATTR_HVAC_MODES)
    if not modes:
        return None
    features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0)

    def names(attribute: str, feature: ClimateEntityFeature) -> tuple[str, ...]:
        if not features & feature:
            return ()
        return tuple(str(item) for item in (state.attributes.get(attribute) or []))

    def number(attribute: str) -> float | None:
        try:
            value = state.attributes.get(attribute)
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    return RoomCapabilities(
        hvac_modes=tuple(str(mode) for mode in modes),
        fan_modes=names(ATTR_FAN_MODES, ClimateEntityFeature.FAN_MODE),
        swing_modes=names(ATTR_SWING_MODES, ClimateEntityFeature.SWING_MODE),
        swing_horizontal_modes=names(
            ATTR_SWING_HORIZONTAL_MODES, ClimateEntityFeature.SWING_HORIZONTAL_MODE
        ),
        min_temp=number(ATTR_MIN_TEMP),
        max_temp=number(ATTR_MAX_TEMP),
        target_temp_step=number(ATTR_TARGET_TEMP_STEP),
        single_target=bool(features & ClimateEntityFeature.TARGET_TEMPERATURE),
        range_target=bool(features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE),
    )


def supported_hvac_modes(state: State) -> set[str]:
    """The HVAC modes a climate entity advertises."""
    return {str(mode) for mode in (state.attributes.get(ATTR_HVAC_MODES) or [])}


def profile_from_states(
    states: Sequence[State | None],
) -> tuple[RoomCapabilities | None, str | None]:
    """A room's profile from its heads' live states, and a note if one is needed.

    DR-048. None where any head is missing or is not reporting what it can do:
    a profile built from one of two heads would offer a control the other
    cannot carry out. The intersection across the heads otherwise, with the
    setpoint step left unset where the heads' steps are not whole multiples of
    each other (the note says so).
    """
    if not states:
        return None, None
    profiles: list[RoomCapabilities] = []
    for state in states:
        if state is None:
            return None, None
        read = read_capabilities(state)
        if read is None:
            return None, None
        profiles.append(read)
    combined = intersect(profiles)
    limits, note = combine_limits(
        [SetpointLimits(p.target_temp_step, p.min_temp, p.max_temp) for p in profiles]
    )
    return replace(combined, target_temp_step=limits.step), note


def _hvac_mode_of(state: State | None) -> HVACMode | None:
    """The mode a climate entity is in, or None if it is not reporting one."""
    if state is None:
        return None
    try:
        return HVACMode(state.state)
    except ValueError:
        return None


def resolve_hvac_mode(
    state: State,
    preferred: tuple[HVACMode, ...],
    profile: RoomCapabilities | None = None,
) -> HVACMode | None:
    """First preferred mode the unit offers, or None.

    From the room's stored profile where it has one (DR-048); from the live
    entity otherwise.
    """
    available = (
        set(profile.hvac_modes) if profile is not None else supported_hvac_modes(state)
    )
    return next((mode for mode in preferred if mode in available), None)


#: Float tolerance for comparing a desired setpoint against what the entity
#: currently reports, so a rounding difference of a hundredth of a degree
#: does not read as a real divergence worth re-commanding.
_LIVE_TEMPERATURE_TOLERANCE_C = 0.05


def _matches_live_state(
    state: State, hvac_mode: HVACMode, temperature: float | None
) -> bool:
    """Whether the entity is already reporting the mode and setpoint wanted.

    0.8.11. There is no override to detect and no intent to infer from a
    mismatch — this only answers "is a command still necessary", by reading
    what the entity itself currently reports rather than trusting this
    coordinator's memory of what it last sent.
    """
    if state.state != str(hvac_mode):
        return False
    if temperature is None or hvac_mode is HVACMode.OFF:
        return True
    features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0)
    if features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE and hvac_mode in (
        HVACMode.HEAT_COOL,
        HVACMode.AUTO,
    ):
        low = state.attributes.get(ATTR_TARGET_TEMP_LOW)
        high = state.attributes.get(ATTR_TARGET_TEMP_HIGH)
        if low is None or high is None:
            return False
        return (
            abs(float(low) - (temperature - RANGE_DEADBAND_C))
            < _LIVE_TEMPERATURE_TOLERANCE_C
            and abs(float(high) - (temperature + RANGE_DEADBAND_C))
            < _LIVE_TEMPERATURE_TOLERANCE_C
        )
    current = state.attributes.get(ATTR_TEMPERATURE)
    if current is None:
        return False
    return abs(float(current) - temperature) < _LIVE_TEMPERATURE_TOLERANCE_C


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
        #: The last COMMAND_LOG_SIZE mode or setpoint commands sent, newest
        #: last, for the diagnostics download (DR-052).
        self.command_log: deque[dict[str, Any]] = deque(maxlen=COMMAND_LOG_SIZE)

    def _record_command(
        self,
        room: RoomConfig,
        entity_id: str,
        hvac_mode: str,
        temperature: float | None,
        trace: DecisionTrace | None,
    ) -> None:
        """Log a mode or setpoint command that was actually sent (DR-052).

        `trace` is None for a command the user made by hand from the room's
        controls, which carry no demand or target.
        """
        entry: dict[str, Any] = {
            "at": dt_util.utcnow().isoformat(),
            "room": room.room_id,
            "entity": entity_id,
            "hvac_mode": hvac_mode,
            "setpoint_c": temperature,
            "source": "coordinator" if trace is not None else "user",
            "mode": None if trace is None else str(trace.mode),
            "demand": None if trace is None else trace.demand,
            "room_c": None if trace is None else trace.room_c,
            "target_c": (
                None
                if trace is None or trace.target_dry_bulb_c is None
                else round(trace.target_dry_bulb_c, 1)
            ),
        }
        self.command_log.append(entry)
        LOGGER.info(
            "Sent %s to %s: mode %s, setpoint %s (%s; room %s, demand %s, target %s)",
            room.room_id,
            entity_id,
            hvac_mode,
            temperature,
            entry["source"],
            entry["room_c"],
            entry["demand"],
            entry["target_c"],
        )

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
            #
            # DR-056. The one exception is a room inside its band, where the
            # unit is left running: the room loop's setpoint is still walked
            # onto it. Leaving the setpoint where a pull-down put it kept the
            # unit working flat out after the room had arrived, until the
            # room overshot.
            if trace.hold_setpoint and not trace.hold_compressor:
                await self._async_hold_setpoint(room, trace)
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

    async def _async_hold_setpoint(self, room: RoomConfig, trace: DecisionTrace) -> None:
        """Walk the loop's setpoint onto a unit that is running and in band.

        Only the setpoint is sent, never a mode, and only to a unit that is in
        a mode that works against a setpoint. It is sent only when the unit's
        own setpoint differs by at least a step (and at least half a degree),
        so a setpoint that has landed is not sent again. DR-056.
        """
        target = trace.commanded_dry_bulb_c
        if target is None:
            return
        profile = room.capabilities
        step = max((profile.target_temp_step or 0.0) if profile else 0.0, 0.5)
        for entity_id in room.climate_entity_ids:
            state = self.hass.states.get(entity_id)
            live_mode = _hvac_mode_of(state)
            if state is None or live_mode not in (
                HVACMode.COOL,
                HVACMode.HEAT,
                HVACMode.HEAT_COOL,
                HVACMode.AUTO,
            ):
                continue
            try:
                live = float(state.attributes.get(ATTR_TEMPERATURE))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if abs(target - live) < step:
                continue
            try:
                await self._async_set_temperature(
                    entity_id, trace.rejected, state, live_mode, target
                )
            except HomeAssistantError as err:
                LOGGER.warning(
                    "Setting %s to %s for %s failed: %s",
                    entity_id,
                    target,
                    room.room_id,
                    err,
                )
                continue
            self._last_climate[entity_id] = (str(live_mode), target)
            self._record_command(room, entity_id, str(live_mode), target, trace)
            trace.reasons.append(
                f"setpoint walked from {live:.1f} to {target:.1f} C on {entity_id}, "
                "room in band and the unit running"
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

        A room with two heads gets the same instruction on both. It has one
        band, one target and one commanded setpoint — two heads in one room
        are two ways of delivering one temperature, not two zones.
        """
        for entity_id in room.climate_entity_ids:
            await self._async_command_head(
                room, trace, entity_id, preferred, temperature, active=active
            )

    async def _async_command_head(
        self,
        room: RoomConfig,
        trace: DecisionTrace,
        entity_id: str,
        preferred: tuple[HVACMode, ...],
        temperature: float | None,
        *,
        active: bool,
    ) -> None:
        """One head. Dedupe, capability resolution and errors are per head."""
        state = self.hass.states.get(entity_id)
        if state is None:
            trace.rejected.append(f"climate: {entity_id} is unavailable")
            return

        profile = room.capabilities
        hvac_mode = resolve_hvac_mode(state, preferred, profile)
        if hvac_mode is None:
            wanted = ", ".join(str(mode) for mode in preferred)
            trace.rejected.append(
                f"climate: {entity_id} advertises none of {wanted}"
            )
            return

        if hvac_mode is not preferred[0]:
            trace.reasons.append(
                f"{preferred[0]} unavailable, using {hvac_mode} instead"
            )

        # Keyed by entity, not by room. Two heads in one room resolve their
        # own modes and fail independently, so one already holding the
        # instruction must not suppress the other being sent it.
        #
        # 0.8.11. There is no manual-override concept in this design — this
        # is an autonomous coordinator, and the fix for an outcome someone
        # does not want is to change the room's comfort bands, not to expect
        # the hardware to hold a setting made at the wall. The dedupe check
        # exists only to avoid re-sending a command that is already in
        # effect, and "already in effect" has to mean what the entity is
        # actually reporting right now, not what this coordinator remembers
        # having sent. Comparing only against `_last_climate` meant a wall
        # change could sit for one or more cycles before something
        # incidental caused a re-command; the dongle already reports the
        # true state, so there is no reason not to check it directly.
        if self._last_climate.get(
            entity_id
        ) == (hvac_mode, temperature) and _matches_live_state(
            state, hvac_mode, temperature
        ):
            return

        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: hvac_mode},
                blocking=True,
            )
            if temperature is not None:
                await self._async_set_temperature(
                    entity_id, trace.rejected, state, hvac_mode, temperature
                )
            if hvac_mode is not HVACMode.OFF:
                await self._async_set_fan(entity_id, state, active=active, profile=profile)
                # Vane position is the user's to hand over or keep (DR-049).
                # With Automatic vane control off the coordinator never sends
                # one.
                if not self.coordinator.is_room_vanes_manual(room.room_id):
                    await self._async_set_swing(
                        entity_id, state, active=active, profile=profile
                    )
        except HomeAssistantError as err:
            # A failed actuation must not stop the other rooms being evaluated.
            trace.rejected.append(f"climate: {err}")
            LOGGER.warning(
                "Setting %s to %s failed: %s", entity_id, hvac_mode, err
            )
            return

        self._last_climate[entity_id] = (hvac_mode, temperature)
        self._record_command(room, entity_id, str(hvac_mode), temperature, trace)
        trace.reasons.append(
            f"set {entity_id} to {hvac_mode}"
            + (f" at {temperature:.1f} C" if temperature is not None else "")
        )

    async def _async_set_temperature(
        self,
        entity_id: str,
        rejected: list[str],
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
                {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: target},
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
                    ATTR_ENTITY_ID: entity_id,
                    ATTR_TARGET_TEMP_LOW: round(target - RANGE_DEADBAND_C, 1),
                    ATTR_TARGET_TEMP_HIGH: round(target + RANGE_DEADBAND_C, 1),
                },
                blocking=True,
            )
            return

        rejected.append(f"climate: {entity_id} takes no temperature target")

    async def _async_set_fan(
        self,
        entity_id: str,
        state: State,
        *,
        active: bool,
        profile: RoomCapabilities | None,
    ) -> None:
        """Ask for a sensible fan mode, from the units' stored profile."""
        if profile is not None:
            available = list(profile.fan_modes)
        else:
            features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0)
            if not features & ClimateEntityFeature.FAN_MODE:
                return
            available = [
                str(mode) for mode in (state.attributes.get(ATTR_FAN_MODES) or [])
            ]
        wanted = _pick(available, ACTIVE_FAN_MODES if active else QUIET_FAN_MODES)
        if wanted is None or state.attributes.get(ATTR_FAN_MODE) == wanted:
            return
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: entity_id, ATTR_FAN_MODE: wanted},
            blocking=True,
        )

    async def _async_set_swing(
        self,
        entity_id: str,
        state: State,
        *,
        active: bool,
        profile: RoomCapabilities | None,
    ) -> None:
        """Move the vertical vane, from the units' stored profile.

        Mixing the room matters to a comfort index measured at one sensor: a
        stratified room reads comfortable at the sensor and is not. Only the
        vertical vane is ever moved by the coordinator; the horizontal vane
        is the user's alone.
        """
        if profile is not None:
            available = list(profile.swing_modes)
        else:
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
            {ATTR_ENTITY_ID: entity_id, ATTR_SWING_MODE: wanted},
            blocking=True,
        )

    async def async_user_command(
        self,
        room: RoomConfig,
        *,
        hvac_mode: str | None = None,
        temperature: float | None = None,
        fan_mode: str | None = None,
        swing_mode: str | None = None,
        swing_horizontal_mode: str | None = None,
    ) -> None:
        """Carry out one change the user made from the room's controls.

        DR-049. The value must be one the room's stored profile offers, and it
        goes to every head in the room. A value the units do not offer is
        refused here, with a translated reason, rather than sent to fail.
        """
        profile = room.capabilities
        if profile is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="control_profile_not_read",
                translation_placeholders={"room": room.name},
            )

        def check(value: str, offered: tuple[str, ...], control: str) -> None:
            if value not in offered:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="control_value_not_offered",
                    translation_placeholders={
                        "value": value,
                        "control": control,
                        "room": room.name,
                    },
                )

        if temperature is not None and not profile.has_setpoint:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="control_value_not_offered",
                translation_placeholders={
                    "value": str(temperature),
                    "control": "setpoint",
                    "room": room.name,
                },
            )
        if hvac_mode is not None:
            check(hvac_mode, profile.hvac_modes, "mode")
        if fan_mode is not None:
            check(fan_mode, profile.fan_modes, "fan speed")
        if swing_mode is not None:
            check(swing_mode, profile.swing_modes, "vertical vane")
        if swing_horizontal_mode is not None:
            check(
                swing_horizontal_mode, profile.swing_horizontal_modes, "horizontal vane"
            )

        for entity_id in room.climate_entity_ids:
            try:
                if hvac_mode is not None:
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN,
                        SERVICE_SET_HVAC_MODE,
                        {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: hvac_mode},
                        blocking=True,
                    )
                    self._record_command(room, entity_id, hvac_mode, None, None)
                if temperature is not None:
                    state = self.hass.states.get(entity_id)
                    live_mode = _hvac_mode_of(state)
                    if state is None or live_mode is None:
                        raise HomeAssistantError(
                            f"{entity_id} is not reporting a mode, so a setpoint "
                            "cannot be sent to it"
                        )
                    await self._async_set_temperature(
                        entity_id, [], state, live_mode, temperature
                    )
                    self._record_command(
                        room, entity_id, str(live_mode), temperature, None
                    )
                if fan_mode is not None:
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN,
                        SERVICE_SET_FAN_MODE,
                        {ATTR_ENTITY_ID: entity_id, ATTR_FAN_MODE: fan_mode},
                        blocking=True,
                    )
                if swing_mode is not None:
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN,
                        SERVICE_SET_SWING_MODE,
                        {ATTR_ENTITY_ID: entity_id, ATTR_SWING_MODE: swing_mode},
                        blocking=True,
                    )
                if swing_horizontal_mode is not None:
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN,
                        SERVICE_SET_SWING_HORIZONTAL_MODE,
                        {
                            ATTR_ENTITY_ID: entity_id,
                            ATTR_SWING_HORIZONTAL_MODE: swing_horizontal_mode,
                        },
                        blocking=True,
                    )
            except HomeAssistantError as err:
                LOGGER.warning(
                    "Setting %s for %s from its controls failed: %s",
                    entity_id,
                    room.room_id,
                    err,
                )
                raise
        # Whatever the units did, the next evaluation must read the live state
        # rather than trust what was last sent.
        for entity_id in room.climate_entity_ids:
            self._last_climate.pop(entity_id, None)

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
