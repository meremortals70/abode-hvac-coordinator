"""Module 10, Memory: what survives a restart (Module 10).

Memory keeps what each room has learned, and every choice its user has
made, across restarts and upgrades [D031][D064]. No records of readings are
kept: the filter's state is what was learned from them [Prop].

What is kept, and turning it to and from stored data, is pure. So is
seeding: running a room's filters over the recorder's history in time
order, exactly as the live path does. Only the Store, the save timers and
reading the recorder touch Home Assistant, and they import it when they
run, so the pure tests run with nothing installed.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from functools import partial
from typing import TYPE_CHECKING, Any

from .const import CHOICE_SAVE_S, LEARNED_SAVE_S, STORAGE_KEY, STORAGE_VERSION
from .control.arbiter import Arbiter
from .model import RoomModel
from .model.filter import HeatFilter
from .model.humidity import HumidityFilter
from .model.trust import Trust
from .model.unit import DrawLearner, UnitModel
from .models import (
    CapabilityProfile,
    ComfortMode,
    DayScore,
    HouseConfig,
    RawState,
    RoomChoices,
    RoomConfig,
)
from .observe import LoadHistory, RoomObserver

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, State
    from homeassistant.helpers.storage import Store

MINUTE = timedelta(minutes=1)


# --------------------------------------------------------------------------
# What is kept, per room
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RoomState:
    """Everything Memory keeps for one room. NOT the PID's integral [D031]."""

    model: RoomModel = field(default_factory=RoomModel)
    arbiter: Arbiter = field(default_factory=Arbiter)
    choices: RoomChoices = field(default_factory=RoomChoices)
    occupied: bool | None = None  # [D068]
    profile: CapabilityProfile | None = None  # [D048]
    yesterday: DayScore | None = None  # the scorecard, so its sensors show yesterday after a restart


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        out = datetime.fromisoformat(value)
    except ValueError:
        return None
    return out if out.tzinfo is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def choices_to_dict(c: RoomChoices) -> dict[str, Any]:
    """The user's choices [D049][D058][D088][I8]."""
    return {
        "automatic_control": c.automatic_control,
        "automatic_vane_control": c.automatic_vane_control,
        "comfort_mode": c.comfort_mode.value,
        "leave_pressed_at": _iso(c.leave_pressed_at),
        "precool_ready_by": _iso(c.precool_ready_by),
    }


def choices_from_dict(data: Mapping[str, Any]) -> RoomChoices:
    """Restore; a value that cannot be read takes its default."""
    default = RoomChoices()
    auto = data.get("automatic_control")
    vanes = data.get("automatic_vane_control")
    try:
        mode = ComfortMode(str(data.get("comfort_mode")))
    except ValueError:
        mode = default.comfort_mode
    return RoomChoices(
        automatic_control=auto if isinstance(auto, bool) else default.automatic_control,
        automatic_vane_control=vanes if isinstance(vanes, bool) else default.automatic_vane_control,
        comfort_mode=mode,
        leave_pressed_at=_when(data.get("leave_pressed_at")),
        precool_ready_by=_when(data.get("precool_ready_by")),
    )


def profile_to_dict(p: CapabilityProfile) -> dict[str, Any]:
    """The unit's capability profile, as read at setup [D048]."""
    return {
        "hvac_modes": list(p.hvac_modes), "fan_modes": list(p.fan_modes),
        "vertical_vanes": list(p.vertical_vanes), "horizontal_vanes": list(p.horizontal_vanes),
        "min_setpoint": p.min_setpoint, "max_setpoint": p.max_setpoint, "setpoint_step": p.setpoint_step,
        "range_only": p.range_only, "step_reason": p.step_reason, "takes_range": p.takes_range,
    }


def profile_from_dict(data: Mapping[str, Any]) -> CapabilityProfile | None:
    """Restore, or None if it cannot be read: it is then read again from
    the live units (coordinator.py, AT START-UP)."""

    def names(key: str) -> tuple[str, ...] | None:
        v = data.get(key)
        return tuple(str(x) for x in v) if isinstance(v, list) else None

    lists = [names(k) for k in ("hvac_modes", "fan_modes", "vertical_vanes", "horizontal_vanes")]
    lo, hi = _finite(data.get("min_setpoint")), _finite(data.get("max_setpoint"))
    step_raw = data.get("setpoint_step")
    step = _finite(step_raw)
    range_only = data.get("range_only")
    takes_range = data.get("takes_range", True)  # not stored before 0.10.0 r13
    if any(x is None for x in lists) or lo is None or hi is None or lo > hi or not isinstance(range_only, bool) \
            or not isinstance(takes_range, bool):
        return None
    if step_raw is not None and (step is None or step <= 0.0):
        return None
    reason = data.get("step_reason")
    hvac, fans, vert, horiz = (x or () for x in lists)
    return CapabilityProfile(hvac, fans, vert, horiz, lo, hi, step, range_only,
                             str(reason) if reason is not None else None, takes_range)


def score_to_dict(d: DayScore) -> dict[str, Any]:
    return {"comfort_in_band": d.comfort_in_band, "near_centre": d.near_centre,
            "starts_per_hour": d.starts_per_hour, "cost": d.cost}


def score_from_dict(data: Mapping[str, Any]) -> DayScore:
    return DayScore(_finite(data.get("comfort_in_band")), _finite(data.get("near_centre")),
                    _finite(data.get("starts_per_hour")), _finite(data.get("cost")))


def room_to_dict(s: RoomState) -> dict[str, Any]:
    """KEPT, per room (memory.py)."""
    m = s.model
    return {
        "heat": m.heat.as_dict() if m.heat is not None else None,
        "humidity": m.humidity.as_dict() if m.humidity is not None else None,
        "unit": m.unit.as_dict(),
        "trust": m.trust.as_dict(),
        "arbiter": s.arbiter.as_dict(),
        "choices": choices_to_dict(s.choices),
        "occupied": s.occupied,
        "profile": profile_to_dict(s.profile) if s.profile is not None else None,
        "yesterday": score_to_dict(s.yesterday) if s.yesterday is not None else None,
    }


def room_from_dict(data: Mapping[str, Any]) -> RoomState:
    """AT START-UP: restore a room. Any stored number that is not a finite
    number is discarded and relearned [D031]: each part's from_dict refuses
    it, and a part that cannot be read starts afresh."""

    def part(key: str) -> Mapping[str, Any] | None:
        v = data.get(key)
        return v if isinstance(v, Mapping) else None

    heat = HeatFilter.from_dict(h) if (h := part("heat")) is not None else None
    humidity = HumidityFilter.from_dict(h) if (h := part("humidity")) is not None else None
    # a heat filter refused is relearned, and the trust earned by its
    # forecasts goes with it: trust measures that model, not the next [R19]
    refused = part("heat") is not None and heat is None
    model = RoomModel(heat=heat, humidity=humidity,
                      unit=UnitModel.from_dict(part("unit") or {}),
                      trust=Trust() if refused else Trust.from_dict(part("trust") or {}))
    occupied = data.get("occupied")
    return RoomState(
        model=model,
        arbiter=Arbiter.from_dict(part("arbiter") or {}),
        choices=choices_from_dict(part("choices") or {}),
        occupied=occupied if isinstance(occupied, bool) else None,
        profile=profile_from_dict(p) if (p := part("profile")) is not None else None,
        yesterday=score_from_dict(y) if (y := part("yesterday")) is not None else None,
    )


@dataclass(slots=True)
class HouseState:
    """What Memory keeps for the house: each outdoor unit's draw by effort
    [D028]."""

    draw: DrawLearner = field(default_factory=DrawLearner)


def to_data(rooms: Mapping[str, RoomState], house: HouseState) -> dict[str, Any]:
    """The whole stored file's data."""
    return {"rooms": {rid: room_to_dict(s) for rid, s in rooms.items()}, "draw": house.draw.as_dict()}


def from_data(data: Mapping[str, Any] | None, *, has_load_sensor: bool) -> tuple[dict[str, RoomState], HouseState]:
    """The stored file's data, restored. Nothing stored: nothing restored."""
    if not data:
        return {}, HouseState(DrawLearner(has_load_sensor=has_load_sensor))
    rooms_raw = data.get("rooms")
    rooms = (
        {str(rid): room_from_dict(r) for rid, r in rooms_raw.items() if isinstance(r, Mapping)}
        if isinstance(rooms_raw, Mapping) else {}
    )
    draw_raw = data.get("draw")
    try:
        draw = DrawLearner.from_dict(draw_raw if isinstance(draw_raw, Mapping) else {}, has_load_sensor)
    except (TypeError, ValueError):
        draw = DrawLearner(has_load_sensor=has_load_sensor)
    return rooms, HouseState(draw)


def migrate(old_major: int, old_minor: int, data: Mapping[str, Any]) -> dict[str, Any]:
    """IF the file is an older version: convert it; never discard learning
    [Prop]. Version 1 is the first; a newer minor version of 1 is read as it
    stands, its unknown keys ignored."""
    return dict(data)


# --------------------------------------------------------------------------
# SEED: the room's filters over the recorder's history [Prop]
# --------------------------------------------------------------------------


def seed_entities(room: RoomConfig, house: HouseConfig) -> tuple[str, ...]:
    """Every entity whose history a room's filters need, the same ones the
    live path reads for the model: the room's temperature and humidity,
    each head's climate entity with its attributes, and its frequency
    sensor; the outdoor sensors; solar and house load; the room's blinds
    and sun sensor, and the weather entity for cloud cover, which give the
    sun on the glass."""
    ids: list[str | None] = [room.temperature_sensor, room.humidity_sensor, room.sun_sensor, *room.blinds]
    for h in room.heads:
        ids += [h.climate, h.frequency_sensor]
    ids += [house.outdoor_temperature, house.outdoor_humidity, house.outdoor_wind, house.solar_power,
            house.house_load, house.weather]
    return tuple(dict.fromkeys(i for i in ids if i))


@dataclass(frozen=True, slots=True)
class SeedResult:
    """How much history was replayed [D006]."""

    days: float
    corrections: int


_WEATHER_NOW = ("temperature", "humidity", "wind_speed", "cloud_coverage", "uv_index")


def _weather_hour(state: RawState | None, now: datetime) -> list[dict[str, Any]]:
    """The weather entity's own readings for the hour, as a one-hour
    forecast: the recorder keeps no forecast. Without cloud cover the
    filter would not correct on any step with sun on the glass, and in a
    house with no outdoor sensor the forecast's temperature and humidity
    stand in for it, as they do live [D099]."""
    if state is None:
        return []
    start = now.replace(minute=0, second=0, microsecond=0)
    return [{"datetime": start.isoformat(), **{k: state.attributes.get(k) for k in _WEATHER_NOW}}]


def _weather_units(state: RawState | None) -> tuple[str | None, str | None]:
    """The units the weather entity's readings are in."""
    if state is None:
        return None, None
    return state.attributes.get("temperature_unit"), state.attributes.get("wind_speed_unit")


def _written(s: RawState) -> datetime:
    """When a recorded row was written: the later of its last change and
    its last report [D002]."""
    return max(s.last_changed, s.last_reported)


def replay(
    model: RoomModel,
    *,
    room: RoomConfig,
    house: HouseConfig,
    histories: Mapping[str, Sequence[RawState]],
    start: datetime,
    end: datetime,
    tz: tzinfo,
    location: tuple[float, float] | None,
    system_temperature_unit: str,
    stop: Callable[[], bool] = lambda: False,
) -> SeedResult:
    """Run the room's filters over its history, minute by minute in time
    order, exactly as the live path does: each minute's snapshot from the
    states as they then stood, the model's minute, and its 5-minute step.
    The controllers do not act. Each recorded row is applied at the time it
    was written, the later of its last change and its last report, so an
    update that changed only attributes (a unit's hvac_action, a forecast's
    cloud cover) is replayed when it happened."""
    events = sorted((s for h in histories.values() for s in h), key=_written)
    written = [_written(s) for s in events]
    observer = RoomObserver(room)
    states: dict[str, RawState] = {}
    head_units = {h.climate: h.outdoor_unit or f"own:{h.climate}" for h in room.heads}
    before = model.heat.corrections if model.heat is not None else 0
    t = start
    i = 0
    first: datetime | None = None
    while t <= end and not stop():
        while i < len(events) and written[i] <= t:
            states[events[i].entity_id] = events[i]
            i += 1
        if states:
            weather = states.get(house.weather) if house.weather else None
            t_unit, w_unit = _weather_units(weather)
            snap = observer.build(
                house=house, states=states, now=t, choices=RoomChoices(), location=location,
                forecast=_weather_hour(weather, t),
                forecast_temperature_unit=t_unit, forecast_wind_unit=w_unit, tariff=(), load_history=LoadHistory(),
                commands={}, draw_kw={}, head_outdoor_units=head_units,
                system_temperature_unit=system_temperature_unit,
            )
            local = t.astimezone(tz)
            model.minute(snap, local.hour + local.minute / 60.0)
            if first is None and model.heat is not None:
                first = t
            if t.minute % 5 == 0:
                model.five_minutes(t)
        t += MINUTE
    after = model.heat.corrections if model.heat is not None else 0
    days = (min(t, end) - first).total_seconds() / 86400.0 if first is not None else 0.0
    return SeedResult(max(days, 0.0), after - before)


# --------------------------------------------------------------------------
# Home Assistant: the Store, the save timers, the recorder
# --------------------------------------------------------------------------


def _make_store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    from homeassistant.helpers.storage import Store

    class _Store(Store[dict[str, Any]]):
        async def _async_migrate_func(
            self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
        ) -> dict[str, Any]:
            return migrate(old_major_version, old_minor_version, old_data)

    return _Store(hass, STORAGE_VERSION, STORAGE_KEY)


@dataclass(slots=True)
class Memory:
    """The one versioned storage file: .storage/abode_hvac_coordinator [Prop].

    collect returns the data to write, built when the write happens. A
    learned-state save waits at most 5 minutes; a user's choice is saved
    within 5 seconds; a waiting save is never pushed later; everything is
    written when the component unloads [D031][D064]. Home Assistant's own
    final write at shutdown covers a save still waiting then
    (helpers/storage.py, 2026.10.3) [HA].
    """

    hass: HomeAssistant
    collect: Callable[[], dict[str, Any]]
    _store: Store[dict[str, Any]] | None = None
    _waiting: bool = False

    def __post_init__(self) -> None:
        self._store = _make_store(self.hass)

    @property
    def store(self) -> Store[dict[str, Any]]:
        assert self._store is not None
        return self._store

    async def async_load(self) -> dict[str, Any] | None:
        """AT START-UP. A file that cannot be read raises, so setup is
        retried (ConfigEntryNotReady, __init__.py) [R18]."""
        return await self.store.async_load()

    def _data(self) -> dict[str, Any]:
        self._waiting = False
        return self.collect()

    def learned(self) -> None:
        """WHEN the model learns something: IF no save is waiting, of
        either kind, schedule one for 5 minutes from now. Home Assistant
        moves a waiting save to a later time when a later one is asked for
        (helpers/storage.py:500, 2026.10.4), so a learned save is never
        asked for while one is waiting: it would push a choice's 5-second
        save out to 5 minutes."""
        if self._waiting:
            return
        self._waiting = True
        self.store.async_delay_save(self._data, LEARNED_SAVE_S)

    def choice(self) -> None:
        """WHEN the user changes a choice: saved within 5 seconds. An
        earlier time replaces a later one (helpers/storage.py:504), so this
        also brings a waiting learned save forward."""
        self._waiting = True
        self.store.async_delay_save(self._data, CHOICE_SAVE_S)

    async def async_write_now(self) -> None:
        """WHEN the component unloads or reloads: write everything now."""
        await self.store.async_save(self._data())

    async def async_remove(self) -> None:
        """When the integration is removed: its file goes too [Prop]."""
        await self.store.async_remove()


def raw_from_state(state: State) -> RawState:
    """A recorded state, in the shape Observe reads."""
    return RawState(state.entity_id, state.state, dict(state.attributes), state.last_changed, state.last_reported)


async def async_read_history(
    hass: HomeAssistant, entity_ids: Sequence[str], start: datetime, end: datetime
) -> dict[str, list[RawState]]:
    """Every recorded row of each entity, attribute-only updates included,
    with the state each stood in at the start, inside the recorder's
    executor. get_significant_states with significant_changes_only off
    keeps the rows that state_changes_during_period drops, the ones where
    only attributes changed (recorder/history/__init__.py:146 and :430,
    2026.10.4) [HA]."""
    from homeassistant.components.recorder.history import get_significant_states
    from homeassistant.core import State as HaState
    from homeassistant.helpers.recorder import get_instance

    if not entity_ids:
        return {}
    found = await get_instance(hass).async_add_executor_job(
        partial(
            get_significant_states, hass, start, end, list(entity_ids), None,
            include_start_time_state=True, significant_changes_only=False,
            minimal_response=False, no_attributes=False,
        )
    )
    out: dict[str, list[RawState]] = {}
    for entity_id in entity_ids:
        out[entity_id] = [raw_from_state(s) for s in found.get(entity_id.lower(), []) if isinstance(s, HaState)]
    return out


def recorder_keep_days(hass: HomeAssistant, default: int) -> int:
    """How many days the recorder keeps (purge_keep_days, 10 unless the
    user changed it) [HA]."""
    from homeassistant.helpers.recorder import get_instance

    try:
        return int(get_instance(hass).keep_days)
    except (KeyError, AttributeError):
        return default
