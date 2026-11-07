"""The humidity filter's figures are held in [ln 1e-6, ln 1e3] as the heat
filter's are: a stored figure outside is refused, and a correction cannot
take one outside."""

from __future__ import annotations

import math
import unittest

from tests.pure._load import load

hum = load("model.humidity")
const = load("const")

LO, HI = const.FIGURE_LOG_RANGE


class HumidityFigureRange(unittest.TestCase):
    def test_a_stored_exchange_figure_outside_the_range_is_refused(self) -> None:
        f = hum.HumidityFilter.start(15.0)
        data = f.as_dict()
        data["z"][hum.H_LN_H] = math.log(1e5)  # h = 100,000 per hour
        self.assertIsNone(hum.HumidityFilter.from_dict(data), "stored ln h of ln 1e5 was accepted")

    def test_a_correction_holds_every_figure_in_the_range(self) -> None:
        f = hum.HumidityFilter.start(15.0)
        f.z[hum.H_LN_REB] = HI + 2.0  # e.g. carried in from a stored file
        f.observe_e(15.0)
        f.observe_e(15.01)
        minutes = [hum.MoistureMinute(15.0, False, 0.0, False)] * 5
        f.five_minutes(minutes, 15.0, True)
        for i in (hum.H_LN_H, hum.H_LN_DRY, hum.H_LN_COOL, hum.H_LN_REB):
            self.assertLessEqual(f.z[i], HI + 1e-9, f"figure {i} at ln {f.z[i]:.2f}, above ln 1e3 = {HI:.2f}")


    def test_the_minute_step_is_exact_for_a_fast_exchange(self) -> None:
        """At h = 200 or 1,000 per hour a straight-line minute diverges; the
        exact step settles on the outdoor moisture, as the forecast's does
        [Der]."""
        for h in (200.0, 1000.0):
            z = hum.HumidityFilter.start(15.0).z
            z[hum.H_LN_H] = math.log(h)
            z[hum.H_Q] = 0.0
            e = hum.moisture_step(z, [hum.MoistureMinute(25.0, False, 0.0, False)] * 5)[hum.H_E]
            self.assertAlmostEqual(e, 25.0, delta=0.01, msg=f"h {h}: e {e}")
        z = hum.HumidityFilter.start(15.0).z
        z[hum.H_LN_H], z[hum.H_Q] = math.log(0.5), 0.0
        e = hum.moisture_step(z, [hum.MoistureMinute(25.0, False, 0.0, False)] * 5)[hum.H_E]
        self.assertAlmostEqual(e, 25.0 - 10.0 * math.exp(-0.5 * 5 / 60.0), places=9)

if __name__ == "__main__":
    unittest.main()
