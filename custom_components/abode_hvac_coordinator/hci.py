"""The comfort index: how the room feels (Section 4.1).

HCI = T + 0.33 e - 4.00

Steadman's shade apparent temperature with the wind term at zero, in the
form the Bureau of Meteorology publishes [R2][D010], from the room's
measured temperature and humidity only. Nothing is added to it: the sun,
people and appliances reach the index only through the temperature and
humidity they cause, which the model learns (decision of 27 October 2026).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from .const import (
    BLIND_CLOSED_TRANSMISSION,
    BLIND_FULLY_BLOCKING_AT,
    INDEX_SOLVE_HIGH_C,
    INDEX_SOLVE_LOW_C,
    INDEX_SOLVE_MAX_HALVINGS,
    INDEX_SOLVE_TOLERANCE,
)

# Bureau of Meteorology saturation vapour pressure, hPa [R2]
_E0 = 6.105
_A = 17.27
_B = 237.7
# Steadman's shade apparent temperature [R2][D010]
_E_COEFF = 0.33
_OFFSET = 4.00
_WIND_COEFF = 0.70


def saturation_vapour_pressure(t_c: float) -> float:
    """Saturation vapour pressure in hPa at t_c [R2]."""
    return _E0 * math.exp(_A * t_c / (_B + t_c))


def vapour_pressure(t_c: float, rh: float) -> float:
    """Vapour pressure e in hPa from temperature (C) and RH (%) [R2][D010]."""
    return rh / 100.0 * saturation_vapour_pressure(t_c)


def rh_at_temperature(e_hpa: float, t_c: float) -> float:
    """Relative humidity (%) at t_c of air holding vapour pressure e [Der].

    The same water in the air at another temperature.
    """
    return 100.0 * e_hpa / saturation_vapour_pressure(t_c)


def blind_transmission(open_fraction: float | None) -> float:
    """The blinds' transmission tau_blind [Set].

    None means no blind, or a blind whose position is unknown: 1. At or
    below 5 % open: 0.15. Otherwise 0.15 + 0.85 x the open fraction.
    """
    if open_fraction is None:
        return 1.0
    if open_fraction <= BLIND_FULLY_BLOCKING_AT:
        return BLIND_CLOSED_TRANSMISSION
    return BLIND_CLOSED_TRANSMISSION + (1.0 - BLIND_CLOSED_TRANSMISSION) * open_fraction


def comfort_index(t_c: float, rh: float) -> float:
    """The room's comfort index, HCI = T + 0.33 e - 4.00 [D010][R2][D055]."""
    return t_c + _E_COEFF * vapour_pressure(t_c, rh) - _OFFSET


def outdoor_apparent_temperature(t_c: float, rh: float, wind_ms: float) -> float:
    """AT_out = T + 0.33 e - 0.70 w - 4.00, wind in m/s [D010][R2]."""
    return t_c + _E_COEFF * vapour_pressure(t_c, rh) - _WIND_COEFF * wind_ms - _OFFSET


def index_slope(t_c: float, rh: float) -> float:
    """dHCI/dT at fixed RH = 1 + 0.33 e 17.27 x 237.7 / (237.7 + T)^2 [Der]."""
    return 1.0 + _E_COEFF * vapour_pressure(t_c, rh) * _A * _B / (_B + t_c) ** 2


@dataclass(frozen=True, slots=True)
class SolvedTemperature:
    """A dry bulb solved from an index. held is true at either end [Set]."""

    t_c: float
    held: bool


def temperature_for_index(wanted: float, rh: float) -> SolvedTemperature:
    """The dry bulb at which the index equals wanted, at this RH [D009][Der].

    Halving from 5 C to 40 C until the index is within 0.001 of wanted.
    The index rises with T at fixed RH, and its slope on that interval is
    at most about 2.3, so 17 halvings always suffice; the limit of 60 is
    never reached (Section 4.1).
    """
    low, high = INDEX_SOLVE_LOW_C, INDEX_SOLVE_HIGH_C
    if comfort_index(low, rh) > wanted:
        return SolvedTemperature(low, True)
    if comfort_index(high, rh) < wanted:
        return SolvedTemperature(high, True)
    middle = (low + high) / 2.0
    for _ in range(INDEX_SOLVE_MAX_HALVINGS):
        middle = (low + high) / 2.0
        at_middle = comfort_index(middle, rh)
        if abs(at_middle - wanted) < INDEX_SOLVE_TOLERANCE:
            return SolvedTemperature(middle, False)
        if at_middle < wanted:
            low = middle
        else:
            high = middle
    return SolvedTemperature(middle, False)


class BandPosition(StrEnum):
    """Where an index sits against a band."""

    BELOW = "below"
    IN = "in"
    ABOVE = "above"


@dataclass(frozen=True, slots=True)
class Band:
    """A comfort band, low and high, in comfort index [D009][I4]."""

    low: float
    high: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.low) and math.isfinite(self.high)):
            raise ValueError("band edges must be finite")
        if self.low >= self.high:
            # a band of no width has no leeway, and distance is measured in
            # half-band widths; refused (decision of 27 October 2026)
            raise ValueError("band low is not below its high")

    @property
    def centre(self) -> float:
        """The goal: the centre of the band [I4]."""
        return (self.low + self.high) / 2.0

    @property
    def half_width(self) -> float:
        """Half the band's width: the unit distance is measured in [I4][Der]."""
        return (self.high - self.low) / 2.0

    def position(self, index: float) -> BandPosition:
        """Whether an index is below, in or above the band."""
        if index < self.low:
            return BandPosition.BELOW
        if index > self.high:
            return BandPosition.ABOVE
        return BandPosition.IN
