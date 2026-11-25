"""The simulated room's humidity must move for the whole-loop tests to mean
anything: a fixed reading cannot show a coil rebound [D060][D103]."""

from __future__ import annotations

import unittest

from tests.pure.sim_room import ROOM_RH, SimRoom


class SimulatedMoisture(unittest.TestCase):
    def test_the_default_room_keeps_its_fixed_reading(self) -> None:
        """The many model tests that use a bare SimRoom must not change."""
        sim = SimRoom(seed=3)
        readings = set()
        for _ in range(120):
            sim.advance()
            readings.add(sim.snapshot().room_humidity.value)
        self.assertEqual(readings, {ROOM_RH})

    def test_a_moisture_room_dries_when_the_coil_runs(self) -> None:
        sim = SimRoom(seed=3, external=True, moisture=True)
        sim.command_direction, sim.command_kind, sim.command_effort = 1, "cool", 3.0
        before = sim.e_hpa
        for _ in range(90):
            sim.advance()
        self.assertLess(sim.e_hpa, before)
        self.assertNotEqual(sim.snapshot().room_humidity.value, ROOM_RH)

    def test_a_wet_stop_gives_the_coils_moisture_back(self) -> None:
        sim = SimRoom(seed=3, external=True, moisture=True)
        sim.command_direction, sim.command_kind, sim.command_effort = 1, "cool", 3.0
        for _ in range(90):
            sim.advance()
        stopped_at = sim.e_hpa
        sim.command_direction = 0
        for _ in range(10):
            sim.advance()
        self.assertGreater(sim.e_hpa, stopped_at)

    def test_a_dry_after_a_long_rest_does_not_repeat_the_mode_change_stop(self) -> None:
        """Only a wet coil stops on cool-to-dry [D103]. After an hour at rest
        the coil has already given its moisture back [D060], so a later dry
        must not pay a second rebound or lose three minutes again."""
        sim = SimRoom(seed=3, external=True, moisture=True)
        sim.command_direction, sim.command_kind, sim.command_effort = 1, "cool", 3.0
        for _ in range(90):
            sim.advance()
        sim.command_direction, sim.command_effort = 0, 0.0
        for _ in range(60):
            sim.advance()
        self.assertIsNone(sim._change_stop)
        sim.command_direction, sim.command_kind, sim.command_effort = 1, "dry", 3.0
        for _ in range(4):
            sim.advance()
        self.assertIsNone(sim._change_stop)
