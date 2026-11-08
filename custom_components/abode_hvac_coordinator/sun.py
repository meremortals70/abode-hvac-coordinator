"""Is the sun on the glass? (Section 9) [D023]

The sun's position uses NOAA's solar calculation (the equations of its
solar calculator spreadsheet, after Meeus), with its approximation for
atmospheric refraction, because light reaches the glass along the
apparent, refracted direction. Accuracy is checked in the tests against
NREL's solar position algorithm.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .const import (
    SOLAR_REFERENCE_AZIMUTH_BIN_DEG,
    SOLAR_REFERENCE_DAILY_KEEP,
    SOLAR_REFERENCE_ELEVATION_BIN_DEG,
    SOLAR_REFERENCE_MIN_DAYS,
    SUN_MAX_OFF_SQUARE_DEG,
    SUN_MIN_ELEVATION_DEG,
)


@dataclass(frozen=True, slots=True)
class SunPosition:
    """Azimuth clockwise from north, and apparent elevation, in degrees."""

    azimuth: float
    elevation: float


def _refraction_deg(elevation: float) -> float:
    """NOAA's atmospheric refraction approximation, in degrees."""
    if elevation > 85.0:
        return 0.0
    te = math.tan(math.radians(elevation))
    if elevation > 5.0:
        arcsec = 58.1 / te - 0.07 / te**3 + 0.000086 / te**5
    elif elevation > -0.575:
        arcsec = 1735.0 + elevation * (-518.2 + elevation * (103.4 + elevation * (-12.79 + elevation * 0.711)))
    else:
        arcsec = -20.772 / te
    return arcsec / 3600.0


def sun_position(latitude: float, longitude: float, when: datetime) -> SunPosition:
    """The sun's azimuth and apparent elevation for an aware moment.

    Raises ValueError for a naive datetime: a moment without a time zone
    cannot place the sun.
    """
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("when must be timezone-aware")
    utc = when.astimezone(UTC)
    jd = utc.timestamp() / 86400.0 + 2440587.5
    jc = (jd - 2451545.0) / 36525.0

    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360.0
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    ecc = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    m_r = math.radians(m)
    centre = (
        math.sin(m_r) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + math.sin(2 * m_r) * (0.019993 - 0.000101 * jc)
        + math.sin(3 * m_r) * 0.000289
    )
    true_long = l0 + centre
    omega = math.radians(125.04 - 1934.136 * jc)
    app_long = true_long - 0.00569 - 0.00478 * math.sin(omega)
    mean_obliq = 23.0 + (26.0 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60.0) / 60.0
    obliq = math.radians(mean_obliq + 0.00256 * math.cos(omega))
    decl = math.asin(math.sin(obliq) * math.sin(math.radians(app_long)))

    y = math.tan(obliq / 2.0) ** 2
    l0_r = math.radians(l0)
    eq_time = 4.0 * math.degrees(
        y * math.sin(2 * l0_r)
        - 2 * ecc * math.sin(m_r)
        + 4 * ecc * y * math.sin(m_r) * math.cos(2 * l0_r)
        - 0.5 * y * y * math.sin(4 * l0_r)
        - 1.25 * ecc * ecc * math.sin(2 * m_r)
    )
    minutes = utc.hour * 60.0 + utc.minute + utc.second / 60.0 + utc.microsecond / 6e7
    true_solar = (minutes + eq_time + 4.0 * longitude) % 1440.0
    hour_angle = math.radians(true_solar / 4.0 - 180.0)

    lat = math.radians(latitude)
    cos_zen = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    zenith = math.acos(max(-1.0, min(1.0, cos_zen)))
    geometric = 90.0 - math.degrees(zenith)
    azimuth = (
        math.degrees(
            math.atan2(
                math.sin(hour_angle),
                math.cos(hour_angle) * math.sin(lat) - math.tan(decl) * math.cos(lat),
            )
        )
        + 180.0
    ) % 360.0
    return SunPosition(azimuth=azimuth, elevation=geometric + _refraction_deg(geometric))


class SunOnWindow(StrEnum):
    """Whether the sun is on a window's glass."""

    ON = "on"
    OFF = "off"
    CANNOT_SAY = "cannot_say"


def off_square_deg(azimuth: float, facing: float) -> float:
    """How far the sun is off the way the window faces, 0 to 180 degrees."""
    diff = abs((azimuth - facing + 180.0) % 360.0 - 180.0)
    return diff


def sun_on_window(
    position: SunPosition | None,
    facing: float | None,
    overhang_projection_m: float | None = None,
    overhang_height_m: float | None = None,
) -> SunOnWindow:
    """Whether the sun is on the glass of a window [D023].

    Unknown position or direction: cannot say. Below 5 degrees elevation
    [Set], or more than 90 degrees off square [Der]: not on the glass.
    With an overhang (a projection above zero and a height), the glass is
    shaded when the sun's profile angle is at or above the cutoff
    atan(height / projection); the profile angle is atan(tan(elevation) /
    cos(angle off square)) [Der], computed as atan2 so that it stays
    defined at 90 degrees off square. The overhang is taken as wide enough
    that sun cannot pass its ends [NYE].
    """
    if position is None or facing is None:
        return SunOnWindow.CANNOT_SAY
    if position.elevation < SUN_MIN_ELEVATION_DEG:
        return SunOnWindow.OFF
    gamma = off_square_deg(position.azimuth, facing)
    if gamma > SUN_MAX_OFF_SQUARE_DEG:
        return SunOnWindow.OFF
    if (
        overhang_projection_m is not None
        and overhang_height_m is not None
        and overhang_projection_m > 0.0
    ):
        cutoff = math.atan2(overhang_height_m, overhang_projection_m)
        beta = math.radians(position.elevation)
        profile = math.atan2(math.sin(beta), math.cos(beta) * math.cos(math.radians(gamma)))
        if profile >= cutoff:
            return SunOnWindow.OFF
    return SunOnWindow.ON


@dataclass(slots=True)
class SolarReference:
    """The sun as the house's solar panels measure it [Prop].

    For each place of the sun in the sky, the most the panels have made
    there, learned from the house's own readings and fading by 1 % a day.
    The solar power now over that most is the share of a clear sky the sun
    gives now: what the forecast's cloud cover gives where it has one."""

    bins: dict[tuple[int, int], tuple[float, str, int]] = field(default_factory=dict)

    @staticmethod
    def _key(position: SunPosition) -> tuple[int, int]:
        return (round(position.elevation / SOLAR_REFERENCE_ELEVATION_BIN_DEG),
                round(position.azimuth / SOLAR_REFERENCE_AZIMUTH_BIN_DEG) % round(360 / SOLAR_REFERENCE_AZIMUTH_BIN_DEG))

    def observe(self, day: str, position: SunPosition | None, solar_kw: float | None) -> None:
        """Every cycle: learn the most made with the sun where it is."""
        if position is None or solar_kw is None or position.elevation < SUN_MIN_ELEVATION_DEG:
            return
        key = self._key(position)
        peak, last, days = self.bins.get(key, (0.0, "", 0))
        if last != day:
            peak, days = peak * SOLAR_REFERENCE_DAILY_KEEP, days + 1
        self.bins[key] = (max(peak, max(solar_kw, 0.0)), day, days)

    def clear_fraction(self, position: SunPosition | None, solar_kw: float | None) -> float | None:
        """The share of a clear sky now, 0 to 1; None where it cannot be
        told: the sun low or down, no solar reading, or fewer than three
        days learned with the sun in this place."""
        if position is None or solar_kw is None or position.elevation < SUN_MIN_ELEVATION_DEG:
            return None
        peak, _, days = self.bins.get(self._key(position), (0.0, "", 0))
        if days < SOLAR_REFERENCE_MIN_DAYS or peak <= 0.0:
            return None
        return min(max(solar_kw / peak, 0.0), 1.0)

    def as_dict(self) -> dict[str, Any]:
        """What Memory keeps."""
        return {"bins": [[e, a, peak, day, days] for (e, a), (peak, day, days) in self.bins.items()]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SolarReference:
        """Restore; anything not readable or not finite is dropped [D031]."""
        out = cls()
        for row in data.get("bins", []) if isinstance(data.get("bins"), list) else []:
            try:
                e, a, peak, day, days = row
                peak = float(peak)
                if math.isfinite(peak) and peak >= 0.0 and int(days) > 0:
                    out.bins[(int(e), int(a))] = (peak, str(day), int(days))
            except (TypeError, ValueError):
                continue
        return out
