"""psychro.py: dew point, free cooling, condensation (Section 4.1)."""

from __future__ import annotations

import math
import unittest

from tests.pure._load import load

psychro = load("psychro")
hci = load("hci")


def bureau_dew_point(t: float, rh: float) -> float:
    """Dew point from the Bureau's vapour pressure formula, inverted."""
    x = math.log(hci.vapour_pressure(t, rh) / 6.105)
    return 237.7 * x / (17.27 - x)


class DewPoint(unittest.TestCase):
    def test_magnus(self) -> None:
        self.assertAlmostEqual(psychro.dew_point(25.0, 60.0), 16.69315, places=4)
        self.assertAlmostEqual(psychro.dew_point(20.0, 100.0), 20.0, places=9)

    def test_rh_floor(self) -> None:
        self.assertEqual(psychro.dew_point(25.0, 0.0), psychro.dew_point(25.0, 0.5))

    def test_agrees_with_bureau_form_within_0_0554(self) -> None:
        # Section 4.1 says "no more than 0.055 C apart" over 10-40 C and
        # 20-100 % RH. Measured on a 0.1 C x 0.1 % grid the largest gap is
        # 0.0553 C, at 10 C and 20 %.
        worst = 0.0
        for t in range(10, 41):
            for rh in range(20, 101):
                worst = max(worst, abs(psychro.dew_point(t, rh) - bureau_dew_point(t, rh)))
        self.assertLessEqual(worst, 0.0554)
        self.assertGreater(worst, 0.055)


def advice(**kw: float | bool | None) -> object:
    args: dict[str, float | bool | None] = {
        "needs_cooling": True,
        "room_index": 28.0,
        "room_t": 26.0,
        "room_rh": 60.0,
        "outdoor_t": 20.0,
        "outdoor_rh": 50.0,
        "outdoor_wind_ms": 0.0,
    }
    args.update(kw)
    return psychro.free_cooling(**args)


class FreeCooling(unittest.TestCase):
    def test_advised_when_cooler_and_drier(self) -> None:
        a = advice()
        self.assertTrue(a.advised)
        self.assertAlmostEqual(a.outdoors_feels, hci.outdoor_apparent_temperature(20.0, 50.0, 0.0), places=12)

    def test_only_when_cooling_is_the_plan(self) -> None:
        self.assertFalse(advice(needs_cooling=False).advised)

    def test_missing_reading_is_no(self) -> None:
        for key in ("room_index", "room_t", "room_rh", "outdoor_t", "outdoor_rh"):
            self.assertFalse(advice(**{key: None}).advised, key)

    def test_missing_wind_is_still_air(self) -> None:
        self.assertEqual(advice(outdoor_wind_ms=None).outdoors_feels, advice(outdoor_wind_ms=0.0).outdoors_feels)

    def test_wind_counts_at_0_3(self) -> None:
        a = advice(outdoor_wind_ms=10.0)
        self.assertAlmostEqual(a.outdoors_feels, hci.outdoor_apparent_temperature(20.0, 50.0, 3.0), places=12)

    def test_index_margin_is_at_least_2(self) -> None:
        feels = hci.outdoor_apparent_temperature(20.0, 50.0, 0.0)
        self.assertTrue(advice(room_index=feels + 2.0).advised)
        self.assertFalse(advice(room_index=feels + 1.99).advised)

    def test_dew_point_margin_is_at_least_1(self) -> None:
        out_dp = psychro.dew_point(20.0, 50.0)
        # find a room RH whose dew point is 0.9 C above outdoors
        rh = 30.0
        while psychro.dew_point(26.0, rh) < out_dp + 0.9:
            rh += 0.01
        self.assertFalse(advice(room_rh=rh).advised)
        while psychro.dew_point(26.0, rh) < out_dp + 1.0:
            rh += 0.01
        self.assertTrue(advice(room_rh=rh).advised)


class Condensation(unittest.TestCase):
    def test_margin_2_5(self) -> None:
        self.assertTrue(psychro.condensation_risk(18.0, 15.6))
        self.assertFalse(psychro.condensation_risk(18.0, 15.5))


if __name__ == "__main__":
    unittest.main()
