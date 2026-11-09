"""Regression for the short cycling on a whole-degree unit (#1).

The limit "never heat after a cooling cycle, never cool after a heating
cycle" [D102] was held across the whole six-hour plan. After a cooling run
the plan could not heat until the forecast had the room below its band, and
after a heating run it could not cool until the room had risen above it. The
room crossed both edges and the compressor started 10-13 times a day.

The rule is now decided from a rest forecast: after a run the plan forecasts
the room with the unit resting, and turns it the other way only if that
forecast does not bring the room back inside its band. An overshoot the room's
own drift will undo is left to drift back. On the settled simulated day the
compressor starts 3-5 times and the room is inside the band at least 90 % of
the day.

This is the slow whole-loop simulation (tests/sim/intesis_loop.py); the pure
tests cover the rule itself, and this one covers the room's whole loop, which
is where the sawtooth appeared.
"""

from __future__ import annotations

import unittest

from tests.sim.intesis_loop import run


class ShortCyclingOnAWholeDegreeUnit(unittest.TestCase):
    """Two simulated days through the Intesis unit, compressor not reported
    (D101); the second day is the settled one."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.days = run(days_n=2, known=False)

    def test_the_settled_day_starts_at_most_eight_times(self) -> None:
        # 5 on the fixed build; 10-13 with D102 held across the plan
        self.assertLessEqual(self.days[1]["starts"], 8)

    def test_the_settled_day_is_inside_the_band_at_least_86_per_cent(self) -> None:
        inside = self.days[1]["inside"] / self.days[1]["n"]
        self.assertGreaterEqual(inside, 0.85)


if __name__ == "__main__":
    unittest.main()
