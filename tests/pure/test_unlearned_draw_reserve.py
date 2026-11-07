"""An Enforced room whose outdoor unit's draw is not yet learned has no
ceiling and keeps running until the battery is down to its reserve, when it
is turned off (Jason, 6 November 2026), whether or not the rest of the
house has been measured: here the unit has run for the last 15 minutes."""

from __future__ import annotations

import unittest
from datetime import timedelta

from tests.pure.test_economics import T0, eco, room


class UnlearnedDrawReserve(unittest.TestCase):
    def evaluate(self, minutes_running: int) -> object:
        at_reserve = eco.HousePower(10.0, 10.0, 1.0, None, True, True)  # 1 kWh charge, 1 kWh reserve
        b = eco.PowerBudget()
        for m in range(15):
            running = frozenset({"ou1"}) if 15 - m <= minutes_running else frozenset()
            b.loads.add(T0 - timedelta(minutes=15 - m), 2.0, 0.0, running)
        return b.evaluate(now=T0, window_end=T0 + timedelta(hours=2), house=at_reserve,
                          rooms=[room(running=True)], draw_for_rate={"r1": lambda _r: None},
                          running_draw={("ou1", "r1"): None}, solar_slices=None, worst_clear_sky=1.0)["r1"]

    def test_turned_off_at_the_reserve_after_ten_minutes_running(self) -> None:
        out = self.evaluate(10)
        self.assertTrue(out[1], f"{out}")

    def test_turned_off_at_the_reserve_after_fifteen_minutes_running(self) -> None:
        out = self.evaluate(15)
        self.assertTrue(out[1], f"turn_off is {out[1]}: {out}")


if __name__ == "__main__":
    unittest.main()
