"""hci.py: the comfort index (Section 4.1)."""

from __future__ import annotations

import unittest

from tests.pure._load import load

hci = load("hci")
const = load("const")


class VapourPressure(unittest.TestCase):
    def test_bureau_form(self) -> None:
        # 6.105 exp(17.27 x 25 / 262.7) = 31.583 hPa [R2]
        self.assertAlmostEqual(hci.saturation_vapour_pressure(25.0), 31.58296, places=4)
        self.assertAlmostEqual(hci.vapour_pressure(25.0, 60.0), 18.94978, places=4)

    def test_same_water_at_another_temperature(self) -> None:
        e = hci.vapour_pressure(25.0, 60.0)
        self.assertAlmostEqual(hci.rh_at_temperature(e, 25.0), 60.0, places=9)
        # colder air holding the same water is more humid
        self.assertGreater(hci.rh_at_temperature(e, 20.0), 60.0)


class ComfortIndex(unittest.TestCase):
    def test_shade_apparent_temperature_and_nothing_added(self) -> None:
        # T + 0.33 e - 4.00 from measured temperature and humidity only:
        # nothing is added for sun, air movement or appliances [D010]
        for t, rh in ((25.0, 60.0), (18.0, 30.0), (32.0, 90.0)):
            e = hci.vapour_pressure(t, rh)
            self.assertAlmostEqual(hci.comfort_index(t, rh), t + 0.33 * e - 4.0, places=12)
        self.assertAlmostEqual(hci.comfort_index(25.0, 60.0), 27.25343, places=4)

    def test_index_takes_only_temperature_and_humidity(self) -> None:
        import inspect
        self.assertEqual(list(inspect.signature(hci.comfort_index).parameters), ["t_c", "rh"])
        self.assertEqual(list(inspect.signature(hci.temperature_for_index).parameters), ["wanted", "rh"])
        self.assertFalse(hasattr(hci, "IndexCorrections"))
        self.assertFalse(hasattr(hci, "sun_share"))

    def test_humid_air_counts_as_warmer(self) -> None:
        self.assertGreater(hci.comfort_index(25.0, 70.0), hci.comfort_index(25.0, 50.0))

    def test_outdoor_apparent_temperature(self) -> None:
        # 30 C, 50 %, 2 m/s: 30 + 0.33 x 21.237 - 1.4 - 4.0
        self.assertAlmostEqual(hci.outdoor_apparent_temperature(30.0, 50.0, 2.0), 31.57738, places=4)

    def test_26_at_60_is_above_the_default_occupied_band(self) -> None:
        band = hci.Band(*const.DEFAULT_OCCUPIED_BAND)
        self.assertEqual(band.position(hci.comfort_index(26.0, 60.0)), hci.BandPosition.ABOVE)

    def test_default_bands_mean_the_same_air(self) -> None:
        # occupied and precool 22 to 24 C, sleep 19.5 to 22 C, at 60 % (Section 7)
        for band, (lo, hi) in ((const.DEFAULT_OCCUPIED_BAND, (21.83, 24.08)),
                               (const.DEFAULT_PRECOOL_BAND, (21.83, 24.08)),
                               (const.DEFAULT_SLEEP_BAND, (19.52, 21.83))):
            self.assertAlmostEqual(hci.temperature_for_index(band[0], 60.0).t_c, lo, places=2)
            self.assertAlmostEqual(hci.temperature_for_index(band[1], 60.0).t_c, hi, places=2)


class BlindTransmission(unittest.TestCase):
    def test_blind_transmission(self) -> None:
        self.assertEqual(hci.blind_transmission(None), 1.0)
        self.assertEqual(hci.blind_transmission(0.05), 0.15)
        self.assertEqual(hci.blind_transmission(0.0), 0.15)
        self.assertAlmostEqual(hci.blind_transmission(1.0), 1.0, places=12)
        self.assertEqual(hci.blind_transmission(0.04), 0.15)
        self.assertAlmostEqual(hci.blind_transmission(0.06), 0.15 + 0.85 * 0.06, places=12)


class Slope(unittest.TestCase):
    def test_slope_matches_numerical_derivative(self) -> None:
        for t in (5.0, 18.0, 27.0, 40.0):
            for rh in (20.0, 60.0, 100.0):
                h = 1e-5
                num = (hci.comfort_index(t + h, rh) - hci.comfort_index(t - h, rh)) / (2 * h)
                self.assertAlmostEqual(hci.index_slope(t, rh), num, places=6)

    def test_slope_bound_of_section_4_1(self) -> None:
        # above 1 everywhere, at most 2.29 on 5-40 C (at 40 C and 100 %)
        self.assertAlmostEqual(hci.index_slope(40.0, 100.0), 2.29036, places=4)
        for t10 in range(50, 401):
            for rh in (0.0, 50.0, 100.0):
                s = hci.index_slope(t10 / 10.0, rh)
                self.assertGreaterEqual(s, 1.0)
                self.assertLessEqual(s, 2.2904)


class TemperatureForIndex(unittest.TestCase):
    def test_solves_within_tolerance(self) -> None:
        for wanted in (18.0, 22.5, 25.5, 30.0, 40.0):
            for rh in (30.0, 60.0, 90.0):
                got = hci.temperature_for_index(wanted, rh)
                self.assertFalse(got.held)
                self.assertLess(abs(hci.comfort_index(got.t_c, rh) - wanted), 0.001)

    def test_seventeen_halvings_suffice(self) -> None:
        # 17 halvings leave the midpoint within 35 / 2^18 C of the answer;
        # at a slope of at most 2.29 that is within 0.0004 of the index
        self.assertLess(35.0 / 2**18 * hci.index_slope(40.0, 100.0), 0.001)

    def test_held_at_either_end(self) -> None:
        low = hci.temperature_for_index(-20.0, 50.0)
        self.assertEqual((low.t_c, low.held), (5.0, True))
        high = hci.temperature_for_index(90.0, 50.0)
        self.assertEqual((high.t_c, high.held), (40.0, True))



class BandTests(unittest.TestCase):
    def test_centre_half_width_position(self) -> None:
        band = hci.Band(24.0, 27.0)
        self.assertEqual(band.centre, 25.5)
        self.assertEqual(band.half_width, 1.5)
        self.assertEqual(band.position(23.9), hci.BandPosition.BELOW)
        self.assertEqual(band.position(24.0), hci.BandPosition.IN)
        self.assertEqual(band.position(27.0), hci.BandPosition.IN)
        self.assertEqual(band.position(27.1), hci.BandPosition.ABOVE)

    def test_inverted_band_refused(self) -> None:
        with self.assertRaises(ValueError):
            hci.Band(27.0, 24.0)
        with self.assertRaises(ValueError):
            hci.Band(float("nan"), 24.0)
        with self.assertRaises(ValueError):
            hci.Band(24.0, 24.0)


if __name__ == "__main__":
    unittest.main()
