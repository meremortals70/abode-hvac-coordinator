"""Dew point, free cooling and condensation risk.

Pure. No Home Assistant imports.

TWO QUESTIONS, TWO TESTS
------------------------
**Will it feel better with the windows open?** Not "is it cooler outside" —
dry bulb is the wrong comparison. What arrives through an open window is air at
some temperature, carrying some humidity, moving at some speed, and all three
change how the room feels. So the comparison is outdoor apparent temperature
against the room's comfort index: the same Steadman formula evaluated outdoors
with wind and indoors without. A 26 C breeze beats a still 26 C room, and dry
bulb cannot see that.

**Will I regret it in an hour?** A separate question, and it is the one dry
bulb *and* apparent temperature both get wrong. A Brisbane evening after rain
can feel cooler on arrival while carrying far more water than the room holds.
The breeze drops, the moisture stays, and the air conditioner spends an hour
removing it. Apparent temperature says open up; dew point says you will pay for
it. Dew point wins, because the felt benefit is transient and the latent load
is not.

Both tests must pass. Neither is redundant.

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

from .hci import apparent_temperature

#: Magnus coefficients over water, valid across the range a house sees.
#: Sonntag 1990. Accurate to better than 0.1 C between -45 C and 60 C, which
#: is far more than this needs.
_MAGNUS_B = 17.62
_MAGNUS_C = 243.12

#: How much better outdoors must feel, in HCI, before opening up is advised.
#: Small margins produce advice that flips on and off with sensor noise, and
#: advice nobody can act on twice in a row is worse than none.
FREE_COOLING_MARGIN_HCI = 2.0

#: How much drier outdoors must be at the dew point. Separate from the felt
#: comparison and not tradeable against it: a room can feel better and still be
#: taking on water.
FREE_COOLING_DEW_POINT_MARGIN_C = 1.0

#: Fraction of the free-stream wind that actually reaches a person in a room
#: with the windows open. Outdoor wind is measured at ten metres in the clear;
#: what gets through a window opening, past a flyscreen, around furniture and
#: into the middle of a room is a fraction of it.
#:
#: A stated assumption rather than a setting. Using the full outdoor figure
#: would overstate the benefit by roughly three degrees on a windy day, which
#: is the difference between good advice and advice that sends you to open the
#: windows on an evening you should not.
WIND_PENETRATION = 0.3

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
    #: Outdoor apparent temperature, on the comfort index scale, with the
    #: damped wind term applied. None when the outdoor feeds are incomplete.
    outdoor_apparent_c: float | None
    indoor_dew_point_c: float | None
    outdoor_dew_point_c: float | None
    reason: str


def free_cooling(
    *,
    indoor_hci: float | None,
    indoor_c: float | None,
    indoor_rh: float | None,
    outdoor_c: float | None,
    outdoor_rh: float | None,
    outdoor_wind_ms: float | None,
    demand: str | None,
) -> FreeCooling:
    """Whether opening up would make the room feel better without wetting it.

    Two independent tests, both of which must pass. The felt comparison is on
    the comfort index scale so that indoor and outdoor are the same quantity.
    The dew point test is a veto: it is not traded against the felt benefit,
    because the benefit stops when the breeze does and the moisture does not.

    Wind is damped by `WIND_PENETRATION` before it is applied. The outdoor
    figure is measured in the clear at ten metres; a room with the windows open
    sees a fraction of it.
    """
    if demand != "cool":
        return FreeCooling(
            advised=False,
            outdoor_apparent_c=None,
            indoor_dew_point_c=None,
            outdoor_dew_point_c=None,
            reason="free cooling: the room is not asking to be cooled",
        )

    if indoor_hci is None or None in (indoor_c, indoor_rh, outdoor_c, outdoor_rh):
        return FreeCooling(
            advised=False,
            outdoor_apparent_c=None,
            indoor_dew_point_c=None,
            outdoor_dew_point_c=None,
            reason=(
                "free cooling: needs a comfort reading indoors and both "
                "temperature and humidity outdoors"
            ),
        )

    assert indoor_c is not None and indoor_rh is not None
    assert outdoor_c is not None and outdoor_rh is not None

    wind = (outdoor_wind_ms or 0.0) * WIND_PENETRATION
    outdoor_at = apparent_temperature(outdoor_c, outdoor_rh, wind)
    indoor_dp = dew_point_c(indoor_c, indoor_rh)
    outdoor_dp = dew_point_c(outdoor_c, outdoor_rh)

    feels_better = outdoor_at <= indoor_hci - FREE_COOLING_MARGIN_HCI
    dry_enough = outdoor_dp <= indoor_dp - FREE_COOLING_DEW_POINT_MARGIN_C

    breeze = (
        f", {outdoor_wind_ms:.1f} m/s of wind included"
        if outdoor_wind_ms
        else ", no wind reading so still air assumed"
    )

    if feels_better and dry_enough:
        reason = (
            f"free cooling: outdoors feels {indoor_hci - outdoor_at:.1f} better "
            f"and is {indoor_dp - outdoor_dp:.1f} C drier at the dew point"
            f"{breeze} — opening up would help"
        )
    elif feels_better:
        reason = (
            f"free cooling: outdoors feels {indoor_hci - outdoor_at:.1f} better, "
            f"but its dew point is {outdoor_dp:.1f} C against {indoor_dp:.1f} C "
            "indoors — the breeze stops and the moisture stays"
        )
    elif dry_enough:
        reason = (
            f"free cooling: outdoors is drier, but feels {outdoor_at:.1f} "
            f"against {indoor_hci:.1f} indoors{breeze} — not enough to help"
        )
    else:
        reason = (
            f"free cooling: outdoors feels {outdoor_at:.1f} against "
            f"{indoor_hci:.1f} indoors and is no drier{breeze}"
        )

    return FreeCooling(
        advised=feels_better and dry_enough,
        outdoor_apparent_c=round(outdoor_at, 1),
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
