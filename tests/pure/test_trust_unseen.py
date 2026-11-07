"""The trust check scores only hours whose compressor was known and stopped (4.3)."""

from __future__ import annotations

import dataclasses
import unittest

from tests.pure._load import load
from tests.pure.sim_room import SimRoom

models = load("models")
model_pkg = load("model")
forecast = load("model.forecast")


class UnknownCompressorHourIsNotResting(unittest.TestCase):
    def test_an_hour_the_unit_ran_unseen_is_not_scored_as_resting(self) -> None:
        """A house whose units report no hvac_action and have no frequency
        sensor: the compressor is UNKNOWN. An hour in which the unit in fact
        cooled must not be scored as a resting hour."""
        sim = SimRoom(seed=3)
        m = model_pkg.RoomModel()
        ran_in_hour = False
        scored_while_running = 0
        for minute in range(3 * 1440):
            sim.advance()
            ran_in_hour = ran_in_hour or sim.running
            snap = sim.snapshot()
            head = dataclasses.replace(snap.heads[0], compressor=models.CompressorState.UNKNOWN,
                                       compressor_source=models.CompressorSource.UNKNOWN)
            snap = dataclasses.replace(snap, heads=(head,))
            m.minute(snap, sim.hour)
            if sim.minute % 5 == 0:
                m.five_minutes(sim.now)
            if sim.minute % 60 == 0 and m.heat is not None:
                rest = [forecast.ForecastInput(sim.outdoor(), 60.0, 0.0, (sim.hour + i / 12.0) % 24,
                                               models.StepKind.REST, 0.0) for i in range(12)]
                before = len(m.trust.one_hour_errors)
                m.hour(sim.now, snap.room_temperature.value, rest, 20.0)
                if len(m.trust.one_hour_errors) > before and ran_in_hour:
                    scored_while_running += 1
                ran_in_hour = False
        self.assertEqual(scored_while_running, 0,
                         f"{scored_while_running} hours in which the unit cooled were scored as resting")


if __name__ == "__main__":
    unittest.main()
