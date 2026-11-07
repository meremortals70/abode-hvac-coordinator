"""An opening made on free-cooling advice stops the unit at once, not
held by the short-cycle guard [D073], with the guard in the loop as the
coordinator runs it."""

from __future__ import annotations

import unittest

from tests.pure.test_limits import (
    CS,
    MIN,
    SK,
    T0,
    allowed,
    limits,
    opening,
    proposed,
    room,
    snap,
)


class FreeCoolingOpening(unittest.TestCase):
    def test_window_opened_on_advice_stops_a_unit_three_minutes_into_its_run(self) -> None:
        rl = limits.RoomLimits(room())
        guard = limits.HouseGuard()
        # the plan starts cooling at T0; the guard sees the start
        allowed(rl, guard, p=proposed(), s=snap(comp=CS.STOPPED), now=T0)
        allowed(rl, guard, p=proposed(), s=snap(comp=CS.RUNNING), now=T0 + MIN, running=True)
        # outdoor air turns better: free cooling is advised while it runs
        allowed(rl, guard, p=proposed(advised=True), s=snap(comp=CS.RUNNING), now=T0 + 2 * MIN, running=True)
        # the user opens the window on that advice
        r = allowed(rl, guard, p=proposed(advised=True),
                    s=snap(comp=CS.RUNNING, openings=(opening(T0 + 2.5 * MIN),)), now=T0 + 3 * MIN, running=True)
        self.assertIs(r.kind, SK.OFF, f"kind {r.kind}; changed {r.changed}; reasons {r.reasons}")


if __name__ == "__main__":
    unittest.main()
