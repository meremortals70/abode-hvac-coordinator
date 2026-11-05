"""Module 8, Act: send the allowed step, and record it (Module 8).

Act is the only part that writes to anything. It compares the allowed
step with each unit's live state and sends only what differs, so a change
made at the wall is put back on the next cycle while Automatic control is
on [D007]. It writes to climate entities and covers only [D005], records
every command it sends [D052], and carries the user's own commands from
the room's controls while Automatic control is off [D049].

Deciding what to send is pure and imports nothing from Home Assistant: the
service and attribute names are written out from Home Assistant 2026.10.3
(climate/const.py, cover/const.py, const.py) [HA]. Only the two functions
that send import Home Assistant, when they run.

Every temperature the other modules hand over is in C. A climate entity's
attributes and set_temperature's fields are in the system's temperature
unit, which Home Assistant converts to the entity's own
(climate/services.py, 2026.10.3) [HA], so Act converts at the boundary.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .capabilities import DEGREE_IN_C, FROM_C, NOT_REPORTING_STATES, TO_C
from .const import (
    BLIND_MOVE_POINTS,
    COMMANDS_KEPT,
    CYCLE_S,
    DOMAIN,
    NO_STEP_ADVERTISED,
    RANGE_HALF_WIDTH_C,
)
from .models import (
    CapabilityProfile,
    CommandRecord,
    HeadCommand,
    LimitsResult,
    RawState,
    RoomChoices,
    RoomConfig,
    ServiceCall,
    StepKind,
)

if TYPE_CHECKING:
    from homeassistant.core import Context, HomeAssistant

_LOGGER = logging.getLogger(__package__)

# Home Assistant 2026.10.3 [HA]
CLIMATE = "climate"
COVER = "cover"
SET_HVAC_MODE = "set_hvac_mode"
SET_TEMPERATURE = "set_temperature"
SET_FAN_MODE = "set_fan_mode"
SET_SWING_MODE = "set_swing_mode"
SET_SWING_HORIZONTAL_MODE = "set_swing_horizontal_mode"
SET_COVER_POSITION = "set_cover_position"
ATTR_ENTITY_ID = "entity_id"
ATTR_HVAC_MODE = "hvac_mode"
ATTR_TEMPERATURE = "temperature"
ATTR_TARGET_TEMP_LOW = "target_temp_low"
ATTR_TARGET_TEMP_HIGH = "target_temp_high"
ATTR_FAN_MODE = "fan_mode"
ATTR_SWING_MODE = "swing_mode"
ATTR_SWING_HORIZONTAL_MODE = "swing_horizontal_mode"
ATTR_POSITION = "position"
ATTR_CURRENT_POSITION = "current_position"
MODE_OFF = "off"

_PENDING = timedelta(seconds=CYCLE_S)
_EPS = 1e-6


def to_system(value_c: float, unit: str | None, step_c: float | None, max_c: float | None = None) -> float:
    """A C value in the system's unit [D051].

    It is put on the unit's step in C first, the grid the profile and
    Delivery use. Home Assistant then converts what is sent to the unit's
    own unit with no rounding: F = C x 1.8 + 32 and C = (F - 32) / 1.8
    (util/unit_conversion.py, climate/services.py:132-136, 2026.11.0b0)
    [HA]. So the value sent is the one whose conversion lands on the
    unit's grid. Where no float lands exactly it is the next float above,
    so an integration that truncates does not set a degree lower; except
    at the top of the unit's range (max_c), where Home Assistant refuses a
    value even a float above its maximum (climate/services.py:150), so it
    is the next float below."""
    step = step_c or NO_STEP_ADVERTISED
    on_step = round(value_c / step) * step
    at_top = max_c is not None and on_step + step > max_c + 1e-9
    # a unit that works in C has a step of tenths of a degree C; one that
    # works in F has a step of 5/9 C or a fraction of it (capabilities.py
    # reads the step in the unit's own unit) [D051]
    works_in_c = abs(step * 10.0 - round(step * 10.0)) < 1e-9
    if unit == "°F":
        sent = round(on_step * 1.8 + 32.0, 6)
        if works_in_c:  # Home Assistant converts it to C
            return _land(sent, round(on_step, 6), lambda v: (v - 32.0) / 1.8, at_top)
        return sent
    if unit in (None, "°C"):
        if works_in_c:
            return round(on_step, 6)
        native_f = round(on_step * 1.8 + 32.0, 6)  # Home Assistant converts it to F
        return _land((native_f - 32.0) / 1.8, native_f, lambda v: v * 1.8 + 32.0, at_top)
    return round(FROM_C[unit](on_step), 4)


def _land(sent: float, target: float, convert: Callable[[float], float], at_top: bool) -> float:
    """The float nearest sent whose conversion is the target, or the
    nearest on the side allowed: above, or below at the top of the range."""
    for _ in range(_NUDGES):
        got = convert(sent)
        if got == target or (got > target if not at_top else got < target):
            return sent
        sent = math.nextafter(sent, -math.inf if at_top else math.inf)
    return sent


_NUDGES = 8  # floats tried above the first; one or two always suffice


def _shown_half(shown: Any, unit: str | None, step_c: float | None) -> float:
    """How far a shown setpoint may be from the unit's own, in C, where
    Home Assistant shows it in whole degrees of another unit, coarser than
    the unit's step: half a shown degree. Otherwise no limit beyond the step
    itself. A unit working in C has a step of tenths of a degree C (see
    to_system); in a F house it is shown in whole degrees F by default, and
    a F unit in a C house in whole degrees C (climate/entity.py precision,
    2026.11.0b0) [HA]."""
    step = step_c or NO_STEP_ADVERTISED
    works_in_c = abs(step * 10.0 - round(step * 10.0)) < 1e-9
    other_unit = (unit == "°F" and works_in_c) or (unit in (None, "°C") and not works_in_c)
    degree_c = DEGREE_IN_C.get(unit, 1.0)
    whole = isinstance(shown, int | float) and not isinstance(shown, bool) and float(shown).is_integer()
    return degree_c / 2.0 + _EPS if other_unit and whole and degree_c > step + _EPS else math.inf


def _live_c(value: Any, unit: str | None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return TO_C[unit](float(value))


@dataclass(frozen=True, slots=True)
class CommandContext:
    """What a command is logged with [D052]."""

    rate: float | None
    room_temperature: float | None
    centre_c: float | None


@dataclass(slots=True)
class RoomAct:
    """One room's Act. It remembers what it sent in the last cycle, so a
    command is not sent again while the unit has yet to report it."""

    room: RoomConfig
    _sent: dict[tuple[str, str], tuple[Any, datetime]] = field(default_factory=dict)

    def calls(
        self,
        *,
        now: datetime,
        step: LimitsResult,
        live: Mapping[str, RawState],
        system_unit: str | None,
        choices: RoomChoices,
    ) -> list[ServiceCall]:
        """EVERY CYCLE, per room: what to send for the allowed step.

        NONE sends nothing [D017]. OFF sets each head that is not off to
        off. Otherwise each head's mode, setpoint, fan speed and, under
        Automatic vane control, vertical vane are set where they differ
        from the head's live state; the horizontal vane is never moved
        [D049]. Anything already as wanted live, or sent in the last cycle
        and not yet reported, is skipped [D007].
        """
        if step.kind is StepKind.NONE:
            # not run by the component (Automatic control off, a reading
            # missing): the units may be changed by hand meanwhile, so what was
            # last sent no longer says what they hold [D007]
            self._sent.clear()
            return []
        out: list[ServiceCall] = []
        profile = self.room.profile
        for head in self.room.heads:
            state = live.get(head.climate)
            if state is None or state.state in NOT_REPORTING_STATES:
                continue
            if step.kind is StepKind.OFF:
                if state.state != MODE_OFF:
                    self._add(out, now, CLIMATE, SET_HVAC_MODE, head.climate, ATTR_HVAC_MODE, MODE_OFF)
                continue
            wanted = next((c for c in step.heads if c.climate == head.climate), None)
            if wanted is None:
                continue
            out.extend(self._head(now, wanted, state, system_unit, profile, choices))
        for blind in step.blinds:
            state = live.get(blind.cover)
            if state is None or state.state in NOT_REPORTING_STATES:
                continue
            current = _live_c(state.attributes.get(ATTR_CURRENT_POSITION), None)
            if current is None or abs(current - blind.position) > BLIND_MOVE_POINTS:  # [Set]
                self._add(out, now, COVER, SET_COVER_POSITION, blind.cover, ATTR_POSITION,
                          round(blind.position))
        return out

    def _head(
        self,
        now: datetime,
        wanted: HeadCommand,
        state: RawState,
        unit: str | None,
        profile: CapabilityProfile | None,
        choices: RoomChoices,
    ) -> list[ServiceCall]:
        out: list[ServiceCall] = []
        e = wanted.climate
        attrs = state.attributes
        if wanted.mode is not None and state.state != wanted.mode:
            self._add(out, now, CLIMATE, SET_HVAC_MODE, e, ATTR_HVAC_MODE, wanted.mode)
        step_c = profile.setpoint_step if profile is not None else None
        top = profile.max_setpoint if profile is not None else None
        tolerance = (step_c or NO_STEP_ADVERTISED) - _EPS
        if wanted.setpoint is not None:
            live_sp = _live_c(attrs.get(ATTR_TEMPERATURE), unit)
            value = to_system(wanted.setpoint, unit, step_c, top)
            # Home Assistant may show the setpoint coarser than the unit holds
            # it: a C unit in a F house shows whole degrees F (climate/entity.py
            # precision, 2026.11.0b0) [HA]. Then a shown value is as wanted only
            # within half a shown degree; and a value other than the one last
            # sent is always sent [D007]
            last = self._sent.get((e, ATTR_TEMPERATURE))
            half = _shown_half(attrs.get(ATTR_TEMPERATURE), unit, step_c)
            if (
                live_sp is None
                or abs(live_sp - wanted.setpoint) >= tolerance
                or abs(live_sp - wanted.setpoint) > half
                or (last is not None and last[0] != value)
                # shown too coarsely to tell, and nothing sent to go by: sent once
                or (last is None and math.isfinite(half))
            ):
                self._add(out, now, CLIMATE, SET_TEMPERATURE, e, ATTR_TEMPERATURE, value)
        elif wanted.setpoint_low is not None and wanted.setpoint_high is not None:
            lo = _live_c(attrs.get(ATTR_TARGET_TEMP_LOW), unit)
            hi = _live_c(attrs.get(ATTR_TARGET_TEMP_HIGH), unit)
            pair = (to_system(wanted.setpoint_low, unit, step_c, top), to_system(wanted.setpoint_high, unit, step_c, top))
            last = self._sent.get((e, "range"))
            if lo is None or hi is None or abs(lo - wanted.setpoint_low) >= tolerance or abs(
                hi - wanted.setpoint_high
            ) >= tolerance or (last is not None and last[0] != pair):
                self._add(out, now, CLIMATE, SET_TEMPERATURE, e, "range", pair)
        if wanted.fan_mode is not None and attrs.get(ATTR_FAN_MODE) != wanted.fan_mode:
            self._add(out, now, CLIMATE, SET_FAN_MODE, e, ATTR_FAN_MODE, wanted.fan_mode)
        if (
            choices.automatic_vane_control
            and wanted.vertical_vane is not None
            and attrs.get(ATTR_SWING_MODE) != wanted.vertical_vane
        ):
            self._add(out, now, CLIMATE, SET_SWING_MODE, e, ATTR_SWING_MODE, wanted.vertical_vane)  # [D049]
        return out

    def _add(self, out: list[ServiceCall], now: datetime, domain: str, service: str, entity: str, key: str,
             value: Any) -> None:
        """Add a call unless the same value was sent within the last cycle
        and the unit has not reported it yet [D007]."""
        last = self._sent.get((entity, key))
        if last is not None and last[0] == value and now - last[1] < _PENDING:
            return
        self._sent[(entity, key)] = (value, now)
        if key == "range":
            low, high = value
            data: dict[str, Any] = {ATTR_ENTITY_ID: entity, ATTR_TARGET_TEMP_LOW: low, ATTR_TARGET_TEMP_HIGH: high}
        else:
            data = {ATTR_ENTITY_ID: entity, key: value}
        out.append(ServiceCall(domain, service, data))


# --------------------------------------------------------------------------
# The user's own commands from the room's controls [D049]
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UserCommand:
    """One change the user made from a room control. A temperature is C."""

    hvac_mode: str | None = None
    setpoint: float | None = None
    fan_mode: str | None = None
    vertical_vane: str | None = None
    horizontal_vane: str | None = None


def user_calls(room: RoomConfig, choices: RoomChoices, cmd: UserCommand, system_unit: str | None) -> list[ServiceCall]:
    """WHEN the user changes a room control: refused while Automatic
    control is on (for a vane, Automatic vane control), or if the value is
    not one the stored profile offers; otherwise sent to every head
    [D049][D048].

    Raises RefusedCommand with a translation key; the entity turns it into
    a ServiceValidationError (Section 8, entity.py).
    """
    vane = cmd.vertical_vane is not None or cmd.horizontal_vane is not None
    if vane and choices.automatic_vane_control:
        raise RefusedCommand("automatic_vane_control_on", {"room": room.name})
    if not vane and choices.automatic_control:
        raise RefusedCommand("automatic_control_on", {"room": room.name})
    profile = room.profile
    if profile is None:
        raise RefusedCommand("profile_not_read", {"room": room.name})
    checks = (
        (cmd.hvac_mode, profile.hvac_modes, "mode"),
        (cmd.fan_mode, profile.fan_modes, "fan speed"),
        (cmd.vertical_vane, profile.vertical_vanes, "vertical vane"),
        (cmd.horizontal_vane, profile.horizontal_vanes, "horizontal vane"),
    )
    for value, offered, control in checks:
        if value is not None and value not in offered:
            raise RefusedCommand("not_offered", {"value": value, "control": control, "room": room.name})
    if cmd.setpoint is not None and not profile.min_setpoint - _EPS <= cmd.setpoint <= profile.max_setpoint + _EPS:
        raise RefusedCommand("not_offered", {"value": f"{cmd.setpoint:g}", "control": "setpoint", "room": room.name})
    out: list[ServiceCall] = []
    top = profile.max_setpoint
    for head in room.heads:
        e = {ATTR_ENTITY_ID: head.climate}
        if cmd.hvac_mode is not None:
            out.append(ServiceCall(CLIMATE, SET_HVAC_MODE, {**e, ATTR_HVAC_MODE: cmd.hvac_mode}))
        if cmd.setpoint is not None:
            step = profile.setpoint_step
            if profile.range_only:  # [D024]
                out.append(ServiceCall(CLIMATE, SET_TEMPERATURE, {
                    **e,
                    ATTR_TARGET_TEMP_LOW: to_system(cmd.setpoint - RANGE_HALF_WIDTH_C, system_unit, step, top),
                    ATTR_TARGET_TEMP_HIGH: to_system(cmd.setpoint + RANGE_HALF_WIDTH_C, system_unit, step, top),
                }))
            else:
                out.append(ServiceCall(CLIMATE, SET_TEMPERATURE,
                                       {**e, ATTR_TEMPERATURE: to_system(cmd.setpoint, system_unit, step, top)}))
        if cmd.fan_mode is not None:
            out.append(ServiceCall(CLIMATE, SET_FAN_MODE, {**e, ATTR_FAN_MODE: cmd.fan_mode}))
        if cmd.vertical_vane is not None:
            out.append(ServiceCall(CLIMATE, SET_SWING_MODE, {**e, ATTR_SWING_MODE: cmd.vertical_vane}))
        if cmd.horizontal_vane is not None:
            out.append(ServiceCall(CLIMATE, SET_SWING_HORIZONTAL_MODE,
                                   {**e, ATTR_SWING_HORIZONTAL_MODE: cmd.horizontal_vane}))
    return out


class RefusedCommand(Exception):
    """A user command refused, with the translation key and placeholders
    for the message (strings.json, exceptions)."""

    def __init__(self, key: str, placeholders: Mapping[str, str]) -> None:
        super().__init__(key)
        self.key = key
        self.placeholders = dict(placeholders)


# --------------------------------------------------------------------------
# The record of commands, and sending
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CommandLog:
    """The last 200 commands sent, for diagnostics [D052]."""

    records: deque[CommandRecord] = field(default_factory=lambda: deque(maxlen=COMMANDS_KEPT))

    def add(self, record: CommandRecord) -> None:
        self.records.append(record)
        values = {k: v for k, v in record.data.items() if k != ATTR_ENTITY_ID}
        what = f"{record.service} {values}"
        if record.ok:
            _LOGGER.info(
                "%s %s: %s%s; rate needed %s C/h, room %s C, centre %s C",
                record.room_id, record.entity_id, what, " (by the user)" if record.by_user else "",
                _fmt(record.rate), _fmt(record.room_temperature), _fmt(record.centre_c),
            )
        else:
            _LOGGER.warning("%s %s: %s failed: %s", record.room_id, record.entity_id, what, record.error)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


async def async_send(
    hass: HomeAssistant,
    calls: Sequence[ServiceCall],
    *,
    room_id: str,
    log: CommandLog,
    now: datetime,
    about: CommandContext,
    by_user: bool = False,
    context: Context | None = None,
) -> list[CommandRecord]:
    """Send each call and record it. A call that fails is logged; the
    room's other commands still go, and nothing is retried until the next
    cycle [Prop]. Returns the records, for Model and Evidence [D081]."""
    from homeassistant.exceptions import HomeAssistantError

    out: list[CommandRecord] = []
    for call in calls:
        ok, error = True, None
        try:
            await hass.services.async_call(call.domain, call.service, dict(call.data), blocking=True, context=context)
        except HomeAssistantError as err:
            ok, error = False, str(err) or type(err).__name__
        record = CommandRecord(now, room_id, call.entity_id, call.service, call.data, about.rate,
                               about.room_temperature, about.centre_c, by_user, ok, error)
        log.add(record)
        out.append(record)
    return out


async def async_user_command(
    hass: HomeAssistant,
    room: RoomConfig,
    choices: RoomChoices,
    cmd: UserCommand,
    *,
    log: CommandLog,
    now: datetime,
    context: Context | None = None,
) -> list[CommandRecord]:
    """Carry out a user's command, or refuse it with a translated reason
    (ServiceValidationError); a command the unit fails is raised as a
    translated HomeAssistantError [R18]."""
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

    try:
        calls = user_calls(room, choices, cmd, hass.config.units.temperature_unit)
    except RefusedCommand as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key=err.key, translation_placeholders=err.placeholders
        ) from err
    records = await async_send(hass, calls, room_id=room.room_id, log=log, now=now,
                               about=CommandContext(None, None, None), by_user=True, context=context)
    failed = [r for r in records if not r.ok]
    if failed:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"entity": failed[0].entity_id, "error": failed[0].error or ""},
        )
    return records
