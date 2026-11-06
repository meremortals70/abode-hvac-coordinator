"""The humidity filter splits its misses between the reading and the room [R17][R21]."""

from __future__ import annotations

import random
import unittest

from tests.pure._load import load

hum = load("model.humidity")


class HumidityFollowsTheRoom(unittest.TestCase):
    def test_a_room_whose_moisture_moved_is_followed(self) -> None:
        """A sensor reporting e to 0.01 hPa; the room sits at 20 hPa for two
        days, then (an unforecast change: a window, a shower, people) sits at
        14 hPa. R comes from the reading's own second differences, and the
        rest of the miss is the room's own change, so the room is followed."""
        rnd = random.Random(1)
        f = hum.HumidityFilter.start(20.0)
        rest = [hum.MoistureMinute(outdoor_e=20.0, dry=False, cooling_rate=0.0, rebound=False)] * 5
        e = 20.0
        for _ in range(2 * 288):
            r = round(e + rnd.gauss(0.0, 0.05), 2)
            f.observe_e(r)
            f.five_minutes(rest, r, True)
        e = 14.0
        for _ in range(12 * 12):  # twelve hours
            r = round(e + rnd.gauss(0.0, 0.05), 2)
            f.observe_e(r)
            f.five_minutes(rest, r, True)
        self.assertLess(abs(f.z[hum.H_E] - e), 1.0,
                        f"12 h after the change the estimate is {f.z[hum.H_E]:.2f} hPa against a room at {e} "
                        f"(learned R {f.noise:.3f} hPa^2, P {f.p[0][0]:.4f})")


    def test_an_untrusted_step_corrects_the_moisture_only(self) -> None:
        """A step whose humidity inputs were stood in or unknown still says
        where the room's moisture is; no figure learns from it [D074]."""
        f = hum.HumidityFilter.start(20.0)
        rest = [hum.MoistureMinute(outdoor_e=20.0, dry=False, cooling_rate=0.0, rebound=False)] * 5
        for _ in range(20):
            f.observe_e(20.0)
            f.five_minutes(rest, 20.0, True)
        before_z, before_n = list(f.z), f.corrections
        f.observe_e(16.0)
        reason = f.five_minutes(rest, 16.0, False)
        self.assertIn("no figure learned", reason or "")
        self.assertLess(f.z[hum.H_E], before_z[hum.H_E] - 0.5)
        moved = hum.moisture_step(before_z, rest)
        self.assertEqual(f.z[1:], moved[1:])
        self.assertEqual(f.corrections, before_n)

if __name__ == "__main__":
    unittest.main()
