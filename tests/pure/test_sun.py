"""sun.py: the sun's position, and whether it is on the glass (Section 9)."""

from __future__ import annotations

import math
import unittest
from datetime import datetime

from tests.pure._load import load

sun = load("sun")

# NREL SPA (pvlib 0.16.1 spa_python, sea level, 101325 Pa, 12 C):
# (latitude, longitude), moment, azimuth, apparent elevation
SPA = [
    ((-27.535, 152.97), "2026-01-15T07:30:00+10:00", 99.9725, 29.5271),
    ((-27.535, 152.97), "2026-06-21T12:00:00+10:00", 357.0041, 38.9873),
    ((-27.535, 152.97), "2026-10-27T15:45:00+10:00", 270.4352, 29.4739),
    ((-27.535, 152.97), "2026-03-20T06:10:00+10:00", 88.6234, 3.5120),
    ((-27.535, 152.97), "2030-12-21T09:00:00+10:00", 93.2784, 52.5154),
    ((-27.535, 152.97), "2026-09-01T17:20:00+10:00", 280.5638, 2.6311),
    ((51.5, -0.12), "2026-09-01T17:20:00+10:00", 100.5868, 18.7970),
    ((51.5, -0.12), "2026-06-21T12:00:00+10:00", 27.3287, -10.7262),
]


def angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class Position(unittest.TestCase):
    def test_against_nrel_spa(self) -> None:
        for (lat, lon), when, az, el in SPA:
            got = sun.sun_position(lat, lon, datetime.fromisoformat(when))
            self.assertLess(angle_diff(got.azimuth, az), 0.01, when)
            # tighter than the refraction itself (0.03 degrees at 30), so a
            # missing refraction correction fails; below the horizon NOAA's
            # and NREL's refraction models differ
            tol = 0.015 if el > 0.0 else 0.05
            self.assertLess(abs(got.elevation - el), tol, when)

    def test_naive_datetime_refused(self) -> None:
        with self.assertRaises(ValueError):
            sun.sun_position(-27.5, 153.0, datetime(2026, 1, 1, 12, 0))


def at(az: float, el: float) -> object:
    return sun.SunPosition(azimuth=az, elevation=el)


class OnWindow(unittest.TestCase):
    def test_cannot_say(self) -> None:
        self.assertEqual(sun.sun_on_window(None, 0.0), sun.SunOnWindow.CANNOT_SAY)
        self.assertEqual(sun.sun_on_window(at(0.0, 40.0), None), sun.SunOnWindow.CANNOT_SAY)

    def test_low_sun(self) -> None:
        self.assertEqual(sun.sun_on_window(at(0.0, 4.99), 0.0), sun.SunOnWindow.OFF)
        self.assertEqual(sun.sun_on_window(at(0.0, 5.0), 0.0), sun.SunOnWindow.ON)

    def test_off_square(self) -> None:
        self.assertEqual(sun.sun_on_window(at(90.0, 30.0), 0.0), sun.SunOnWindow.ON)
        self.assertEqual(sun.sun_on_window(at(90.01, 30.0), 0.0), sun.SunOnWindow.OFF)
        self.assertEqual(sun.sun_on_window(at(270.0, 30.0), 0.0), sun.SunOnWindow.ON)
        self.assertEqual(sun.sun_on_window(at(269.9, 30.0), 0.0), sun.SunOnWindow.OFF)
        # wraps through north
        self.assertEqual(sun.sun_on_window(at(350.0, 30.0), 20.0), sun.SunOnWindow.ON)

    def test_overhang_rule(self) -> None:
        # square-on sun at 60 degrees; cutoff atan(H/P)
        self.assertEqual(sun.sun_on_window(at(0.0, 60.0), 0.0, 0.5, 1.0), sun.SunOnWindow.ON)  # cutoff 63.4
        self.assertEqual(sun.sun_on_window(at(0.0, 60.0), 0.0, 0.6, 1.0), sun.SunOnWindow.OFF)  # cutoff 59.0

    def test_overhang_uses_profile_angle(self) -> None:
        # 40 degrees elevation, 60 off square: profile = atan(tan 40 / cos 60) = 59.2
        profile = math.degrees(math.atan(math.tan(math.radians(40.0)) / math.cos(math.radians(60.0))))
        self.assertAlmostEqual(profile, 59.21, places=2)
        h = 1.0
        p_shaded = h / math.tan(math.radians(profile - 0.5))
        p_sunny = h / math.tan(math.radians(profile + 0.5))
        self.assertEqual(sun.sun_on_window(at(60.0, 40.0), 0.0, p_shaded, h), sun.SunOnWindow.OFF)
        self.assertEqual(sun.sun_on_window(at(60.0, 40.0), 0.0, p_sunny, h), sun.SunOnWindow.ON)

    def test_overhang_derivation_by_geometry(self) -> None:
        # the ray drops P tan(elevation) / cos(off square) across the overhang
        for el in (20.0, 45.0, 70.0):
            for g in (0.0, 30.0, 75.0):
                p, h = 0.8, 1.2
                drop = p * math.tan(math.radians(el)) / math.cos(math.radians(g))
                want = sun.SunOnWindow.OFF if drop >= h else sun.SunOnWindow.ON
                self.assertEqual(sun.sun_on_window(at(g, el), 0.0, p, h), want, (el, g))

    def test_grazing_with_overhang_is_shaded(self) -> None:
        self.assertEqual(sun.sun_on_window(at(90.0, 30.0), 0.0, 0.5, 1.0), sun.SunOnWindow.OFF)

    def test_no_projection_is_no_overhang(self) -> None:
        self.assertEqual(sun.sun_on_window(at(0.0, 80.0), 0.0, 0.0, 1.0), sun.SunOnWindow.ON)
        self.assertEqual(sun.sun_on_window(at(0.0, 80.0), 0.0, None, 1.0), sun.SunOnWindow.ON)


if __name__ == "__main__":
    unittest.main()


class SolarReferenceTests(unittest.TestCase):
    """The sun as the house's solar panels measure it, where the forecast
    gives no cloud cover or UV (8 November 2026: weather.oxley_hourly has
    neither, so the sun was never measured and no sunlit step taught the
    model anything) [Prop]."""

    def test_learned_over_three_days_then_measured(self) -> None:
        ref = sun.SolarReference()
        pos = sun.SunPosition(azimuth=30.0, elevation=60.0)
        for day, kw in (("2026-11-08", 5.0), ("2026-11-09", 4.0)):
            ref.observe(day, pos, kw)
        self.assertIsNone(ref.clear_fraction(pos, 2.5))  # two days: not yet
        ref.observe("2026-11-10", pos, 4.8)
        self.assertAlmostEqual(ref.clear_fraction(pos, 2.5), 2.5 / (5.0 * 0.99 * 0.99))
        self.assertEqual(ref.clear_fraction(pos, 9.0), 1.0)

    def test_low_sun_and_no_reading_say_nothing(self) -> None:
        ref = sun.SolarReference()
        low = sun.SunPosition(azimuth=80.0, elevation=3.0)
        for day in ("2026-11-08", "2026-11-09", "2026-11-10"):
            ref.observe(day, low, 0.5)
        self.assertIsNone(ref.clear_fraction(low, 0.5))
        self.assertIsNone(ref.clear_fraction(sun.SunPosition(30.0, 60.0), None))

    def test_round_trip_drops_the_unreadable(self) -> None:
        ref = sun.SolarReference()
        ref.observe("2026-11-08", sun.SunPosition(30.0, 60.0), 5.0)
        back = sun.SolarReference.from_dict(ref.as_dict())
        self.assertEqual(back.bins, ref.bins)
        bad = sun.SolarReference.from_dict({"bins": [[1, 2, float("nan"), "d", 3], ["x"], [1, 2, 3.0, "d", 2]]})
        self.assertEqual(list(bad.bins), [(1, 2)])
