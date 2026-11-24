"""The room's own hours, learned from its presence [D114].

The plan looks six hours ahead; taking the room as it is now over the whole
horizon cannot anticipate an arrival or a bedtime. These are the hours it keeps
instead: a share per hour of the day, learned from the room's own occupancy.
"""

from __future__ import annotations

import json
import unittest

from tests.pure._load import load

occ = load("model.occupancy")


class TheHoursAreLearned(unittest.TestCase):
    def test_an_hour_nobody_has_seen_is_unknown(self) -> None:
        self.assertIsNone(occ.OccupancyProfile().at(18.5))

    def test_a_usual_evening_and_a_usual_morning(self) -> None:
        p = occ.OccupancyProfile()
        for _ in range(14):
            p.observe(22, True)
            p.observe(10, False)
        self.assertGreater(p.at(22.0), 0.9)
        self.assertLess(p.at(10.0), 0.1)

    def test_a_change_of_routine_is_followed(self) -> None:
        p = occ.OccupancyProfile()
        for _ in range(10):
            p.observe(9, True)
        self.assertGreater(p.at(9.0), 0.9)
        for _ in range(20):  # an eighth a day: twenty days is nearly gone
            p.observe(9, False)
        self.assertLess(p.at(9.0), 0.1)

    def test_the_minutes_of_an_hour_belong_to_it(self) -> None:
        p = occ.OccupancyProfile()
        p.observe(6.9, True)
        self.assertAlmostEqual(p.at(6.0) or 0.0, 1.0)

    def test_it_survives_a_save(self) -> None:
        p = occ.OccupancyProfile()
        p.observe(22, True)
        back = occ.OccupancyProfile.from_dict(json.loads(json.dumps(p.as_dict())))
        self.assertAlmostEqual(back.at(22.0) or 0.0, p.at(22.0) or 0.0)
        self.assertIsNone(back.at(3.0))

    def test_a_stored_share_outside_the_day_is_clamped(self) -> None:
        saved = {"hours": [2.0] + [None] * 23}
        self.assertEqual(occ.OccupancyProfile.from_dict(saved).at(0.0), 1.0)
        saved = {"hours": [-3.0] + [None] * 23}
        self.assertEqual(occ.OccupancyProfile.from_dict(saved).at(0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
