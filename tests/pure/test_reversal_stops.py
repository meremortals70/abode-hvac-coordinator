"""A stop that is not the user's is held to the 10-minute run, with or without another room wanting the other way [D016]."""

from __future__ import annotations

import unittest
from typing import Any, ClassVar

from tests.pure.test_limits import (
    BR,
    MIN,
    SK,
    T0,
    cmd,
    comfort,
    econ,
    limits,
    proposed,
    room,
    step,
)


class AStopIsHeldThroughAReversal(unittest.TestCase):
    """a cools from minute 0. At minute 3 a's own step becomes OFF for a
    reason that is not the user's (the room went empty, Enforced power asked
    for off); without another room the guard holds that stop to the end of
    the 10-minute run. With b on the same outdoor unit asking to heat, it
    holds it too: a reversal is a stop and a start [D016]."""

    def run_case(self, b_kind: SK, a_step_kwargs: dict) -> dict:
        g = limits.HouseGuard()
        a = limits.RoomLimits(room("a", "shared"))
        b = limits.RoomLimits(room("b", "shared"))

        def both(minute: int, ka_kwargs: dict, kb: SK, running: bool) -> dict:
            now = T0 + minute * MIN
            ea = limits.GuardEntry(a, step(a, now=now, **ka_kwargs), ("shared",), {"shared": running}, ())
            hb = (cmd("heat", 26.0, climate="climate.b"),) if kb is SK.HEAT else None
            eb = limits.GuardEntry(b, step(b, p=proposed(kb, heads=hb), now=now), ("shared",),
                                   {"shared": running}, (), 2.0, 3.0)
            return g.apply(now, [ea, eb])

        both(-30, {"p": proposed(SK.REST)}, SK.REST, False)
        both(0, {"p": proposed(SK.COOL, heads=(cmd(climate="climate.a"),))}, SK.REST, False)
        return both(3, a_step_kwargs, b_kind, True)

    EMPTY: ClassVar[dict[str, Any]] = {"p": proposed(SK.OFF), "c": comfort(BR.UNOCCUPIED, off=True)}
    ENFORCED: ClassVar[dict[str, Any]] = {"p": proposed(SK.COOL, heads=(cmd(climate="climate.a"),)), "e": econ(turn_off=True)}

    def test_control_empty_room_stop_is_held_without_a_reversal(self) -> None:
        r = self.run_case(SK.REST, self.EMPTY)
        self.assertIs(r["a"].kind, SK.COOL)  # held: 3 minutes into its 10-minute run

    def test_empty_room_stop_is_held_when_another_room_wants_heat(self) -> None:
        r = self.run_case(SK.HEAT, self.EMPTY)
        self.assertIsNot(r["b"].kind, SK.HEAT)
        self.assertIs(r["a"].kind, SK.COOL,
                      "the cooling run was stopped 3 minutes in because another room wanted heat")
        self.assertTrue(r["a"].held_off)  # the OFF is held back, as the spoken warning needs [D077]

    def test_enforced_stop_is_held_when_another_room_wants_heat(self) -> None:
        self.assertIs(self.run_case(SK.REST, self.ENFORCED)["a"].kind, SK.COOL)  # control
        self.assertIs(self.run_case(SK.HEAT, self.ENFORCED)["a"].kind, SK.COOL,
                      "the cooling run was stopped 3 minutes in because another room wanted heat")



class ReversalAfterStartUp(unittest.TestCase):
    """After a restart the shared unit is seen cooling (age unknown). a keeps
    asking to cool; b, further outside its band, asks to heat. The settled
    rule: a reversal is a stop and a start, so heating waits at least the
    5-minute rest after the cooling stops."""

    def test_a_reversal_after_a_restart_still_rests(self) -> None:
        g = limits.HouseGuard()
        a = limits.RoomLimits(room("a", "shared"))
        b = limits.RoomLimits(room("b", "shared"))
        out = []
        for minute in range(12):
            now = T0 + minute * MIN
            ka = proposed(SK.COOL, heads=(cmd(climate="climate.a"),)) if minute == 0 else proposed(SK.REST)
            ea = limits.GuardEntry(a, step(a, p=ka, now=now), ("shared",), {"shared": True}, (), 0.0, 0.1)
            kb = SK.REST if minute == 0 else SK.HEAT
            hb = (cmd("heat", 26.0, climate="climate.b"),) if kb is SK.HEAT else None
            eb = limits.GuardEntry(b, step(b, p=proposed(kb, heads=hb), now=now), ("shared",),
                                   {"shared": True}, (), 2.0, 3.0)
            r = g.apply(now, [ea, eb])
            out.append((minute, r["a"].kind, r["b"].kind))
        heat_minutes = [m for m, ka, kb in out if kb is SK.HEAT]
        stop_minute = min(m for m, ka, kb in out if ka is not SK.COOL)
        self.assertGreaterEqual(heat_minutes[0] - stop_minute, 5,
                                f"heating began {heat_minutes[0] - stop_minute} min after cooling stopped: {out}")


if __name__ == "__main__":
    unittest.main()
