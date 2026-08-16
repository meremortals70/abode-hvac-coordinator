"""Dew point, free cooling and condensation risk.

Pure. No Home Assistant imports.

WHY DEW POINT AND NOT TEMPERATURE
---------------------------------
Two questions this controller has to answer cannot be answered from dry bulb
alone, and getting either wrong is expensive in a subtropical climate.

**Is opening a window worth it?** A Brisbane evening at 24 C outdoors against
26 C indoors looks like free cooling. If the outdoor dew point is 22 C and the
indoor dew point is 15 C, opening the window drops two degrees of dry bulb and
loads the room with moisture the air conditioner then spends an hour removing.
The room feels worse and the unit works harder. Dry bulb says open it; dew
point says do not.

**Will the coil, the duct or the glass sweat?** Condensation forms wherever a
surface sits below the dew point of the air touching it. Aggressive setpoints
in humid weather put surfaces there, and the result is mould in places nobody
inspects.

WHAT THIS DOES NOT DO
---------------------
It does not open windows and it does not command anything. Free cooling is
published as an advisory for the occupant or for an automation that owns a
window actuator; this controller owns the air conditioner and the covers, and
nothing else.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Magnus coefficients over water, valid across the range a house sees.
#: Sonntag 1990. Accurate to better than 0.1 C between -45 C and 60 C, which
#: is far more than this needs.
_MAGNUS_B = 17.62
_MAGNUS_C = 243.12

#: How much cooler and drier outdoors must be before opening up is advised.
#: Small margins produce advice that flips on and off with sensor noise, and
#: advice nobody can act on twice in a row is worse than none.
FREE_COOLING_DRY_BULB_MARGIN_C = 2.0
FREE_COOLING_DEW_POINT_MARGIN_C = 1.0

#: How close a surface may come to the dew point before it is called a risk.
#: Not zero: the coldest surface in a room is never the one being measured, and
#: the supply air off a coil is colder than any setpoint.
CONDENSATION_MARGIN_C = 2.5


def dew_point_c(temp_c: float, relative_humidity: float) -> float:
    """Dew point from dry bulb and relative humidity, in Celsius.

    Relative humidity is clamped away from zero rather than allowed to produce
    a logarithm of zero. A sensor reporting 0% is broken, and the honest
    handling is a very low dew point rather than a traceback.
    """
    rh = min(max(relative_humidity, 0.5), 100.0) / 100.0
    gamma = math.log(rh) + (_MAGNUS_B * temp_c) / (_MAGNUS_C + temp_c)
    return (_MAGNUS_C * gamma) / (_MAGNUS_B - gamma)


@dataclass(frozen=True, slots=True)
class FreeCooling:
    """Whether outdoor air would help, and why or why not."""

    advised: bool
    indoor_dew_point_c: float | None
    outdoor_dew_point_c: float | None
    reason: str


def free_cooling(
    *,
    indoor_c: float | None,
    indoor_rh: float | None,
    outdoor_c: float | None,
    outdoor_rh: float | None,
    demand: str | None,
) -> FreeCooling:
    """Whether opening up would cool the room without loading it with water.

    Both tests must pass. Outdoor air that is cooler but wetter is the trap
    this exists to catch, and it is the normal condition on a subtropical
    evening after rain.
    """
    if demand != "cool":
        return FreeCooling(
            advised=False,
            indoor_dew_point_c=None,
            outdoor_dew_point_c=None,
            reason="free cooling: the room is not asking to be cooled",
        )

    if None in (indoor_c, indoor_rh, outdoor_c, outdoor_rh):
        return FreeCooling(
            advised=False,
            indoor_dew_point_c=None,
            outdoor_dew_point_c=None,
            reason="free cooling: needs indoor and outdoor temperature and humidity",
        )

    assert indoor_c is not None and indoor_rh is not None
    assert outdoor_c is not None and outdoor_rh is not None

    indoor_dp = dew_point_c(indoor_c, indoor_rh)
    outdoor_dp = dew_point_c(outdoor_c, outdoor_rh)

    cool_enough = outdoor_c <= indoor_c - FREE_COOLING_DRY_BULB_MARGIN_C
    dry_enough = outdoor_dp <= indoor_dp - FREE_COOLING_DEW_POINT_MARGIN_C

    if cool_enough and dry_enough:
        reason = (
            f"free cooling: outdoors is {indoor_c - outdoor_c:.1f} C cooler and "
            f"{indoor_dp - outdoor_dp:.1f} C drier at the dew point — opening up "
            "would help"
        )
    elif cool_enough:
        reason = (
            f"free cooling: outdoors is {indoor_c - outdoor_c:.1f} C cooler but "
            f"its dew point is {outdoor_dp:.1f} C against {indoor_dp:.1f} C "
            "indoors — opening up would import moisture the unit then has to "
            "remove"
        )
    else:
        reason = (
            f"free cooling: outdoors is {outdoor_c:.1f} C against {indoor_c:.1f} C "
            "indoors, not cool enough to help"
        )

    return FreeCooling(
        advised=cool_enough and dry_enough,
        indoor_dew_point_c=round(indoor_dp, 1),
        outdoor_dew_point_c=round(outdoor_dp, 1),
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class CondensationRisk:
    """Whether the commanded conditions put a surface near the dew point."""

    at_risk: bool
    dew_point_c: float | None
    margin_c: float | None
    reason: str | None


def condensation_risk(
    *, indoor_c: float | None, indoor_rh: float | None, setpoint_c: float | None
) -> CondensationRisk:
    """Whether the commanded setpoint sits too near the room's dew point.

    The setpoint stands in for the coldest surface the room's air will touch.
    It is an understatement — supply air off the coil is colder still — which
    is why the margin is wide rather than tight.

    Nothing is refused on the strength of this. It is reported, because the
    right response to a humid room is to dehumidify it, and that is a decision
    the actuator ordering already makes.
    """
    if indoor_c is None or indoor_rh is None or setpoint_c is None:
        return CondensationRisk(
            at_risk=False, dew_point_c=None, margin_c=None, reason=None
        )

    dew = dew_point_c(indoor_c, indoor_rh)
    margin = setpoint_c - dew
    if margin >= CONDENSATION_MARGIN_C:
        return CondensationRisk(
            at_risk=False, dew_point_c=round(dew, 1), margin_c=round(margin, 1),
            reason=None,
        )

    return CondensationRisk(
        at_risk=True,
        dew_point_c=round(dew, 1),
        margin_c=round(margin, 1),
        reason=(
            f"condensation: dew point is {dew:.1f} C and the setpoint is "
            f"{setpoint_c:.1f} C, a {margin:.1f} C margin — surfaces in this "
            "room may sweat, dehumidify rather than chase the setpoint"
        ),
    )
