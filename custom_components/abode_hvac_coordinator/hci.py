"""Home Comfort Index (HCI).

Pure functions. This module imports nothing from Home Assistant so it can be
unit tested on its own.

WHAT THE INDEX IS
-----------------
HCI is the Steadman shaded apparent temperature with wind set to zero:

    AT = Ta + 0.33 * e - 0.70 * ws - 4.00          (Steadman / BoM, shade)
    e  = (RH / 100) * 6.105 * exp(17.27 * Ta / (237.7 + Ta))

Indoors ws = 0, so:

    HCI = Ta + 0.33 * e - 4.00

Ta is indoor dry bulb in degrees C, RH is indoor relative humidity in percent,
e is water vapour pressure in hPa.

WHY AIR TEMPERATURE AND HUMIDITY ARE NOT ENOUGH
------------------------------------------------
A person in a room exchanges heat four ways: conduction and convection to the
air, evaporation of sweat, and **radiant exchange with the surfaces around
them**. Ta and RH describe the first two. They say nothing about the third.

Sun coming through glass is radiant. It warms the person and the floor and the
desk directly, and only slowly warms the air. So a room can read 24 C on a wall
sensor while someone sitting in it is uncomfortably hot, and the air-only index
will insist they are fine.

A blind reduces this. It does not remove it. A 50% blind passes roughly half
the radiant load, which is why "the blind is down" is not the same as "there is
no solar gain".

Still air matters for the same reason in reverse. Steadman's wind-zero case
still assumes the small air movement of a normally ventilated room. A room with
the fan off, the door shut and the windows closed has less than that, so both
convective and evaporative loss are worse than the formula assumes.

The corrections below are physically motivated and approximately scaled. They
are not measured against any particular room, and they are the part of this
module most likely to need adjusting against how a room actually feels.

WHY THIS FORMULA AND NOT HEAT INDEX OR HUMIDEX
----------------------------------------------
Both of those are undefined below roughly 26-27 C. The sleep band is 18-20, so
they cannot express it. Steadman AT is continuous across the whole range this
house operates in and collapses toward dry bulb as the air dries out.

The -4.00 constant is kept so the scale matches the convention the BoM feed
already uses. It is a constant offset: it shifts every HCI number by the same
amount and changes no decision. The band defaults are set against this scale.

DO NOT COMPARE HCI TO THE BoM OUTDOOR APPARENT TEMPERATURE.
The outdoor figure carries a wind term and is a different quantity.
Architecture proposal v0.3, section 5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Steadman constants.
_VAPOUR_A = 6.105
_VAPOUR_B = 17.27
_VAPOUR_C = 237.7
_E_COEFF = 0.33
_OFFSET = -4.00

# Bounds on the derived dry bulb setpoint, in degrees C. These are a hard limit
# on what will ever be asked of an air conditioner, not just solve bounds: a
# band and a humidity that together imply 45 C are a configuration error, and
# the controller clamps and records it rather than passing it to the hardware.
# Callers detect a clamp by comparing the result to these values.
SOLVE_MIN_C = 5.0
SOLVE_MAX_C = 40.0
_SOLVE_TOLERANCE = 0.001
_SOLVE_MAX_ITERATIONS = 60


def vapour_pressure_hpa(temp_c: float, relative_humidity: float) -> float:
    """Water vapour pressure in hPa from dry bulb and relative humidity."""
    saturation = _VAPOUR_A * math.exp(
        (_VAPOUR_B * temp_c) / (_VAPOUR_C + temp_c)
    )
    return (relative_humidity / 100.0) * saturation


#: How much hotter unshaded sun through glass makes a person feel, in HCI, at
#: full exposure. Solar gain through a window is on the order of several
#: hundred watts per square metre; against a person's roughly 100 W metabolic
#: output, a few degrees of felt temperature is the right order of magnitude.
#: Approximate, and the first thing to adjust if a sunlit room feels wrong.
SUN_RADIANT_HCI = 3.0

#: How much hotter a room with no air movement at all feels, in HCI, relative
#: to the lightly ventilated room Steadman's wind-zero case assumes.
STILL_AIR_HCI = 1.0

#: Steadman's wind coefficient, in HCI per metre per second. The Bureau of
#: Meteorology's non-radiation apparent temperature is
#: `AT = Ta + 0.33e - 0.70ws - 4.00`, which is exactly the index below with
#: this term restored. Indoors it is dropped because wind is zero; outdoors it
#: is the difference between a still 26 C and a breezy one.
WIND_COEFF = 0.70

#: How much hotter a running heat load in the room makes it feel, in HCI. A
#: workstation and its monitors put out a few hundred watts, most of it into
#: the person sitting in front of it.
HEAT_LOAD_HCI = 1.0

#: Cover position, in percent open, at or below which a cover is treated as
#: blocking essentially all the radiant load. Not zero: blinds leak at the
#: edges, and a fully closed blind still re-radiates what it absorbed.
COVER_FULLY_BLOCKING = 5.0

#: The fraction of radiant load a fully closed cover still passes, by
#: absorbing it and re-radiating into the room.
CLOSED_COVER_TRANSMISSION = 0.15


def radiant_load(
    *,
    direct_sun: bool | None,
    cover_position: float | None,
    has_covers: bool,
) -> float:
    """How much of the solar radiant load reaches the room, 0.0 to 1.0.

    A room with no covers, or with them fully open, takes all of it. A closed
    cover still passes some: it absorbs the energy and re-radiates it inward,
    which is why a room behind a shut blind on a hot afternoon is still warm.

    A semi-transparent blind at 50% passes roughly half. This is where the
    "the blind is down so there is no sun" assumption breaks, and it is the
    reason cover position is an input here rather than a boolean.
    """
    if not direct_sun:
        return 0.0
    if not has_covers or cover_position is None:
        return 1.0
    if cover_position <= COVER_FULLY_BLOCKING:
        return CLOSED_COVER_TRANSMISSION
    # Linear between fully closed and fully open. Cruder than the physics, and
    # closer than treating a half-closed blind as either extreme.
    open_fraction = cover_position / 100.0
    return CLOSED_COVER_TRANSMISSION + open_fraction * (
        1.0 - CLOSED_COVER_TRANSMISSION
    )


def comfort_index(
    temp_c: float,
    relative_humidity: float,
    *,
    radiant: float = 0.0,
    still_air: bool = False,
    heat_load: bool = False,
) -> float:
    """Return the HCI for indoor conditions.

    With no corrections this is Steadman shaded apparent temperature at zero
    wind. The corrections add what air temperature and humidity cannot see: sun
    on the person, no air movement, and equipment heating the room.

    `radiant` is the fraction of solar load reaching the room, from
    `radiant_load()`.
    """
    base = (
        temp_c + _E_COEFF * vapour_pressure_hpa(temp_c, relative_humidity) + _OFFSET
    )
    base += radiant * SUN_RADIANT_HCI
    if still_air:
        base += STILL_AIR_HCI
    if heat_load:
        base += HEAT_LOAD_HCI
    return base


def sensitivity_to_temperature(temp_c: float, relative_humidity: float) -> float:
    """How much the index moves per degree of dry bulb, at fixed humidity.

    Not 1.0. Cooling the air at constant relative humidity also lowers the
    vapour pressure, so a degree of cooling buys more than a degree of index.
    This is what makes the sensible route worth comparing against the latent
    one on its own terms rather than by a humidity threshold.
    """
    saturation = _VAPOUR_A * math.exp((_VAPOUR_B * temp_c) / (_VAPOUR_C + temp_c))
    # d(es)/dT for the Magnus form.
    d_saturation = saturation * (
        (_VAPOUR_B * _VAPOUR_C) / ((_VAPOUR_C + temp_c) ** 2)
    )
    return 1.0 + _E_COEFF * (relative_humidity / 100.0) * d_saturation


def sensitivity_to_humidity(temp_c: float) -> float:
    """How much the index moves per percentage point of relative humidity.

    Depends on temperature only: at a fixed dry bulb, each point of relative
    humidity adds a fixed slice of the saturation vapour pressure. This is why
    a single humidity threshold cannot decide between drying and cooling —
    65% at 22 C and 65% at 30 C move the index by very different amounts.
    """
    saturation = _VAPOUR_A * math.exp((_VAPOUR_B * temp_c) / (_VAPOUR_C + temp_c))
    return _E_COEFF * saturation / 100.0


def apparent_temperature(
    temp_c: float, relative_humidity: float, wind_ms: float = 0.0
) -> float:
    """Outdoor apparent temperature, on the same scale as the comfort index.

    The Bureau of Meteorology's non-radiation form of Steadman apparent
    temperature. `comfort_index` above is this same expression with the wind
    term dropped, which is why an outdoor figure and an indoor one can be
    compared directly: they are one formula evaluated under two conditions,
    not two indices that happen to use the same units.

    Radiation is not included. The Bureau publishes a radiation form as well,
    but it needs net absorbed radiation per unit body area, which no household
    weather feed supplies, and inventing it would make the outdoor figure less
    trustworthy than the indoor one it is being compared against.
    """
    return (
        temp_c
        + _E_COEFF * vapour_pressure_hpa(temp_c, relative_humidity)
        - WIND_COEFF * max(wind_ms, 0.0)
        + _OFFSET
    )


def dry_bulb_for_index(
    target_hci: float,
    relative_humidity: float,
    *,
    radiant: float = 0.0,
    still_air: bool = False,
    heat_load: bool = False,
) -> float:
    """Return the dry bulb setpoint that produces target_hci in these conditions.

    The corrections matter here as much as in the forward direction: a sunlit
    room needs a **lower** air temperature to feel the same as a shaded one, and
    that is exactly the setpoint the air conditioner should be asked for.

    This is the inverse of comfort_index(). It has no closed form because the
    vapour pressure term itself depends on temperature, so it is solved by
    bisection. comfort_index() rises monotonically with temperature at fixed
    humidity, which is what makes bisection safe here.

    This function is the whole reason the user never sets a setpoint. The user
    states how the room should feel; this works out what to ask the AC for.
    """
    low = SOLVE_MIN_C
    high = SOLVE_MAX_C

    def index(temp_c: float) -> float:
        return comfort_index(
            temp_c,
            relative_humidity,
            radiant=radiant,
            still_air=still_air,
            heat_load=heat_load,
        )

    # Outside the solvable range, clamp. The caller is expected to notice, by
    # comparing against SOLVE_MIN_C / SOLVE_MAX_C, and record it.
    if index(low) >= target_hci:
        return low
    if index(high) <= target_hci:
        return high

    for _ in range(_SOLVE_MAX_ITERATIONS):
        middle = (low + high) / 2.0
        value = index(middle)
        if abs(value - target_hci) < _SOLVE_TOLERANCE:
            return middle
        if value < target_hci:
            low = middle
        else:
            high = middle

    return (low + high) / 2.0


@dataclass(frozen=True, slots=True)
class ComfortBand:
    """The only comfort configuration that exists. One low, one high.

    Per room, per mode. No global, no inheritance.
    Architecture proposal v0.3, section 5.
    """

    low: float
    high: float

    def __post_init__(self) -> None:
        if self.low >= self.high:
            raise ValueError(
                f"Comfort band low ({self.low}) must be below high ({self.high})"
            )

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2.0

    def contains(self, hci: float) -> bool:
        return self.low <= hci <= self.high

    def position(self, hci: float) -> str:
        """Where a reading sits relative to the band: below, within or above."""
        if hci < self.low:
            return "below"
        if hci > self.high:
            return "above"
        return "within"
