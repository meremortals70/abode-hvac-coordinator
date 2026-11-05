"""What a unit can do, read from its climate entity (Section 9) [D048].

Attribute names and feature flags are those of Home Assistant's climate
platform (homeassistant/components/climate/const.py, release 2026.10.3).
They are written out here because this helper imports nothing from Home
Assistant [D002].
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .const import NO_STEP_ADVERTISED
from .models import CapabilityProfile

# climate/const.py, 2026.10.3 [HA]
ATTR_HVAC_MODES = "hvac_modes"
ATTR_FAN_MODES = "fan_modes"
ATTR_SWING_MODES = "swing_modes"
ATTR_SWING_HORIZONTAL_MODES = "swing_horizontal_modes"
ATTR_MIN_TEMP = "min_temp"
ATTR_MAX_TEMP = "max_temp"
ATTR_TARGET_TEMP_STEP = "target_temp_step"
ATTR_SUPPORTED_FEATURES = "supported_features"
FEATURE_TARGET_TEMPERATURE = 1
FEATURE_TARGET_TEMPERATURE_RANGE = 2
FEATURE_FAN_MODE = 8
FEATURE_SWING_MODE = 32
FEATURE_SWING_HORIZONTAL_MODE = 512

NOT_REPORTING_STATES = frozenset({"unavailable", "unknown"})

# Temperatures to and from C, the unit every module works in; the unit
# names are those of homeassistant/const.py UnitOfTemperature (2026.10.3).
# A climate entity's attributes, and set_temperature's fields, are in the
# system's unit; Home Assistant converts them to and from the entity's own
# (climate/services.py, 2026.10.3) [HA]. None means no unit was given,
# taken as C.
TO_C: dict[str | None, Callable[[float], float]] = {
    None: lambda v: v,
    "°C": lambda v: v,
    "°F": lambda v: (v - 32.0) * 5.0 / 9.0,
    "K": lambda v: v - 273.15,
}
FROM_C: dict[str | None, Callable[[float], float]] = {
    None: lambda v: v,
    "°C": lambda v: v,
    "°F": lambda v: v * 9.0 / 5.0 + 32.0,
    "K": lambda v: v + 273.15,
}
# the size of one degree of each unit, in C
DEGREE_IN_C: dict[str | None, float] = {None: 1.0, "°C": 1.0, "°F": 5.0 / 9.0, "K": 1.0}


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(str(v) for v in value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def read_profile(
    state: str | None,
    attributes: Mapping[str, Any],
    system_temperature_unit: str | None = "°C",
    native_temperature_unit: str | None = None,
    native_limits: tuple[float, float] | None = None,
) -> CapabilityProfile | None:
    """A unit's profile from its live state and attributes.

    None when the unit is not reporting (state missing, unavailable or
    unknown, or no setpoint limits), so setup can refuse it [D048].
    A unit that advertises no step is taken to step in tenths [D051].
    One setpoint is preferred where the unit offers both; a unit that
    offers only a range is range-only [D024]. The limits and the step are
    read in the system's temperature unit and stored in C. Home Assistant
    converts the limits to the system's unit but publishes the step in
    the entity's own unit (climate/entity.py capability_attributes,
    2026.11.0b0; climate/__init__.py, 2026.9.4) [HA], so the step is
    converted from native_temperature_unit, or from the system's unit
    where that is not known.

    The limits shown are rounded for display too (whole degrees in a F
    house, climate/entity.py:140-164, 2026.11.0b0), while Home Assistant
    refuses a setpoint outside the unit's own limits by any amount
    (climate/services.py:150) [HA]. So native_limits, the entity's own
    min_temp and max_temp in its own unit, are used where given; failing
    that, limits shown in whole degrees for a unit working in another unit
    are moved in by half a shown degree and onto its step, so nothing sent
    can fall outside the real ones.
    """
    if state is None or state in NOT_REPORTING_STATES:
        return None
    to_c = TO_C.get(system_temperature_unit)
    degree = DEGREE_IN_C.get(system_temperature_unit)
    if to_c is None or degree is None:
        return None
    lo_raw = _number(attributes.get(ATTR_MIN_TEMP))
    hi_raw = _number(attributes.get(ATTR_MAX_TEMP))
    if lo_raw is None or hi_raw is None or lo_raw > hi_raw:
        return None
    lo, hi = to_c(lo_raw), to_c(hi_raw)
    native_to_c = TO_C.get(native_temperature_unit) if native_temperature_unit else None
    if native_limits is not None and native_to_c is not None and native_limits[0] <= native_limits[1]:
        lo, hi = native_to_c(native_limits[0]), native_to_c(native_limits[1])
    elif (
        native_temperature_unit is not None
        and native_temperature_unit != system_temperature_unit
        and float(lo_raw).is_integer()
        and float(hi_raw).is_integer()
    ):
        lo, hi = lo + degree / 2.0, hi - degree / 2.0
        grid = _number(attributes.get(ATTR_TARGET_TEMP_STEP))
        grid_c = grid * DEGREE_IN_C.get(native_temperature_unit, degree) if grid and grid > 0 else NO_STEP_ADVERTISED
        lo, hi = math.ceil(lo / grid_c - 1e-9) * grid_c, math.floor(hi / grid_c + 1e-9) * grid_c
        if lo > hi:
            return None
    features_raw = attributes.get(ATTR_SUPPORTED_FEATURES, 0)
    features = int(features_raw) if isinstance(features_raw, int) and not isinstance(features_raw, bool) else 0
    single = bool(features & FEATURE_TARGET_TEMPERATURE)
    ranged = bool(features & FEATURE_TARGET_TEMPERATURE_RANGE)
    advertised = _number(attributes.get(ATTR_TARGET_TEMP_STEP))
    step_degree = DEGREE_IN_C.get(native_temperature_unit, degree) if native_temperature_unit else degree
    step = advertised * step_degree if advertised is not None and advertised > 0.0 else NO_STEP_ADVERTISED
    return CapabilityProfile(
        hvac_modes=_strings(attributes.get(ATTR_HVAC_MODES)),
        fan_modes=_strings(attributes.get(ATTR_FAN_MODES)) if features & FEATURE_FAN_MODE else (),
        vertical_vanes=_strings(attributes.get(ATTR_SWING_MODES)) if features & FEATURE_SWING_MODE else (),
        horizontal_vanes=(
            _strings(attributes.get(ATTR_SWING_HORIZONTAL_MODES))
            if features & FEATURE_SWING_HORIZONTAL_MODE
            else ()
        ),
        min_setpoint=lo,
        max_setpoint=hi,
        setpoint_step=step,
        range_only=ranged and not single,
        takes_range=ranged,
    )


def _shared(lists: Sequence[tuple[str, ...]]) -> tuple[str, ...]:
    """Values every list has, in the first list's order."""
    first, *rest = lists
    return tuple(v for v in first if all(v in other for other in rest))


def _whole_multiple(coarse: float, fine: float) -> bool:
    ratio = coarse / fine
    return abs(ratio - round(ratio)) < 1e-9


def combine(profiles: Sequence[CapabilityProfile]) -> CapabilityProfile:
    """What every head of a room can do together [D020][D051].

    Only the modes, fan speeds and vane positions every head has; the
    narrower range; the coarser step, provided one step is a whole
    multiple of the other; otherwise setpoints are not rounded, and the
    reason says why. If any head takes only a range, the room is sent a
    range (decision of 27 October 2026); a room where another head cannot
    take a range is refused at setup, as are ranges that do not overlap
    (setup_problem).
    """
    if not profiles:
        raise ValueError("a room needs at least one head")
    if len(profiles) == 1:
        return profiles[0]
    lo = max(p.min_setpoint for p in profiles)
    hi = min(p.max_setpoint for p in profiles)
    steps = [p.setpoint_step for p in profiles]
    step: float | None
    reason: str | None = None
    if any(s is None for s in steps):
        step = None
        reason = "A head's setpoints are not rounded, so the room's are not."
    else:
        known = [s for s in steps if s is not None]
        coarse = max(known)
        if all(_whole_multiple(coarse, s) for s in known):
            step = coarse
        else:
            step = None
            reason = (
                "The heads' setpoint steps ("
                + ", ".join(f"{s:g}" for s in known)
                + ") are not whole multiples of one another, so setpoints are not rounded."
            )
    if lo > hi:
        reason = (reason + " " if reason else "") + "The heads' setpoint ranges do not overlap."
    return CapabilityProfile(
        hvac_modes=_shared([p.hvac_modes for p in profiles]),
        fan_modes=_shared([p.fan_modes for p in profiles]),
        vertical_vanes=_shared([p.vertical_vanes for p in profiles]),
        horizontal_vanes=_shared([p.horizontal_vanes for p in profiles]),
        min_setpoint=lo,
        max_setpoint=hi,
        setpoint_step=step,
        range_only=any(p.range_only for p in profiles),
        step_reason=reason,
        takes_range=all(p.takes_range for p in profiles),
    )


def setup_problem(profile: CapabilityProfile) -> str | None:
    """Why a room's combined profile cannot be run, or None.

    A room whose heads' setpoint ranges do not overlap is refused at setup,
    as one whose unit is not reporting is (decision of 27 October 2026). So
    is one where a head takes only a range and another cannot take one:
    nothing sent could suit both, and Home Assistant refuses a range to a
    unit that does not advertise it (climate/services.py:98-106) [D024].
    """
    if profile.min_setpoint > profile.max_setpoint:
        return "The heads' setpoint ranges do not overlap."
    if not heads_agree_on_setpoints(profile):
        return "One head takes only a low and high setpoint, and another cannot take them."
    return None


def heads_agree_on_setpoints(profile: CapabilityProfile) -> bool:
    """Every head can take what the room is sent: a range for a room with a
    range-only head [D024]."""
    return not profile.range_only or profile.takes_range


def round_to_step(value: float, profile: CapabilityProfile) -> float:
    """value clamped to the profile's range and rounded to its step [D051].

    Not rounded where the profile has no step.
    """
    out = min(max(value, profile.min_setpoint), profile.max_setpoint)
    if profile.setpoint_step is None:
        return out
    stepped = round(out / profile.setpoint_step) * profile.setpoint_step
    return min(max(stepped, profile.min_setpoint), profile.max_setpoint)
