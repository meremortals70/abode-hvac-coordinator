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
        observer._load_steps[CLIMATE] = observe._LoadStepMemory(models.CompressorState.RUNNING, 1.5)
        draw = unit.DrawLearner(has_load_sensor=True)
        draw.table[UNIT] = {3: (TRUE_DRAW, 5)}  # 1.5 kW learned at effort 2
        lh = observe.LoadHistory()
        for k in range(40):  # the house's noise, learned over the half-hour before
            lh.add(T0 - timedelta(seconds=30 * (40 - k)), 3.0 + (0.03, -0.02, 0.01, -0.03)[k % 4])
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
                draw_kw={UNIT: draw.draw_at(UNIT, 2.0)},
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


class DrawFromMeter(unittest.TestCase):
    """A metered unit's draw is measured, so nothing of the house's load is
    needed and one reading at an effort is enough [D110]."""

    def states(self, now: datetime, watts: str, setpoint: float) -> dict:
        states = _states(now, setpoint)
        states["sensor.meter"] = models.RawState("sensor.meter", watts, {"unit_of_measurement": "W"}, now, now)
        return states

    def observe(self, draw: object, meters: object, now: datetime, watts: str, setpoint: float) -> None:
        snap = observe.RoomObserver(_room()).build(
            house=models.HouseConfig(), states=self.states(now, watts, setpoint), now=now,
            choices=models.RoomChoices(), location=None, forecast=(), forecast_temperature_unit=None,
            forecast_wind_unit=None, tariff=(), load_history=observe.LoadHistory(), commands={}, draw_kw={},
            unit_meters={UNIT: "sensor.meter"},
        )
        draw.observe(now, snap.heads, {}, observe.LoadHistory(), meters)

    def test_the_meter_teaches_the_draw_without_a_house_load(self) -> None:
        draw, meters = unit.DrawLearner(has_load_sensor=True), observe.UnitMeters()
        now = T0
        meters.add(now, UNIT, 0.02)
        meters.add(now + timedelta(seconds=30), UNIT, 0.02)  # the resting level: the fan
        self.observe(draw, meters, now, "20", 24.0)  # resting: nothing to learn
        self.assertIsNone(draw.draw_at(UNIT, 2.0))
        now += timedelta(seconds=30)
        meters.add(now, UNIT, 1.52)
        self.observe(draw, meters, now, "1520", 22.0)  # 2 K of effort, 1.5 kW above resting
        self.assertAlmostEqual(draw.draw_at(UNIT, 2.0) or 0.0, 1.5, places=2)
        self.assertEqual(draw.table[UNIT], {3: (1.5, 1)})  # effort 2 is bin 3, one reading

    def test_a_metered_unit_is_not_learned_from_the_house_load(self) -> None:
        """The load step would read the whole house, so a metered unit's draw
        never comes from it: the meter's reading stands alone [D028][D110]."""
        draw, meters = unit.DrawLearner(has_load_sensor=True), observe.UnitMeters()
        meters.add(T0, UNIT, 0.02)
        draw._events.append(unit._DrawEvent(UNIT, T0, 1, 2.0))
        lh = observe.LoadHistory()
        for k in range(40):
            lh.add(T0 - timedelta(seconds=30 * (40 - k)), 1.0 + (0.03, -0.02, 0.01, -0.03)[k % 4])
        for k in range(30):
            lh.add(T0 + timedelta(seconds=30 * k), 1.0 + TRUE_DRAW + (0.03, -0.02, 0.01, -0.03)[k % 4])
        draw._judge(T0 + timedelta(minutes=6), lh, meters)
        self.assertEqual(draw.table.get(UNIT, {}), {})


if __name__ == "__main__":
    unittest.main()
