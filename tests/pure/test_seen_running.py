"""A head in a running mode with its compressor unknown counts as running
wherever the component decides what to do; the model and the draw learner
use only a known state [D028][D050][D074]."""

from __future__ import annotations

import dataclasses
import unittest

from tests.pure._load import load
from tests.pure.sim_room import SimRoom
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

unit = load("model.unit")


class SeenRunning(unittest.TestCase):
    def test_the_rule(self) -> None:
        h = SimRoom().snapshot().heads[0]
        for mode, comp, want in (("cool", CS.RUNNING, True), ("cool", CS.UNKNOWN, True), ("heat", CS.UNKNOWN, True),
                                 ("dry", CS.UNKNOWN, True), ("fan_only", CS.UNKNOWN, False),
                                 ("off", CS.UNKNOWN, False), ("cool", CS.STOPPED, False)):
            self.assertIs(unit.seen_running(dataclasses.replace(h, mode=mode, compressor=comp)), want, (mode, comp))

    def test_a_door_opened_on_a_unit_running_unseen_keeps_its_grace(self) -> None:
        """The living room's case: no frequency sensor, no hvac_action. A door
        opened while it cools is given its grace, as a unit seen running is;
        before, it was taken as 'nothing new is started' and stopped at once."""
        rl = limits.RoomLimits(room())
        r = allowed(rl, p=proposed(), s=snap(comp=CS.UNKNOWN, openings=(opening(T0 - MIN),)), now=T0)
        self.assertIs(r.kind, SK.COOL, r.reasons)
        stopped = allowed(limits.RoomLimits(room()), p=proposed(),
                          s=snap(comp=CS.STOPPED, openings=(opening(T0 - MIN),)), now=T0)
        self.assertIsNot(stopped.kind, SK.COOL)  # a stopped unit is not started with a door open


if __name__ == "__main__":
    unittest.main()
