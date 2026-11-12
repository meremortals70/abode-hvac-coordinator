"""Dew point, outdoor air's moisture and condensation risk (Section 4.1, Section 9)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .const import (
    CONDENSATION_MARGIN_C,
    DEW_POINT_MIN_RH,
    FREE_COOLING_DEW_POINT_MARGIN_C,
)

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
class MoistureAdvice:
    """Whether outdoor air is dry enough to admit, both dew points, and why."""

    ok: bool
    outdoor_dew_point: float | None
    room_dew_point: float | None
    reason: str


def outdoor_air_dry(
    *,
    room_t: float | None,
    room_rh: float | None,
    outdoor_t: float | None,
    outdoor_rh: float | None,
) -> MoistureAdvice:
    """Whether the outdoor air is dry enough to admit [Set][D105].

    Its dew point must be at least 1.0 C below the room's. This is the one
    measured constraint on free cooling: the model learns the heat an
    opening brings (physics.py, vent) but has no learned moisture coupling
    for an opening, so humid air is refused rather than priced.
    """
    if room_t is None or room_rh is None or outdoor_t is None or outdoor_rh is None:
        return MoistureAdvice(False, None, None, "A reading needed to judge outdoor air is missing.")
    out_dp = dew_point(outdoor_t, outdoor_rh)
    room_dp = dew_point(room_t, room_rh)
    if out_dp <= room_dp - FREE_COOLING_DEW_POINT_MARGIN_C:
        return MoistureAdvice(
            True, out_dp, room_dp,
            f"The outdoor dew point {out_dp:.1f} C is {room_dp - out_dp:.1f} C below the room's.",
        )
    return MoistureAdvice(
        False, out_dp, room_dp,
        f"The outdoor dew point {out_dp:.1f} C is not {FREE_COOLING_DEW_POINT_MARGIN_C:.1f} C "
        f"below the room's {room_dp:.1f} C.",
    )


def condensation_risk(setpoint_c: float, room_dew_point_c: float) -> bool:
    """True when the setpoint is within 2.5 C of the room's dew point.

    Reported only, never acted on [Set].
    """
    return setpoint_c - room_dew_point_c < CONDENSATION_MARGIN_C
