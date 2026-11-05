"""Form answers to stored settings and back (Section 8, forms.py) [Set].

Turns what the setup and options screens collect into the house's and the
rooms' settings, fills in defaults, checks the bands, and writes the
plain-English summary shown on the options menu. It imports nothing from
Home Assistant, so it is tested without it.

Stored shapes. The config entry's data holds the house: the House screens'
answers under the CONF_ keys of const.py. Its options hold the rooms, a list
under "rooms", each a dictionary of the Room screens' answers plus the room
id and the capability profile read when the room was saved; and the names
already used for outdoor units and lockout reasons, for the dropdowns.
Minutes stay minutes in storage, as the screens show them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .const import (
    CONF_ALLOW_BLIND_CONTROL,
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_THROUGH,
    CONF_BATTERY_CHARGE,
    CONF_BATTERY_MAX_DISCHARGE,
    CONF_BATTERY_SIZE,
    CONF_BLINDS,
    CONF_CLIMATE,
    CONF_EMPTY_AFTER,
    CONF_FREQUENCY_SENSOR,
    CONF_GRID_POSITIVE_IS_IMPORT,
    CONF_GRID_POWER,
    CONF_HEADS,
    CONF_HOUSE_LOAD,
    CONF_HUMIDITY_SENSOR,
    CONF_LOCKOUT_REASON,
    CONF_NAME,
    CONF_OCCUPIED_AFTER,
    CONF_OCCUPIED_HIGH,
    CONF_OCCUPIED_LOW,
    CONF_OPENING_GRACE,
    CONF_OPENINGS,
    CONF_OUTDOOR_HUMIDITY,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_OUTDOOR_UNIT,
    CONF_OUTDOOR_WIND,
    CONF_OVERHANG_HEIGHT,
    CONF_OVERHANG_PROJECTION,
    CONF_POWER_MANAGEMENT,
    CONF_PRECOOL_HIGH,
    CONF_PRECOOL_LOW,
    CONF_PRESENCE_SENSOR,
    CONF_PROFILE,
    CONF_RESERVE_MARGIN,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SLEEP_HIGH,
    CONF_SLEEP_LOW,
    CONF_SLEEP_SCHEDULE,
    CONF_SOLAR_FORECAST_SOURCES,
    CONF_SOLAR_POWER,
    CONF_SUN_SENSOR,
    CONF_TARIFF_ENTRY,
    CONF_TEMPERATURE_SENSOR,
    CONF_TTS_ENGINE,
    CONF_WARNING_GRACE,
    CONF_WEATHER,
    CONF_WINDOW_FACING,
    DEFAULT_ALLOW_BLIND_CONTROL,
    DEFAULT_ANNOUNCE,
    DEFAULT_EMPTY_AFTER_MIN,
    DEFAULT_LOCKOUT_REASONS,
    DEFAULT_OCCUPIED_AFTER_MIN,
    DEFAULT_OCCUPIED_BAND,
    DEFAULT_OPENING_GRACE_MIN,
    DEFAULT_PRECOOL_BAND,
    DEFAULT_SLEEP_BAND,
    DEFAULT_WARNING_GRACE_MIN,
    LOCKOUT_NOT_LOCKED_OUT,
    WINDOW_FACINGS,
)
from .memory import profile_from_dict, profile_to_dict
from .models import (
    CapabilityProfile,
    HeadConfig,
    HouseConfig,
    PowerManagement,
    RoomConfig,
)
from .observe import _POWER_KW

# Option keys beside the rooms: names already used, for the dropdowns [Set]
CONF_OUTDOOR_UNIT_NAMES = "outdoor_unit_names"
CONF_LOCKOUT_REASONS = "lockout_reasons"

# The room settings each screen collects
ROOM_SCREEN_1 = (
    CONF_NAME, CONF_CLIMATE, CONF_TEMPERATURE_SENSOR, CONF_HUMIDITY_SENSOR, CONF_PRESENCE_SENSOR,
    CONF_SLEEP_SCHEDULE, CONF_SUN_SENSOR, CONF_OPENINGS, CONF_BLINDS, CONF_ALLOW_BLIND_CONTROL,
    CONF_WINDOW_FACING, CONF_OVERHANG_PROJECTION, CONF_OVERHANG_HEIGHT, CONF_OCCUPIED_AFTER, CONF_EMPTY_AFTER,
    CONF_WARNING_GRACE, CONF_ANNOUNCE, CONF_ANNOUNCE_THROUGH, CONF_OPENING_GRACE, CONF_LOCKOUT_REASON,
)
BAND_KEYS = (
    (CONF_OCCUPIED_LOW, CONF_OCCUPIED_HIGH),
    (CONF_SLEEP_LOW, CONF_SLEEP_HIGH),
    (CONF_PRECOOL_LOW, CONF_PRECOOL_HIGH),
)
ROOM_SCREEN_3 = (*(k for pair in BAND_KEYS for k in pair), CONF_POWER_MANAGEMENT)

ROOM_DEFAULTS: dict[str, Any] = {
    CONF_ALLOW_BLIND_CONTROL: DEFAULT_ALLOW_BLIND_CONTROL,
    CONF_OCCUPIED_AFTER: DEFAULT_OCCUPIED_AFTER_MIN,
    CONF_EMPTY_AFTER: DEFAULT_EMPTY_AFTER_MIN,
    CONF_WARNING_GRACE: DEFAULT_WARNING_GRACE_MIN,
    CONF_ANNOUNCE: DEFAULT_ANNOUNCE,
    CONF_OPENING_GRACE: DEFAULT_OPENING_GRACE_MIN,
    CONF_LOCKOUT_REASON: LOCKOUT_NOT_LOCKED_OUT,
    CONF_OCCUPIED_LOW: DEFAULT_OCCUPIED_BAND[0],
    CONF_OCCUPIED_HIGH: DEFAULT_OCCUPIED_BAND[1],
    CONF_SLEEP_LOW: DEFAULT_SLEEP_BAND[0],
    CONF_SLEEP_HIGH: DEFAULT_SLEEP_BAND[1],
    CONF_PRECOOL_LOW: DEFAULT_PRECOOL_BAND[0],
    CONF_PRECOOL_HIGH: DEFAULT_PRECOOL_BAND[1],
    CONF_POWER_MANAGEMENT: PowerManagement.OFF.value,
}

HOUSE_KEYS = (
    CONF_TARIFF_ENTRY, CONF_OUTDOOR_TEMPERATURE, CONF_OUTDOOR_HUMIDITY, CONF_OUTDOOR_WIND, CONF_WEATHER,
    CONF_BATTERY_CHARGE, CONF_BATTERY_SIZE, CONF_BATTERY_MAX_DISCHARGE, CONF_SOLAR_POWER, CONF_HOUSE_LOAD,
    CONF_RESERVE_MARGIN, CONF_GRID_POWER, CONF_GRID_POSITIVE_IS_IMPORT, CONF_SOLAR_FORECAST_SOURCES,
    CONF_TTS_ENGINE,
)


# --------------------------------------------------------------------------
# Reading values
# --------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    """A non-empty string, or None."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _texts(value: Any) -> tuple[str, ...]:
    """A list of entity ids or names, empty ones and repeats left out."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        return ()
    return tuple(dict.fromkeys(t for t in (_text(v) for v in value) if t is not None))


def _flag(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _minutes(value: Any, default: float) -> float:
    n = _number(value)
    return n if n is not None and n >= 0.0 else default


# --------------------------------------------------------------------------
# The house
# --------------------------------------------------------------------------


def house_from_data(data: Mapping[str, Any]) -> HouseConfig:
    """The house's settings from the config entry's data."""
    grid_sign = data.get(CONF_GRID_POSITIVE_IS_IMPORT)
    return HouseConfig(
        tariff_entry=_text(data.get(CONF_TARIFF_ENTRY)),
        outdoor_temperature=_text(data.get(CONF_OUTDOOR_TEMPERATURE)),
        outdoor_humidity=_text(data.get(CONF_OUTDOOR_HUMIDITY)),
        outdoor_wind=_text(data.get(CONF_OUTDOOR_WIND)),
        weather=_text(data.get(CONF_WEATHER)),
        battery_charge=_text(data.get(CONF_BATTERY_CHARGE)),
        battery_size_kwh=_number(data.get(CONF_BATTERY_SIZE)),
        battery_max_discharge_kw=_number(data.get(CONF_BATTERY_MAX_DISCHARGE)),
        solar_power=_text(data.get(CONF_SOLAR_POWER)),
        house_load=_text(data.get(CONF_HOUSE_LOAD)),
        reserve_margin_kwh=_number(data.get(CONF_RESERVE_MARGIN)),
        grid_power=_text(data.get(CONF_GRID_POWER)),
        grid_positive_is_import=grid_sign if isinstance(grid_sign, bool) else None,
        solar_forecast_sources=_texts(data.get(CONF_SOLAR_FORECAST_SOURCES)),
        tts_engine=_text(data.get(CONF_TTS_ENGINE)),
    )


def clean_house(answers: Mapping[str, Any]) -> dict[str, Any]:
    """The House screens' answers as stored: only the house's keys, and
    nothing for a field left empty."""
    out: dict[str, Any] = {}
    for key in HOUSE_KEYS:
        value = answers.get(key)
        if value is None or value == "" or value == []:
            continue
        out[key] = value
    return out


def grid_sensor_changed(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """Grid direction is asked only when the grid sensor is new or changed
    [D038]."""
    sensor = _text(new.get(CONF_GRID_POWER))
    return sensor is not None and (
        sensor != _text(old.get(CONF_GRID_POWER)) or not isinstance(old.get(CONF_GRID_POSITIVE_IS_IMPORT), bool)
    )


def power_kw(state: str, unit: Any) -> float | None:
    """A power reading in kW, in the units Observe reads [D038]."""
    try:
        value = float(state)
    except ValueError:
        return None
    convert = _POWER_KW.get(unit if isinstance(unit, str) else None)
    return convert(value) if convert is not None and math.isfinite(value) else None


# --------------------------------------------------------------------------
# Rooms
# --------------------------------------------------------------------------


def _band(data: Mapping[str, Any], keys: tuple[str, str]) -> tuple[float, float]:
    lo = _number(data.get(keys[0]))
    hi = _number(data.get(keys[1]))
    return (
        lo if lo is not None else float(ROOM_DEFAULTS[keys[0]]),
        hi if hi is not None else float(ROOM_DEFAULTS[keys[1]]),
    )


def room_from_options(data: Mapping[str, Any]) -> RoomConfig:
    """One room's settings, with defaults for anything not set.

    Raises ValueError for a room that cannot run at all: no id, no name,
    no air conditioner, or no temperature or humidity sensor [D046].
    """
    room_id = _text(data.get(CONF_ROOM_ID))
    name = _text(data.get(CONF_NAME))
    temperature = _text(data.get(CONF_TEMPERATURE_SENSOR))
    humidity = _text(data.get(CONF_HUMIDITY_SENSOR))
    heads_raw = data.get(CONF_HEADS)
    heads: list[HeadConfig] = []
    if isinstance(heads_raw, list):
        for h in heads_raw:
            if isinstance(h, Mapping) and (climate := _text(h.get(CONF_CLIMATE))) is not None:
                heads.append(HeadConfig(climate, _text(h.get(CONF_OUTDOOR_UNIT)), _text(h.get(CONF_FREQUENCY_SENSOR))))
    if room_id is None or name is None or temperature is None or humidity is None or not heads:
        raise ValueError("the room's id, name, air conditioners and sensors are required")
    management_raw = data.get(CONF_POWER_MANAGEMENT, PowerManagement.OFF.value)
    try:
        management = PowerManagement(management_raw)
    except ValueError:
        management = PowerManagement.OFF
    facing = _text(data.get(CONF_WINDOW_FACING))
    lockout = _text(data.get(CONF_LOCKOUT_REASON))
    return RoomConfig(
        room_id=room_id,
        name=name,
        heads=tuple(heads),
        temperature_sensor=temperature,
        humidity_sensor=humidity,
        occupied_band=_band(data, BAND_KEYS[0]),
        sleep_band=_band(data, BAND_KEYS[1]),
        precool_band=_band(data, BAND_KEYS[2]),
        power_management=management,
        presence_sensor=_text(data.get(CONF_PRESENCE_SENSOR)),
        sleep_schedule=_text(data.get(CONF_SLEEP_SCHEDULE)),
        sun_sensor=_text(data.get(CONF_SUN_SENSOR)),
        openings=_texts(data.get(CONF_OPENINGS)),
        blinds=_texts(data.get(CONF_BLINDS)),
        allow_blind_control=_flag(data.get(CONF_ALLOW_BLIND_CONTROL), DEFAULT_ALLOW_BLIND_CONTROL),
        window_facing=facing.upper() if facing is not None and facing.upper() in WINDOW_FACINGS else None,
        overhang_projection_m=_number(data.get(CONF_OVERHANG_PROJECTION)),
        overhang_height_m=_number(data.get(CONF_OVERHANG_HEIGHT)),
        occupied_after_s=_minutes(data.get(CONF_OCCUPIED_AFTER), DEFAULT_OCCUPIED_AFTER_MIN) * 60.0,
        empty_after_s=_minutes(data.get(CONF_EMPTY_AFTER), DEFAULT_EMPTY_AFTER_MIN) * 60.0,
        warning_grace_s=_minutes(data.get(CONF_WARNING_GRACE), DEFAULT_WARNING_GRACE_MIN) * 60.0,
        announce=_flag(data.get(CONF_ANNOUNCE), DEFAULT_ANNOUNCE),
        announce_through=_texts(data.get(CONF_ANNOUNCE_THROUGH)),
        opening_grace_s=_minutes(data.get(CONF_OPENING_GRACE), DEFAULT_OPENING_GRACE_MIN) * 60.0,
        lockout_reason=None if lockout in (None, LOCKOUT_NOT_LOCKED_OUT) else lockout,
        profile=profile_from_dict(p) if isinstance(p := data.get(CONF_PROFILE), Mapping) else None,
    )


def rooms_from_options(options: Mapping[str, Any]) -> list[RoomConfig]:
    """Every room that can be read. One stored in a form that cannot be
    read is left out, never fatal to the others."""
    raw = options.get(CONF_ROOMS)
    out: list[RoomConfig] = []
    if not isinstance(raw, list):
        return out
    for r in raw:
        if not isinstance(r, Mapping):
            continue
        try:
            out.append(room_from_options(r))
        except ValueError:
            continue
    return out


def room_answers(stored: Mapping[str, Any]) -> dict[str, Any]:
    """A stored room as the Room screens' answers, to pre-fill Edit: the
    heads' climate entities as one list, the defaults for anything unset."""
    out = {**ROOM_DEFAULTS, **{k: v for k, v in stored.items() if k not in (CONF_HEADS, CONF_PROFILE)}}
    heads = stored.get(CONF_HEADS)
    out[CONF_CLIMATE] = [h[CONF_CLIMATE] for h in heads if isinstance(h, Mapping) and CONF_CLIMATE in h] if (
        isinstance(heads, list)
    ) else []
    return out


def build_room(
    room_id: str,
    screen1: Mapping[str, Any],
    heads: Sequence[Mapping[str, Any]],
    screen3: Mapping[str, Any],
    profile: CapabilityProfile,
) -> dict[str, Any]:
    """The stored room from its three screens and the profile read when it
    was saved [D048]."""
    out: dict[str, Any] = {CONF_ROOM_ID: room_id}
    for key in ROOM_SCREEN_1:
        if key == CONF_CLIMATE:
            continue
        value = screen1.get(key, ROOM_DEFAULTS.get(key))
        if value is None or value == "" or value == []:
            continue
        out[key] = value
    out[CONF_HEADS] = [
        {k: v for k, v in h.items() if k in (CONF_CLIMATE, CONF_OUTDOOR_UNIT, CONF_FREQUENCY_SENSOR) and v}
        for h in heads
    ]
    for key in ROOM_SCREEN_3:
        out[key] = screen3.get(key, ROOM_DEFAULTS[key])
    out[CONF_PROFILE] = profile_to_dict(profile)
    return out


def band_inverted(screen3: Mapping[str, Any]) -> str | None:
    """The first band whose low is not below its high, or None. A band
    with no width has no leeway and is refused (decision of 27 October
    2026) [Set]."""
    for name, keys in zip(("occupied", "sleep", "precool"), BAND_KEYS, strict=True):
        lo, hi = _band(screen3, keys)
        if lo >= hi:
            return name
    return None


def climate_used_elsewhere(
    stored_rooms: Sequence[Mapping[str, Any]], climates: Sequence[str], room_id: str | None
) -> tuple[str, str] | None:
    """An air conditioner already used by another room, and that room's
    name [D021]."""
    for r in stored_rooms:
        if r.get(CONF_ROOM_ID) == room_id:
            continue
        heads = r.get(CONF_HEADS)
        used = {h.get(CONF_CLIMATE) for h in heads if isinstance(h, Mapping)} if isinstance(heads, list) else set()
        for c in climates:
            if c in used:
                return c, str(r.get(CONF_NAME, ""))
    return None


def replace_room(options: Mapping[str, Any], room: Mapping[str, Any]) -> dict[str, Any]:
    """The options with this room added, or replacing the one with its id;
    its outdoor unit names and lockout reason remembered for the dropdowns
    [Set]."""
    rooms = [r for r in options.get(CONF_ROOMS, []) if isinstance(r, Mapping)]
    rid = room[CONF_ROOM_ID]
    replaced = [dict(room) if r.get(CONF_ROOM_ID) == rid else dict(r) for r in rooms]
    if not any(r.get(CONF_ROOM_ID) == rid for r in rooms):
        replaced.append(dict(room))
    units = list(options.get(CONF_OUTDOOR_UNIT_NAMES, []))
    for h in room.get(CONF_HEADS, []):
        name = _text(h.get(CONF_OUTDOOR_UNIT))
        if name is not None and name not in units:
            units.append(name)
    reasons = list(options.get(CONF_LOCKOUT_REASONS, []))
    reason = _text(room.get(CONF_LOCKOUT_REASON))
    if reason is not None and reason != LOCKOUT_NOT_LOCKED_OUT and reason not in (*DEFAULT_LOCKOUT_REASONS, *reasons):
        reasons.append(reason)
    return {**options, CONF_ROOMS: replaced, CONF_OUTDOOR_UNIT_NAMES: units, CONF_LOCKOUT_REASONS: reasons}


def remove_room(options: Mapping[str, Any], room_id: str) -> dict[str, Any]:
    """The options without this room."""
    rooms = [dict(r) for r in options.get(CONF_ROOMS, []) if isinstance(r, Mapping) and r.get(CONF_ROOM_ID) != room_id]
    return {**options, CONF_ROOMS: rooms}


def lockout_choices(options: Mapping[str, Any]) -> list[str]:
    """The lockout dropdown: "Not locked out" first, then the default
    reasons, then any the user has typed before [D021][Set]."""
    typed = [r for r in options.get(CONF_LOCKOUT_REASONS, []) if isinstance(r, str)]
    return list(dict.fromkeys([LOCKOUT_NOT_LOCKED_OUT, *DEFAULT_LOCKOUT_REASONS, *typed]))


def outdoor_unit_choices(options: Mapping[str, Any]) -> list[str]:
    """The outdoor unit dropdown: names already used [D020]."""
    return [u for u in options.get(CONF_OUTDOOR_UNIT_NAMES, []) if isinstance(u, str)]


# --------------------------------------------------------------------------
# Migration [Set]
# --------------------------------------------------------------------------

ENTRY_VERSION = 1
ENTRY_MINOR_VERSION = 1


def migrate_entry(version: int, minor: int, data: Mapping[str, Any], options: Mapping[str, Any]) -> tuple[
    dict[str, Any], dict[str, Any]
]:
    """A configuration stored in an older format, converted; never refused.
    Version 1.1 is the first, so nothing is converted yet."""
    return dict(data), dict(options)


# --------------------------------------------------------------------------
# The summary on the options menu [Set]
# --------------------------------------------------------------------------


def summary(house: HouseConfig, rooms: Sequence[RoomConfig]) -> str:
    """The configuration in plain English, one line per part."""
    lines: list[str] = []
    lines.append(f"Tariff: {'from Abode Power Tariffs' if house.tariff_entry else 'none; every hour priced the same'}.")
    lines.append(f"Forecast: {house.weather or 'none'}.")
    power = [n for n, v in (("battery", house.battery_charge), ("solar", house.solar_power),
                            ("house load", house.house_load), ("grid", house.grid_power)) if v]
    lines.append(f"Power sensors: {', '.join(power) if power else 'none'}.")
    if not rooms:
        lines.append("No rooms yet.")
    for r in rooms:
        heads = len(r.heads)
        lo, hi = r.occupied_band
        extra = f" Locked out: {r.lockout_reason}." if r.lockout_reason else ""
        lines.append(
            f"{r.name}: {heads} air conditioner{'s' if heads != 1 else ''}, comfort {lo:g} to {hi:g}, "
            f"power management {r.power_management.value}.{extra}"
        )
    return "\n".join(lines)

