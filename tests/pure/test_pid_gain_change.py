"""The PID across a change of gains: only the feedback part is held (eleventh review, 6 November 2026)."""

from __future__ import annotations

import unittest

from tests.pure.test_control import G, pid, step


class NewPlanNewGains(unittest.TestCase):
    def test_a_new_plans_rate_reaches_the_output(self) -> None:
        """The gains change at nearly every replan, as the figures move at
        every filter correction. The new plan's rate passes straight through
        to the output; only the feedback part is held. Room exactly on its
        path (e = 0) throughout."""
        p = pid.Pid()
        step(p, 0, u_plan=1.0)
        self.assertAlmostEqual(step(p, 1, u_plan=1.0).u, 1.0)
        g2 = pid.Gains(kp=G.kp * 1.001, ti=G.ti, td=G.td)  # a replan: the figures moved a little
        r = step(p, 2, g=g2, u_plan=4.0)  # the new plan: pull down at 4 C/h
        later = step(p, 10, g=g2, u_plan=4.0)
        self.assertAlmostEqual(later.u, 4.0, places=2,
                               msg=f"new plan asks 4.0 C/h, room on its path; the PID sends {r.u:.2f} then "
                                   f"{later.u:.2f} (integral term {later.i:+.2f})")


if __name__ == "__main__":
    unittest.main()
