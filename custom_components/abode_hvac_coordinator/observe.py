"""Module 1, Observe: one snapshot of one room (Section 6, observe.py).

Observe reads nothing itself. The coordinator hands it raw states, so every
decision in a cycle sees the same moment [D002]. Each value in the snapshot
carries a tag saying how far it can be trusted, so the model learns only
from what was measured [I1].

Readings are converted to the units the modules work in: degrees Celsius,
kilowatts, metres per second, percent. A reading in a unit this file does
not know is treated as missing rather than guessed at.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .capabilities import TO_C
from .const import (
    CLEAR_SKY_CLOUD_LIMIT,
    CLEAR_SKY_FLOOR,
    CLEAR_SKY_UV_DIVISOR,
    LOAD_STEP_SHARE_OF_DRAW,
    LOAD_STEP_WINDOW_S,
    OUTDOOR_TOO_OLD_S,
    POWER_TOO_OLD_S,
    WINDOW_FACINGS,
)
from .hci import blind_transmission
from .models import (
    CompressorSource,
    CompressorState,
    Flag,
    ForecastHour,
    HeadSnapshot,
    HouseConfig,
    RawState,
    Reading,
    RoomChoices,
    RoomConfig,
    Snapshot,
    SunSnapshot,
    TariffInterval,
    ValueTag,
)
from .sun import SunOnWindow, sun_on_window, sun_position

MISSING_STATES = frozenset({"unavailable", "unknown"})
ON_STATES = frozenset({"on"})
OFF_STATES = frozenset({"off"})

# hvac_action values (climate/const.py, 2026.10.3) [D025]
_ACTION_RUNNING = frozenset({"cooling", "heating", "drying"})
_ACTION_NOT_RUNNING = frozenset({"idle", "fan", "off"})
# modes in which the compressor cannot run (decision of 27 October 2026)
_MODES_NOT_RUNNING = frozenset({"off", "fan_only"})

# Unit conversions to the working unit; units are those of
# homeassistant/const.py (2026.10.3). None means no unit was given, which
# is taken as the working unit.
_TEMPERATURE: dict[str | None, Callable[[float], float]] = TO_C
_POWER_KW: dict[str | None, Callable[[float], float]] = {
    None: lambda v: v,
    "kW": lambda v: v,
    "W": lambda v: v / 1000.0,
    "mW": lambda v: v / 1e6,
    "MW": lambda v: v * 1000.0,
}
_SPEED_MS: dict[str | None, Callable[[float], float]] = {
    None: lambda v: v,
    "m/s": lambda v: v,
    "km/h": lambda v: v / 3.6,
    "kn": lambda v: v * 1852.0 / 3600.0,
    "mph": lambda v: v * 0.44704,
    "ft/s": lambda v: v * 0.3048,
    "in/s": lambda v: v * 0.0254,
    "mm/s": lambda v: v / 1000.0,
    # WMO Beaufort scale: v = 0.836 B^1.5 m/s
    "Beaufort": lambda v: 0.836 * max(v, 0.0) ** 1.5,
}
_IDENTITY: dict[str | None, Callable[[float], float]] = {None: lambda v: v, "%": lambda v: v, "Hz": lambda v: v}


def _age_s(raw: RawState, now: datetime) -> float:
    """Age from the later of the last change and the last report [D066]."""
    return (now - max(raw.last_changed, raw.last_reported)).total_seconds()


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class _Reader:
    """Reads one cycle's raw states and keeps the stale list."""

    def __init__(self, states: Mapping[str, RawState], now: datetime, system_temperature_unit: str) -> None:
        self.states = states
        self.now = now
        self.system_temperature_unit = system_temperature_unit
        self.stale: list[str] = []

    def raw(self, entity_id: str | None) -> RawState | None:
        return self.states.get(entity_id) if entity_id else None

    def number(
        self,
        entity_id: str | None,
        units: Mapping[str | None, Callable[[float], float]],
        age_limit_s: float | None,
    ) -> Reading:
        """A numeric sensor: MISSING if unavailable, unknown, not a number,
        in a unit not known, or older than its limit (then also stale)."""
        if entity_id is None:
            return Reading(None, ValueTag.MISSING)
        raw = self.raw(entity_id)
        if raw is None or raw.state in MISSING_STATES:
            return Reading(None, ValueTag.MISSING, entity_id)
        age = _age_s(raw, self.now)
        if age_limit_s is not None and age > age_limit_s:
            self.stale.append(entity_id)
            return Reading(None, ValueTag.MISSING, entity_id, age)
        value = _to_float(raw.state)
        unit = raw.attributes.get("unit_of_measurement")
        convert = units.get(unit) if unit is None or isinstance(unit, str) else None
        if value is None or convert is None:
            return Reading(None, ValueTag.MISSING, entity_id, age)
        return Reading(convert(value), ValueTag.MEASURED, entity_id, age)

    def flag(self, entity_id: str | None) -> Flag | None:
        """An on/off entity; None when none is configured. No age limit
        [D087]; only unavailable or unknown is missing [D070]."""
        if entity_id is None:
            return None
        raw = self.raw(entity_id)
        if raw is None or raw.state in MISSING_STATES:
            return Flag(None, ValueTag.MISSING, entity_id)
        if raw.state in ON_STATES:
            return Flag(True, ValueTag.MEASURED, entity_id, raw.last_changed)
        if raw.state in OFF_STATES:
            return Flag(False, ValueTag.MEASURED, entity_id, raw.last_changed)
        return Flag(None, ValueTag.MISSING, entity_id)

    def system_temperature(self, value: Any) -> float | None:
        """A climate attribute temperature, in the system's unit, as C."""
        v = _to_float(value)
        convert = _TEMPERATURE.get(self.system_temperature_unit)
        return None if v is None or convert is None else convert(v)


@dataclass(slots=True)
class LoadHistory:
    """The house load over the last few minutes, for the house-load step
    test of Section 4.8. One per house; the coordinator adds a sample each
    time it reads the house load."""

    keep_s: float = 10 * 60.0
    samples: deque[tuple[datetime, float]] = field(default_factory=deque)

    def add(self, when: datetime, kw: float | None) -> None:
        """Record a house-load reading in kW (None is not recorded)."""
        if kw is None:
            return
        self.samples.append((when, kw))
        while self.samples and (when - self.samples[0][0]).total_seconds() > self.keep_s:
            self.samples.popleft()

    def at_or_before(self, when: datetime) -> float | None:
        """The latest reading at or before a moment."""
        out: float | None = None
        for t, kw in self.samples:
            if t > when:
                break
            out = kw
        return out


@dataclass(slots=True)
class _LoadStepMemory:
    state: CompressorState = CompressorState.UNKNOWN
    evaluated_command: datetime | None = None


def _outdoor_unit(climate: str, name: str | None) -> str:
    """A head with no outdoor-unit name is its own outdoor unit [D020]."""
    return name if name else f"own:{climate}"


@dataclass(slots=True)
class RoomObserver:
    """Builds a room's snapshot each cycle, and remembers each head's last
    house-load inference between cycles (Section 4.8)."""

    room: RoomConfig
    _load_steps: dict[str, _LoadStepMemory] = field(default_factory=dict)

    def build(
        self,
        *,
        house: HouseConfig,
        states: Mapping[str, RawState],
        now: datetime,
        choices: RoomChoices,
        location: tuple[float, float] | None,
        forecast: Sequence[Mapping[str, Any]],
        forecast_temperature_unit: str | None,
        forecast_wind_unit: str | None,
        tariff: Sequence[TariffInterval],
        load_history: LoadHistory,
        commands: Mapping[str, datetime],
        draw_kw: Mapping[str, float | None],
        head_outdoor_units: Mapping[str, str],
        system_temperature_unit: str = "°C",
    ) -> Snapshot:
        """BUILD SNAPSHOT for this room from the raw states handed over.

        commands: the time of the last command sent to each climate entity
        in the house (Act). draw_kw: each outdoor unit's learned draw at
        its running effort (Model), None while not yet learned.
        head_outdoor_units: every head in the house and its outdoor unit,
        so "no other unit changing" can be judged.
        """
        r = _Reader(states, now, system_temperature_unit)
        room = self.room

        # ROOM TEMPERATURE and HUMIDITY: no age limit [D070]
        room_t = r.number(room.temperature_sensor, _TEMPERATURE, None)
        room_rh = r.number(room.humidity_sensor, _IDENTITY, None)

        heads = tuple(
            self._head(r, h.climate, _outdoor_unit(h.climate, h.outdoor_unit), h.frequency_sensor, load_history,
                       commands, draw_kw, head_outdoor_units)
            for h in room.heads
        )
        room_t = _stand_in(room_t, [h.own_temperature for h in heads])
        room_rh = _stand_in(room_rh, [h.own_humidity for h in heads])

        # OUTDOOR: too old after 3 hours [D030]
        out_t = r.number(house.outdoor_temperature, _TEMPERATURE, OUTDOOR_TOO_OLD_S)
        out_rh = r.number(house.outdoor_humidity, _IDENTITY, OUTDOOR_TOO_OLD_S)
        out_w = r.number(house.outdoor_wind, _SPEED_MS, OUTDOOR_TOO_OLD_S)
        hours = _forecast_hours(forecast, forecast_temperature_unit, forecast_wind_unit, out_t.value, out_rh.value,
                                out_w.value)
        # no outdoor sensor, or one gone quiet: the forecast's hour stands in, so the room still learns
        out_t = _forecast_stands_in(out_t, hours, now, lambda h: h.temperature)
        out_rh = _forecast_stands_in(out_rh, hours, now, lambda h: h.humidity)

        # HOUSE: power readings too old after 15 minutes [D030]
        load = r.number(house.house_load, _POWER_KW, POWER_TOO_OLD_S)
        solar = r.number(house.solar_power, _POWER_KW, POWER_TOO_OLD_S)
        charge = r.number(house.battery_charge, _IDENTITY, POWER_TOO_OLD_S)
        grid = r.number(house.grid_power, _POWER_KW, POWER_TOO_OLD_S)

        blinds = tuple(_blind_position(r, b) for b in room.blinds)
        sun = _sun(r, room, location, now, hours, blinds)

        tariff_now = next((i for i in tariff if i.start <= now < i.end), None)
        tariff_ahead = tuple(sorted((i for i in tariff if i.start > now), key=lambda i: i.start))

        return Snapshot(
            room_id=room.room_id,
            time=now,
            room_temperature=room_t,
            room_humidity=room_rh,
            heads=heads,
            sun=sun,
            outdoor_temperature=out_t,
            outdoor_humidity=out_rh,
            outdoor_wind_ms=out_w,
            forecast=hours,
            presence=r.flag(room.presence_sensor),
            leave_pressed_at=choices.leave_pressed_at,
            precool_ready_by=choices.precool_ready_by,
            openings=tuple(f for f in (r.flag(o) for o in room.openings) if f is not None),
            blind_positions=blinds,
            sleep_schedule=r.flag(room.sleep_schedule),
            house_load_kw=load,
            solar_kw=solar,
            battery_charge_pct=charge,
            grid_kw=grid,
            tariff_now=tariff_now,
            tariff_ahead=tariff_ahead,
            stale=tuple(r.stale),
        )

    # ------------------------------------------------------------------
    def _head(
        self,
        r: _Reader,
        climate: str,
        outdoor_unit: str,
        frequency_sensor: str | None,
        load_history: LoadHistory,
        commands: Mapping[str, datetime],
        draw_kw: Mapping[str, float | None],
        head_outdoor_units: Mapping[str, str],
    ) -> HeadSnapshot:
        raw = r.raw(climate)
        frequency = (
            r.number(frequency_sensor, _IDENTITY, None) if frequency_sensor else Reading(None, ValueTag.MISSING)
        )
        if raw is None or raw.state in MISSING_STATES:
            return HeadSnapshot(climate, outdoor_unit, False, None, None, None, None, None, None, None, None,
                                frequency, CompressorState.UNKNOWN, CompressorSource.UNKNOWN)
        attrs = raw.attributes
        setpoint = r.system_temperature(attrs.get("temperature"))
        if setpoint is None:
            lo = r.system_temperature(attrs.get("target_temp_low"))
            hi = r.system_temperature(attrs.get("target_temp_high"))
            # a unit that holds a range: the middle [D081]
            setpoint = (lo + hi) / 2.0 if lo is not None and hi is not None else None
        action_raw = attrs.get("hvac_action")
        action = str(action_raw) if action_raw is not None else None
        state, source = self._compressor(climate, outdoor_unit, frequency, action, raw.state, load_history,
                                         commands, draw_kw, head_outdoor_units, r.now)
        return HeadSnapshot(
            climate=climate,
            outdoor_unit=outdoor_unit,
            available=True,
            mode=raw.state,
            setpoint=setpoint,
            hvac_action=action,
            own_temperature=r.system_temperature(attrs.get("current_temperature")),
            own_humidity=_to_float(attrs.get("current_humidity")),
            fan_mode=_str_or_none(attrs.get("fan_mode")),
            vertical_vane=_str_or_none(attrs.get("swing_mode")),
            horizontal_vane=_str_or_none(attrs.get("swing_horizontal_mode")),
            frequency=frequency,
            compressor=state,
            compressor_source=source,
        )

    def _compressor(
        self,
        climate: str,
        outdoor_unit: str,
        frequency: Reading,
        action: str | None,
        mode: str,
        load_history: LoadHistory,
        commands: Mapping[str, datetime],
        draw_kw: Mapping[str, float | None],
        head_outdoor_units: Mapping[str, str],
        now: datetime,
    ) -> tuple[CompressorState, CompressorSource]:
        """COMPRESSOR STATE; the first answer that exists wins (4.8)."""
        # a head that is off or in fan-only is not running a compressor,
        # whatever a frequency sensor says: one sensor is often chosen for
        # every head on an outdoor unit, and reads another head's compressor
        # (decision of 27 October 2026; never "running" from the mode [D025])
        if mode in _MODES_NOT_RUNNING:
            # the next mode's state is not carried from this one: only a
            # measured rise after the next command counts as a start
            self._load_steps.setdefault(climate, _LoadStepMemory()).state = CompressorState.UNKNOWN
            return CompressorState.STOPPED, CompressorSource.MODE
        # 1. frequency sensor MEASURED: running if above 0 Hz [Prop]
        if frequency.tag is ValueTag.MEASURED and frequency.value is not None:
            running = frequency.value > 0.0
            return (CompressorState.RUNNING if running else CompressorState.STOPPED), CompressorSource.FREQUENCY
        # 2. hvac_action reported [D025]
        if action in _ACTION_RUNNING:
            return CompressorState.RUNNING, CompressorSource.HVAC_ACTION
        if action in _ACTION_NOT_RUNNING:
            return CompressorState.STOPPED, CompressorSource.HVAC_ACTION
        memory = self._load_steps.setdefault(climate, _LoadStepMemory())
        # 3. house-load step [Prop]
        self._evaluate_load_step(memory, climate, outdoor_unit, load_history, commands, draw_kw, head_outdoor_units,
                                 now)
        if memory.state is not CompressorState.UNKNOWN:
            return memory.state, CompressorSource.LOAD_STEP
        # 4. otherwise UNKNOWN [Prop]
        return CompressorState.UNKNOWN, CompressorSource.UNKNOWN

    @staticmethod
    def _evaluate_load_step(
        memory: _LoadStepMemory,
        climate: str,
        outdoor_unit: str,
        load_history: LoadHistory,
        commands: Mapping[str, datetime],
        draw_kw: Mapping[str, float | None],
        head_outdoor_units: Mapping[str, str],
        now: datetime,
    ) -> None:
        """Within a minute of a command to this head, with no other unit
        changing: a rise of at least half the outdoor unit's learned draw
        is a start, the matching fall a stop [Prop]. Judged once the
        minute has passed; the answer stands until the next one."""
        commanded = commands.get(climate)
        if commanded is None or commanded == memory.evaluated_command:
            return
        window = timedelta(seconds=LOAD_STEP_WINDOW_S)
        if now - commanded < window:
            return
        # judged once a load reading from the end of the minute is in: this
        # cycle's reading is added after Observe runs, so the newest one
        # held can be from 30 seconds before now [Prop]
        if not load_history.samples or load_history.samples[-1][0] < commanded + window:
            return
        memory.evaluated_command = commanded
        draw = draw_kw.get(outdoor_unit)
        if draw is None or draw <= 0.0:
            return
        for other, when in commands.items():
            if other == climate or head_outdoor_units.get(other, f"own:{other}") == outdoor_unit:
                continue
            if abs((when - commanded).total_seconds()) <= LOAD_STEP_WINDOW_S:
                return  # another unit changed within the minute
        before = load_history.at_or_before(commanded - timedelta(microseconds=1))
        after = load_history.at_or_before(commanded + window)
        if before is None or after is None:
            return
        step = after - before
        if step >= LOAD_STEP_SHARE_OF_DRAW * draw:
            memory.state = CompressorState.RUNNING
        elif step <= -LOAD_STEP_SHARE_OF_DRAW * draw:
            memory.state = CompressorState.STOPPED


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _stand_in(reading: Reading, own_values: Sequence[float | None]) -> Reading:
    """The unit's own reading stands in for a missing room reading, tagged
    so the model never learns from it [D074]; otherwise MISSING [D018]."""
    if reading.tag is ValueTag.MEASURED:
        return reading
    for v in own_values:
        if v is not None:
            return Reading(v, ValueTag.STOOD_IN, reading.entity_id, reading.age_s)
    return Reading(None, ValueTag.MISSING, reading.entity_id, reading.age_s)


def _forecast_stands_in(
    reading: Reading, hours: Sequence[ForecastHour], now: datetime, value: Callable[[ForecastHour], float | None]
) -> Reading:
    """The forecast's value for the hour now stands in for an outdoor
    reading that is missing or too old, tagged ESTIMATED; the model learns
    from it as from the sensor: the outdoor reading is one of what drives
    the room, never what the filter corrects on [D099]. Otherwise the
    reading as it is."""
    if reading.value is not None:
        return reading
    for h in hours:
        if h.start <= now < h.start + timedelta(hours=1):
            v = value(h)
            if v is not None:
                return Reading(v, ValueTag.ESTIMATED, reading.entity_id, reading.age_s)
    return reading


def _blind_position(r: _Reader, entity_id: str) -> Reading:
    """A blind's open fraction, 0 to 1, from its current_position; MISSING
    when its position cannot be read [D022]."""
    raw = r.raw(entity_id)
    if raw is None or raw.state in MISSING_STATES:
        return Reading(None, ValueTag.MISSING, entity_id)
    pos = _to_float(raw.attributes.get("current_position"))
    if pos is None:
        return Reading(None, ValueTag.MISSING, entity_id)
    return Reading(min(max(pos, 0.0), 100.0) / 100.0, ValueTag.MEASURED, entity_id)


def clear_sky_fraction(cloud_coverage: float | None, uv_index: float | None) -> float | None:
    """f_clear from the forecast for this hour [Set]; None when neither is
    known. Cloud c: 0.2 + 0.8 (1 - c/90) below 90 %, 0.2 at or above;
    otherwise UV index / 8, no more than 1."""
    if cloud_coverage is not None:
        if cloud_coverage >= CLEAR_SKY_CLOUD_LIMIT:
            return CLEAR_SKY_FLOOR
        return CLEAR_SKY_FLOOR + (1.0 - CLEAR_SKY_FLOOR) * (1.0 - cloud_coverage / CLEAR_SKY_CLOUD_LIMIT)
    if uv_index is not None:
        return min(max(uv_index, 0.0) / CLEAR_SKY_UV_DIVISOR, 1.0)
    return None


def _hour_for(hours: Sequence[ForecastHour], now: datetime) -> ForecastHour | None:
    for h in hours:
        if h.start <= now < h.start + timedelta(hours=1):
            return h
    return None


def _sun(
    r: _Reader,
    room: RoomConfig,
    location: tuple[float, float] | None,
    now: datetime,
    hours: Sequence[ForecastHour],
    blinds: Sequence[Reading],
) -> SunSnapshot:
    """SUN ON THE GLASS, 0 to 1 [D023]."""
    transmission = (
        sum(blind_transmission(b.value) for b in blinds) / len(blinds) if blinds else 1.0
    )
    on_glass: bool | None = None
    source = "geometry"
    sensor = r.flag(room.sun_sensor)
    if sensor is not None and sensor.value is not None:
        on_glass, source = sensor.value, "sensor"  # [Set]
    else:
        facing = WINDOW_FACINGS.get(room.window_facing) if room.window_facing else None
        position = sun_position(location[0], location[1], now) if location is not None else None
        answer = sun_on_window(position, facing, room.overhang_projection_m, room.overhang_height_m)
        on_glass = None if answer is SunOnWindow.CANNOT_SAY else answer is SunOnWindow.ON
        if sensor is not None:
            source = "geometry (sensor missing)"
    if not on_glass:
        # not on the glass, or cannot say: sun counted as 0
        return SunSnapshot(on_glass, source, 1.0, transmission, 0.0, ValueTag.MEASURED)
    hour = _hour_for(hours, now)
    f_clear = clear_sky_fraction(hour.cloud_coverage, hour.uv_index) if hour is not None else None
    tag = ValueTag.MEASURED
    if f_clear is None:
        f_clear, tag = 1.0, ValueTag.ESTIMATED  # the filter does not correct on it [Prop]
    return SunSnapshot(True, source, f_clear, transmission, f_clear * transmission, tag)


def _forecast_hours(
    forecast: Sequence[Mapping[str, Any]],
    temperature_unit: str | None,
    wind_unit: str | None,
    out_t: float | None,
    out_rh: float | None,
    out_w: float | None,
) -> tuple[ForecastHour, ...]:
    """The hourly forecast; any value it leaves out is filled from the
    outdoor sensors' readings now [Prop]."""
    to_c = _TEMPERATURE.get(temperature_unit)
    to_ms = _SPEED_MS.get(wind_unit)
    out: list[ForecastHour] = []
    for item in forecast:
        when_raw = item.get("datetime")
        try:
            start = datetime.fromisoformat(str(when_raw))
        except ValueError:
            continue
        if start.tzinfo is None:
            continue
        t = _to_float(item.get("temperature"))
        w = _to_float(item.get("wind_speed"))
        rh = _to_float(item.get("humidity"))
        out.append(
            ForecastHour(
                start=start,
                temperature=to_c(t) if t is not None and to_c is not None else out_t,
                humidity=rh if rh is not None else out_rh,
                wind_ms=to_ms(w) if w is not None and to_ms is not None else out_w,
                cloud_coverage=_to_float(item.get("cloud_coverage")),
                uv_index=_to_float(item.get("uv_index")),
            )
        )
    out.sort(key=lambda h: h.start)
    return tuple(out)
