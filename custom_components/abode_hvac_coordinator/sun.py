"""Sun geometry.

Pure. No Home Assistant imports.

Answers one question: **is the sun on this room's windows right now?**

WHY THIS EXISTS
---------------
Covers are the cheapest actuator, but only when there is something for them to
block. The obvious signal — indoor light level — does not work. A
semi-transparent blind reads bright when it is fully closed, so illuminance
reports nothing to block at exactly the moment the blind is already blocking.

The signal that does work is geometry: where the sun is, against which way the
window faces. That needs no sensor in the room and no assumption about the
blind's material.

WHAT IT NEEDS
-------------
The direction the window faces and the sun's position, which Home Assistant
already publishes as `sun.sun` from the latitude and longitude configured in
its own settings. Nothing needs to be told to this integration twice.

**Overhang shading is separate and matters.** A window under a soffit, an
eave, a balcony or a verandah is shaded whenever the sun is high enough that
the overhang's shadow reaches past the bottom of the glass. In a subtropical
summer that is most of the middle of the day, which is exactly when the sun
would otherwise be worst. A model that ignores it will insist a north-facing
window is in full sun at noon when it is completely shaded.

Two numbers describe it: how far the overhang projects from the wall, and how
far it sits above the bottom of the window. Both are measured once with a tape
measure and never change.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

#: Half-width of the acceptance angle either side of the window normal, in
#: degrees. Beyond about 90 degrees off-normal the sun is behind the wall and
#: cannot reach the glass at all, so 90 is geometry rather than a preference.
ACCEPTANCE_HALF_ANGLE = 90.0

#: Below this elevation the sun is on the horizon: weak, and usually behind
#: something. Above zero rather than at it, because a sun a degree up is
#: contributing nothing worth moving a blind for.
MIN_ELEVATION = 5.0

#: Compass directions offered at setup, with the azimuth each faces.
#: Azimuth is degrees clockwise from north, matching what `sun.sun` reports.
WINDOW_DIRECTIONS: dict[str, float] = {
    "north": 0.0,
    "north_east": 45.0,
    "east": 90.0,
    "south_east": 135.0,
    "south": 180.0,
    "south_west": 225.0,
    "west": 270.0,
    "north_west": 315.0,
}


def angle_between(first: float, second: float) -> float:
    """Smallest angle between two compass bearings, 0 to 180 degrees."""
    difference = abs(first - second) % 360.0
    return 360.0 - difference if difference > 180.0 else difference


def shading_elevation(
    overhang_projection_m: float | None,
    overhang_height_m: float | None,
) -> float | None:
    """Sun elevation above which an overhang fully shades the glass.

    Geometry: the shadow of an overhang projecting `p` from the wall reaches
    down the wall by `p * tan(elevation)`. Once that exceeds the height of the
    overhang above the bottom of the glass, the whole window is in shade.

        elevation = atan(height / projection)

    Returns None when the window has no overhang described, which means no
    shading is applied rather than assuming any.

    This is the head-on case. A window lit obliquely is shaded at a higher
    elevation than this, because the effective projection shortens — handled
    by the caller, which knows the horizontal angle.
    """
    if not overhang_projection_m or overhang_height_m is None:
        return None
    if overhang_projection_m <= 0:
        return None
    return math.degrees(math.atan(overhang_height_m / overhang_projection_m))


def sun_on_window(
    sun_azimuth: float | None,
    sun_elevation: float | None,
    window_azimuth: float | None,
    *,
    overhang_projection_m: float | None = None,
    overhang_height_m: float | None = None,
) -> bool | None:
    """Whether the sun is reaching the glass of a window facing `window_azimuth`.

    None means the question cannot be answered — no sun position, or no window
    direction configured — and the caller must treat that as "do not move the
    covers" rather than as either answer.
    """
    if sun_azimuth is None or sun_elevation is None or window_azimuth is None:
        return None
    if sun_elevation < MIN_ELEVATION:
        return False

    horizontal = angle_between(sun_azimuth, window_azimuth)
    if horizontal > ACCEPTANCE_HALF_ANGLE:
        return False

    cutoff = shading_elevation(overhang_projection_m, overhang_height_m)
    if cutoff is None:
        return True

    # Obliquely lit, the overhang projects less usefully, so it shades only at
    # higher elevations. cos(0) = 1 head-on; approaching 90 degrees off-normal
    # the overhang stops shading at all.
    effective = math.cos(math.radians(horizontal))
    if effective <= 1e-6:
        return True
    return sun_elevation < math.degrees(
        math.atan(math.tan(math.radians(cutoff)) / effective)
    )


def azimuth_for_direction(direction: str | None) -> float | None:
    """Azimuth for a configured compass direction, or None if unset."""
    if direction is None:
        return None
    return WINDOW_DIRECTIONS.get(direction)


def solar_position(
    latitude: float, longitude: float, when: datetime
) -> tuple[float, float]:
    """The sun's azimuth and elevation, in degrees, at a place and time.

    0.9.0, DR-053. Pure: the NOAA general solar position equations, good to a
    degree or so, which is all a window facing one of eight compass directions
    needs. Azimuth is clockwise from north. `when` must be timezone-aware.
    The coordinator reads the sun's position from `sun.sun` for the present
    moment; this is for the moments the room's projection has not reached yet.
    """
    utc = when.astimezone(UTC)
    day = utc.timetuple().tm_yday
    hour = utc.hour + utc.minute / 60.0 + utc.second / 3600.0
    gamma = 2.0 * math.pi / 365.0 * (day - 1 + (hour - 12.0) / 24.0)
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    solar_minutes = hour * 60.0 + equation_of_time + 4.0 * longitude
    hour_angle = math.radians(solar_minutes / 4.0 - 180.0)
    lat = math.radians(latitude)
    sin_elevation = math.sin(lat) * math.sin(declination) + math.cos(
        lat
    ) * math.cos(declination) * math.cos(hour_angle)
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, sin_elevation))))
    azimuth = (
        math.degrees(
            math.atan2(
                math.sin(hour_angle),
                math.cos(hour_angle) * math.sin(lat)
                - math.tan(declination) * math.cos(lat),
            )
        )
        + 180.0
    ) % 360.0
    return azimuth, elevation
