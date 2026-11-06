"""A shared outdoor unit runs one way at a time; a reversal is a stop and a start [D016][D020]."""

from __future__ import annotations

import unittest

from tests.pure.test_limits import (
    MIN,
    SK,
    T0,
    cmd,
    limits,
    models,
    proposed,
    room,
    step,
)


class ReversalWithinMinRun(unittest.TestCase):
    def setUp(self) -> None:
        self.g = limits.HouseGuard()
        self.a = limits.RoomLimits(room("a", "shared"))
        self.b = limits.RoomLimits(room("b", "shared"))

    def both(self, minute, ka, kb, oa=(0.0, 0.0), ob=(0.0, 0.0), running=False):
        now = T0 + minute * MIN
        ha = (cmd(climate="climate.a"),) if ka is SK.COOL else None
        hb = (cmd("heat", 26.0, climate="climate.b"),) if kb is SK.HEAT else None
        ea = limits.GuardEntry(self.a, step(self.a, p=proposed(ka, heads=ha), now=now), ("shared",),
                               {"shared": running}, (), *oa)
        eb = limits.GuardEntry(self.b, step(self.b, p=proposed(kb, heads=hb), now=now), ("shared",),
                               {"shared": running}, (), *ob)
        return self.g.apply(now, [ea, eb])

    def test_other_room_heats_3_minutes_into_a_cooling_run(self) -> None:
        """a cools from minute 0; at minute 3 a's plan rests and b's heats."""
        self.both(-30, SK.REST, SK.REST)
        self.assertIs(self.both(0, SK.COOL, SK.REST)["a"].kind, SK.COOL)
        r = self.both(3, SK.REST, SK.HEAT, running=True)
        self.assertNotEqual((r["a"].kind, r["b"].kind), (SK.REST, SK.HEAT),
                            "outdoor unit reversed cool->heat 3 min into its run with no rest")

    def test_one_way_loser_is_not_kept_and_winner_starts_at_once(self) -> None:
        """a cools from minute 0; at minute 3 b wants heat and is further
        outside its band: the unit still does not reverse inside its run."""
        self.both(-30, SK.REST, SK.REST)
        self.both(0, SK.COOL, SK.REST)
        r = self.both(3, SK.COOL, SK.HEAT, oa=(0.2, 1.7), ob=(1.5, 3.0), running=True)
        self.assertNotEqual((r["a"].kind, r["b"].kind), (SK.REST, SK.HEAT),
                            "outdoor unit reversed cool->heat 3 min into its run with no rest")

    def test_inside_the_run_the_other_way_rests_to_the_end_of_run_and_rest(self) -> None:
        """3 minutes into a cooling run, b asking to heat rests for the 7
        minutes left of the run and the 5 of the rest: 720 s [D016]."""
        self.both(-30, SK.REST, SK.REST)
        self.both(0, SK.COOL, SK.REST)
        r = self.both(3, SK.REST, SK.HEAT, running=True)
        self.assertIs(r["a"].kind, SK.COOL)
        self.assertIsNot(r["b"].kind, SK.HEAT)
        self.assertIn("720 s", r["b"].reasons[-1])

class HandRunRoomDirection(unittest.TestCase):
    def test_hand_heating_room_and_planned_cooling_room(self) -> None:
        """a is heated by hand (Automatic control off, seen running in heat);
        b's plan cools. One outdoor unit cannot do both."""
        g = limits.HouseGuard()
        a = limits.RoomLimits(room("a", "shared"))
        b = limits.RoomLimits(room("b", "shared"))
        manual = models.RoomChoices(automatic_control=False)
        seen_heat = (models.HeadCommand("climate.a", "heat", None, None, None, None, None, None),)
        for minute in (-30, -29, 0, 20):
            now = T0 + minute * MIN
            ea = limits.GuardEntry(a, step(a, p=proposed(SK.REST), now=now, choices=manual), ("shared",),
                                   {"shared": minute >= 0}, seen_heat if minute >= 0 else ())
            kb = SK.COOL if minute >= 20 else SK.REST
            eb = limits.GuardEntry(b, step(b, p=proposed(kb, heads=(cmd(climate="climate.b"),) if kb is SK.COOL
                                                         else None), now=now), ("shared",), {"shared": minute >= 0})
            r = g.apply(now, [ea, eb])
        self.assertIs(r["a"].kind, SK.NONE)
        self.assertIsNot(r["b"].kind, SK.COOL, "b sent cool while a runs heat on the same outdoor unit")

    def test_a_hand_run_room_has_its_way_even_when_nearer_its_band(self) -> None:
        """b is further outside its band, but a room run by hand is never
        changed, so its way stands and b rests [D020][D089]."""
        g = limits.HouseGuard()
        a = limits.RoomLimits(room("a", "shared"))
        b = limits.RoomLimits(room("b", "shared"))
        manual = models.RoomChoices(automatic_control=False)
        seen_heat = (models.HeadCommand("climate.a", "heat", None, None, None, None, None, None),)
        for minute in (-30, -29, 0, 20):
            now = T0 + minute * MIN
            ea = limits.GuardEntry(a, step(a, p=proposed(SK.REST), now=now, choices=manual), ("shared",),
                                   {"shared": minute >= 0}, seen_heat if minute >= 0 else (), 0.0, 0.0)
            kb = SK.COOL if minute >= 20 else SK.REST
            eb = limits.GuardEntry(b, step(b, p=proposed(kb, heads=(cmd(climate="climate.b"),) if kb is SK.COOL
                                                         else None), now=now), ("shared",), {"shared": minute >= 0},
                                   (), 3.0, 4.0)
            r = g.apply(now, [ea, eb])
        self.assertIs(r["a"].kind, SK.NONE)
        self.assertIsNot(r["b"].kind, SK.COOL)
        self.assertIn("run by hand", r["b"].reasons[-1])


if __name__ == "__main__":
    unittest.main()
