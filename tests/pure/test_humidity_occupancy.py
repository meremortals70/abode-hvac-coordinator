"""People add vapour: the moisture filter carries a source that acts only on a
step the room was occupied for, and learns how much it adds [D113]."""

from __future__ import annotations

import unittest

from tests.pure._load import load

hum = load("model.humidity")


def minute(*, occupied: bool) -> "hum.MoistureMinute":
    return hum.MoistureMinute(outdoor_e=20.0, dry=False, cooling_rate=0.0, rebound=False,
                              occupied=occupied)


class OccupancyAddsVapour(unittest.TestCase):
    def test_an_occupied_minute_adds_the_learned_source(self) -> None:
        f = hum.HumidityFilter.start(20.0)
        fig = hum.moisture_figures(f.z)
        difference = hum.rate_of_change(fig, 20.0, minute(occupied=True)) - \
            hum.rate_of_change(fig, 20.0, minute(occupied=False))
        self.assertAlmostEqual(difference, fig.occ, places=9)

    def test_five_occupied_minutes_leave_the_room_damp(self) -> None:
        f = hum.HumidityFilter.start(20.0)
        quiet = hum.moisture_step(f.z, [minute(occupied=False)] * 5)[hum.H_E]
        busy = hum.moisture_step(f.z, [minute(occupied=True)] * 5)[hum.H_E]
        self.assertGreater(busy, quiet)
        expected = hum.moisture_figures(f.z).occ * 5.0 / 60.0
        self.assertAlmostEqual(busy - quiet, expected, delta=expected * 0.15)

    def test_the_figure_learns_only_on_an_occupied_step(self) -> None:
        quiet, busy = [minute(occupied=False)] * 5, [minute(occupied=True)] * 5
        self.assertEqual(len(hum.acting(quiet)), hum.H_SIZE)
        self.assertFalse(hum.acting(quiet)[hum.H_LN_OCC])
        self.assertTrue(hum.acting(busy)[hum.H_LN_OCC])


class TheSaveFromBeforeOccupancy(unittest.TestCase):
    def test_a_six_figure_save_is_kept_and_the_seventh_added(self) -> None:
        f = hum.HumidityFilter.start(20.0)
        saved = f.as_dict()
        saved["z"] = saved["z"][:-1]
        saved["p"] = [row[:-1] for row in saved["p"][:-1]]
        back = hum.HumidityFilter.from_dict(saved)
        self.assertIsNotNone(back, "a save from before occupancy was discarded instead of migrated")
        assert back is not None
        self.assertEqual(len(back.z), hum.H_SIZE)
        for i in range(hum.H_SIZE - 1):
            self.assertAlmostEqual(back.z[i], f.z[i])
        self.assertGreater(back.p[hum.H_LN_OCC][hum.H_LN_OCC], 0.0,
                           "the new figure starts with no uncertainty, so it could never learn")


if __name__ == "__main__":
    unittest.main()
