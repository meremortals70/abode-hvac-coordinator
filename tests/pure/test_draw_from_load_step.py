"""A compressor change found by the house-load step (4.8, source 3) is
dated a minute or more after it happened, so it teaches the draw learner
nothing [D028]. Run in the coordinator's order: Observe, then the cycle's
house-load reading, then on a minute the draw learner."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from tests.pure._load import load

models = load("models")
observe = load("observe")
unit = load("model.unit")

CLIMATE = "climate.living_room_aircon"
UNIT = "own:" + CLIMATE
T0 = datetime(2026, 11, 7, 3, 0, 0, tzinfo=UTC)
TRUE_DRAW = 1.5


def _room() -> object:
    return models.RoomConfig(
        room_id="living", name="Living", heads=(models.HeadConfig(CLIMATE),),
        temperature_sensor="sensor.t", humidity_sensor="sensor.h",
        occupied_band=(23.0, 26.0), sleep_band=(20.0, 23.0), precool_band=(23.0, 26.0),
    )


def _states(now: datetime, setpoint: float) -> dict:
    attrs = {"current_temperature": 24.0, "temperature": setpoint}
    return {
        CLIMATE: models.RawState(CLIMATE, "cool", attrs, T0, now),
        "sensor.t": models.RawState("sensor.t", "24.0", {"unit_of_measurement": "°C"}, T0, now),
        "sensor.h": models.RawState("sensor.h", "55", {"unit_of_measurement": "%"}, T0, now),
    }


class DrawFromLoadStep(unittest.TestCase):
    def test_load_step_stop_learns_the_units_real_draw(self) -> None:
        room = _room()
        observer = observe.RoomObserver(room)
        # the compressor was seen starting by an earlier load step
        observer._load_steps[CLIMATE] = observe._LoadStepMemory(models.CompressorState.RUNNING, None)
        draw = unit.DrawLearner(has_load_sensor=True)
        draw.table[UNIT] = {3: (TRUE_DRAW, 5)}  # 1.5 kW learned at effort 2
        lh = observe.LoadHistory()
        commands: dict[str, datetime] = {}
        command_at = T0 + timedelta(minutes=2, seconds=30)  # Act raises the setpoint
        stop_at = command_at + timedelta(seconds=10)  # the compressor stops 10 s later

        def load_kw(t: datetime) -> float:
            rest = 1.5 if t < T0 + timedelta(minutes=4, seconds=30) else 1.4  # a fridge cycles off
            return rest + (TRUE_DRAW if t < stop_at else 0.0)

        sources = []
        now = T0
        while now <= T0 + timedelta(minutes=7):
            setpoint = 22.0 if now <= command_at else 24.5
            snap = observer.build(
                house=models.HouseConfig(house_load="sensor.load"), states=_states(now, setpoint), now=now,
                choices=models.RoomChoices(), location=None, forecast=(), forecast_temperature_unit=None,
                forecast_wind_unit=None, tariff=(), load_history=lh, commands=commands,
                draw_kw={UNIT: draw.draw_at(UNIT, 2.0)}, head_outdoor_units={CLIMATE: UNIT},
            )
            sources.append((now, snap.heads[0].compressor, snap.heads[0].compressor_source))
            lh.add(now, load_kw(now))  # coordinator._house
            if now.second == 0:
                draw.observe(now, snap.heads, commands, lh)  # the minute's draw learning
            if now == command_at:
                commands[CLIMATE] = now  # Act's record
            now += timedelta(seconds=30)
        # the load step did see the stop
        self.assertIn((models.CompressorState.STOPPED, models.CompressorSource.LOAD_STEP),
                      {(s, src) for _, s, src in sources})
        # the unit draws 1.5 kW: nothing learned from this stop may say it draws a fraction of that
        learned = {draw.draw_at(UNIT, e) for e in unit.curve_efforts()}
        self.assertGreaterEqual(min(learned), 1.4, f"table {draw.table}; draw by effort {sorted(learned)} kW, "
                                f"true {TRUE_DRAW} kW")


if __name__ == "__main__":
    unittest.main()
