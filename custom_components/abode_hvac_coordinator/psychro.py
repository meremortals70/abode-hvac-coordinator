"""Dew point, free cooling and condensation risk (Section 4.1, Section 9)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .const import (
    CONDENSATION_MARGIN_C,
    DEW_POINT_MIN_RH,
    FREE_COOLING_DEW_POINT_MARGIN_C,
    FREE_COOLING_INDEX_MARGIN,
    FREE_COOLING_WIND_SHARE,
)
from .hci import outdoor_apparent_temperature

# Magnus form, WMO-No. 8 coefficients [Set][R5]
_MAGNUS_A = 17.62
_MAGNUS_B = 243.12


def dew_point(t_c: float, rh: float) -> float:
    """Dew point (C) by the Magnus form; RH is taken as at least 0.5 % [Set][R5].

    g = ln(RH/100) + 17.62 T / (243.12 + T); Td = 243.12 g / (17.62 - g)
    """
    rh = max(rh, DEW_POINT_MIN_RH)
    g = math.log(rh / 100.0) + _MAGNUS_A * t_c / (_MAGNUS_B + t_c)
    return _MAGNUS_B * g / (_MAGNUS_A - g)


@dataclass(frozen=True, slots=True)
class FreeCoolingAdvice:
    """Whether opening up would cool the room, both figures, and why [Set]."""

    advised: bool
    outdoors_feels: float | None
    room_index: float | None
    outdoor_dew_point: float | None
    room_dew_point: float | None
    reason: str


def free_cooling(
    *,
    needs_cooling: bool,
    room_index: float | None,
    room_t: float | None,
    room_rh: float | None,
    outdoor_t: float | None,
    outdoor_rh: float | None,
    outdoor_wind_ms: float | None,
) -> FreeCoolingAdvice:
    """Free cooling advice, only when the plan is to cool the room [Set].

    Advised only when outdoors feels at least 2.0 below the room's index,
    using wind x 0.3, and the outdoor dew point is at least 1.0 C below
    the room's. A missing wind reading counts as still air.
    """
    if not needs_cooling:
        return FreeCoolingAdvice(False, None, room_index, None, None, "The room does not need cooling.")
    if room_index is None or room_t is None or room_rh is None or outdoor_t is None or outdoor_rh is None:
        return FreeCoolingAdvice(False, None, room_index, None, None, "A reading needed to judge outdoor air is missing.")
    wind = (outdoor_wind_ms or 0.0) * FREE_COOLING_WIND_SHARE
    feels = outdoor_apparent_temperature(outdoor_t, outdoor_rh, wind)
    out_dp = dew_point(outdoor_t, outdoor_rh)
    room_dp = dew_point(room_t, room_rh)
    cooler = feels <= room_index - FREE_COOLING_INDEX_MARGIN
    drier = out_dp <= room_dp - FREE_COOLING_DEW_POINT_MARGIN_C
    if cooler and drier:
        reason = (
            f"Outdoors feels {feels:.1f}, {room_index - feels:.1f} below the room, "
            f"and its dew point {out_dp:.1f} C is {room_dp - out_dp:.1f} C below the room's."
        )
    elif not cooler:
        reason = f"Outdoors feels {feels:.1f}, not {FREE_COOLING_INDEX_MARGIN:.1f} below the room's {room_index:.1f}."
    else:
        reason = (
            f"The outdoor dew point {out_dp:.1f} C is not {FREE_COOLING_DEW_POINT_MARGIN_C:.1f} C "
            f"below the room's {room_dp:.1f} C."
        )
    return FreeCoolingAdvice(cooler and drier, feels, room_index, out_dp, room_dp, reason)


def condensation_risk(setpoint_c: float, room_dew_point_c: float) -> bool:
    """True when the setpoint is within 2.5 C of the room's dew point.

    Reported only, never acted on [Set].
    """
    return setpoint_c - room_dew_point_c < CONDENSATION_MARGIN_C
