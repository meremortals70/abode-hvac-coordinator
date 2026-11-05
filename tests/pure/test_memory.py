"""memory.py: what is kept, and seeding from history (Module 10). The Store
and the save timers are tested through Home Assistant (tests/ha/test_memory.py)."""

from __future__ import annotations

import dataclasses
import json
import math
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load
from tests.pure.sim_room import SimRoom

mem = load("memory")
models = load("models")
model = load("model")
filt = load("model.filter")
arb = load("control.arbiter")
unit = load("model.unit")

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def room(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "room_id": "sim", "name": "Sim", "heads": (models.HeadConfig("climate.sim", None, "sensor.hz"),),
        "temperature_sensor": "sensor.t", "humidity_sensor": "sensor.rh", "occupied_band": (23.0, 26.0),
        "sleep_band": (20.0, 23.0), "precool_band": (23.0, 26.0), "sun_sensor": "binary_sensor.sun",
        "blinds": ("cover.b",),
    }
    base.update(kw)
    return models.RoomConfig(**base)


HOUSE = models.HouseConfig(outdoor_temperature="sensor.out", outdoor_humidity="sensor.out_rh",
                           solar_power="sensor.pv", house_load="sensor.load", weather="weather.home")
PROFILE = models.CapabilityProfile(("off", "cool"), ("auto",), ("fixed",), (), 17.0, 30.0, 0.5, False, None)


def learned_state() -> Any:
    rm = model.RoomModel()
    sim = SimRoom()
    for _ in range(30):
        sim.advance()
        rm.minute(sim.snapshot(), sim.hour)
        if sim.minute % 5 == 0:
            rm.five_minutes(sim.now)
    rm.unit.u_min = 1.3
    rm.trust.weekly_errors.append(("2026-W43", 0.05))
    a = arb.Arbiter()
    a.wins.extend([True, False])
    choices = models.RoomChoices(automatic_control=False, automatic_vane_control=True,
                                 comfort_mode=models.ComfortMode.SLEEP, leave_pressed_at=T0,
                                 precool_ready_by=T0 + timedelta(hours=1))
    return mem.RoomState(rm, a, choices, True, PROFILE, models.DayScore(98.5, 40.0, 0.5, 1.25))


class Kept(unittest.TestCase):
    def test_round_trip(self) -> None:
        s = learned_state()
        data = json.loads(json.dumps(mem.room_to_dict(s)))  # stored as JSON
        r = mem.room_from_dict(data)
        self.assertEqual(r.model.heat.z, s.model.heat.z)
        self.assertEqual(r.model.heat.p, s.model.heat.p)
        self.assertEqual(r.model.heat.noise, s.model.heat.noise)
        self.assertEqual(r.model.humidity.z, s.model.humidity.z)
        self.assertEqual(r.model.unit.u_min, 1.3)
        self.assertEqual(r.model.trust.weekly_errors, [("2026-W43", 0.05)])
        self.assertEqual(list(r.arbiter.wins), [True, False])
        self.assertEqual(r.choices, s.choices)  # [D049][D058][D088][I8]
        self.assertEqual((r.occupied, r.profile, r.yesterday), (True, PROFILE, s.yesterday))  # [D068][D048]

    def test_not_the_pid_integral(self) -> None:
        def keys(d: Any) -> set[str]:
            if isinstance(d, dict):
                return set(d) | {k for v in d.values() for k in keys(v)}
            return set()

        found = keys(mem.room_to_dict(learned_state()))
        self.assertFalse({"pid", "integral"} & found)  # [D031]
        self.assertTrue({"heat", "arbiter", "choices"} <= found)

    def test_a_new_room(self) -> None:
        r = mem.room_from_dict(mem.room_to_dict(mem.RoomState()))
        self.assertIsNone(r.model.heat)
        self.assertEqual((r.choices, r.occupied, r.profile, r.yesterday), (models.RoomChoices(), None, None, None))

    def test_numbers_that_are_not_finite_are_relearned(self) -> None:
        data = mem.room_to_dict(learned_state())
        data["heat"]["z"][3] = float("nan")
        data["unit"]["u_min"] = float("inf")
        data["profile"]["max_setpoint"] = float("inf")
        data["yesterday"]["cost"] = float("nan")
        r = mem.room_from_dict(data)
        self.assertIsNone(r.model.heat)  # [D031]
        self.assertIsNotNone(r.model.humidity)
        self.assertIsNone(r.model.unit.u_min)
        self.assertIsNone(r.profile)
        self.assertEqual((r.yesterday.cost, r.yesterday.comfort_in_band), (None, 98.5))

    def test_what_cannot_be_read_takes_its_default(self) -> None:
        r = mem.room_from_dict({"heat": "x", "choices": {"automatic_control": "yes", "comfort_mode": "nap",
                                                         "leave_pressed_at": "2026-10-28T12:00:00"},
                                "occupied": 1, "profile": {"hvac_modes": "cool"}})
        self.assertIsNone(r.model.heat)
        self.assertEqual(r.choices, models.RoomChoices())  # a naive time cannot be trusted either
        self.assertEqual(mem.choices_from_dict({}), models.RoomChoices())
        off = mem.choices_from_dict({"automatic_control": False, "automatic_vane_control": "x"})
        self.assertEqual((off.automatic_control, off.automatic_vane_control), (False, False))
        self.assertIsNone(r.occupied)
        self.assertIsNone(r.profile)

    def test_profile(self) -> None:
        d = mem.profile_to_dict(PROFILE)
        self.assertEqual(mem.profile_from_dict(d), PROFILE)
        no_step = {**d, "setpoint_step": None, "step_reason": "steps differ"}
        self.assertEqual(mem.profile_from_dict(no_step).step_reason, "steps differ")
        narrow = dataclasses.replace(PROFILE, takes_range=False)
        self.assertEqual(mem.profile_from_dict(mem.profile_to_dict(narrow)), narrow)
        older = {k: v for k, v in d.items() if k != "takes_range"}  # stored before it was kept
        self.assertTrue(mem.profile_from_dict(older).takes_range)
        for bad in ({**d, "setpoint_step": 0.0}, {**d, "min_setpoint": 31.0}, {**d, "range_only": "no"},
                    {**d, "takes_range": "yes"},
                    {**d, "fan_modes": None}, {k: v for k, v in d.items() if k != "min_setpoint"}):
            self.assertIsNone(mem.profile_from_dict(bad), bad)

    def test_whole_file(self) -> None:
        draw = unit.DrawLearner(has_load_sensor=True)
        draw.table = {"u1": {2: (1.1, 3)}}
        data = json.loads(json.dumps(mem.to_data({"sim": learned_state()}, mem.HouseState(draw))))
        rooms, house = mem.from_data(data, has_load_sensor=True)
        self.assertEqual(list(rooms), ["sim"])
        self.assertEqual(house.draw.table, {"u1": {2: (1.1, 3)}})  # [D028]
        self.assertEqual(mem.from_data(None, has_load_sensor=False)[0], {})
        self.assertFalse(mem.from_data({}, has_load_sensor=False)[1].draw.has_load_sensor)
        rooms, house = mem.from_data({"rooms": "x", "draw": {"table": {"u1": {"x": [1, 1]}}}}, has_load_sensor=True)
        self.assertEqual((rooms, house.draw.table), ({}, {}))

    def test_migrate_never_discards_learning(self) -> None:
        data = mem.to_data({"sim": learned_state()}, mem.HouseState())
        self.assertEqual(mem.migrate(1, 1, data), data)


class Seed(unittest.TestCase):
    def test_entities(self) -> None:
        self.assertEqual(mem.seed_entities(room(), HOUSE), (
            "sensor.t", "sensor.rh", "binary_sensor.sun", "cover.b", "climate.sim", "sensor.hz", "sensor.out",
            "sensor.out_rh", "sensor.pv", "sensor.load", "weather.home"))

    def history(self, minutes: int, outdoor_sensor: bool = True) -> dict[str, list[Any]]:
        """A simulated room's history as the recorder keeps it: a row when the
        state or an attribute changes, last_changed moving only with the state.
        Without the outdoor sensor, the weather entity's own temperature (in
        °F, to check its unit is read) is the only outdoor reading."""
        sim = SimRoom(seed=5)
        out: dict[str, list[Any]] = {}
        last: dict[str, Any] = {}
        changed: dict[str, Any] = {}

        def record(entity: str, state: str, **attrs: Any) -> None:
            if last.get(entity) != (state, attrs):
                if last.get(entity, (None,))[0] != state:
                    changed[entity] = sim.now
                last[entity] = (state, attrs)
                out.setdefault(entity, []).append(models.RawState(entity, state, attrs, changed[entity], sim.now))

        for _ in range(minutes):
            sim.advance()
            sim.command_direction = 1 if sim.t > 25.0 else (0 if sim.t < 23.5 else sim.command_direction)
            sim.command_effort = 1.0
            running = sim.command_direction != 0
            record("sensor.t", f"{sim.sensor():.1f}", unit_of_measurement="°C")
            record("sensor.rh", "55", unit_of_measurement="%")
            if outdoor_sensor:
                record("sensor.out", f"{sim.outdoor():.1f}", unit_of_measurement="°C")
            record("climate.sim", "cool" if running else "off", current_temperature=round(sim.t, 1),
                   temperature=round(sim.t - 1.0, 1), hvac_action="cooling" if running else "off")
            record("binary_sensor.sun", "on" if sim.sun() > 0 else "off")
            if outdoor_sensor:
                record("weather.home", "sunny", cloud_coverage=30)
            else:
                record("weather.home", "sunny", cloud_coverage=30, temperature=round(sim.outdoor() * 1.8 + 32.0),
                       temperature_unit="°F", humidity=50)
        return out

    def test_the_weather_stands_in_for_a_missing_outdoor_sensor(self) -> None:
        """No outdoor sensor: the weather entity's temperature stands in, in
        its own unit, as it does live [D099]."""
        h = self.history(1440, outdoor_sensor=False)
        house = dataclasses.replace(HOUSE, outdoor_temperature=None)
        start = min(s.last_changed for v in h.values() for s in v)
        rm = model.RoomModel()
        got = mem.replay(rm, room=room(), house=house, histories=h, start=start, end=start + timedelta(days=1),
                         tz=UTC, location=None, system_temperature_unit="°C")
        self.assertGreater(got.corrections, 0)
        self.assertNotEqual(rm.last_step.reason if rm.last_step else None, "no outdoor temperature yet")
        f_last = h["weather.home"][-1].attributes["temperature"]
        assert rm.heat is not None and rm.heat.last_outdoor is not None
        self.assertAlmostEqual(rm.heat.last_outdoor, (f_last - 32.0) / 1.8, delta=1.0)  # read in °F, kept in °C

    def test_replayed_exactly_as_the_live_path_does(self) -> None:
        h = self.history(2 * 1440)
        start = min(s.last_changed for v in h.values() for s in v)
        end = max(s.last_reported for v in h.values() for s in v)
        seeded = model.RoomModel()
        got = mem.replay(seeded, room=room(), house=HOUSE, histories=h, start=start, end=end, tz=UTC,
                         location=None, system_temperature_unit="°C")
        # the same states, fed through Observe and the model one minute at a time
        live = model.RoomModel()
        observer = load("observe").RoomObserver(room())
        events = sorted((s for v in h.values() for s in v), key=lambda s: s.last_reported)
        states: dict[str, Any] = {}
        t, i = start, 0
        while t <= end:
            while i < len(events) and events[i].last_reported <= t:
                states[events[i].entity_id] = events[i]
                i += 1
            snap = observer.build(house=HOUSE, states=states, now=t, choices=models.RoomChoices(), location=None,
                                  forecast=[{"datetime": t.replace(minute=0).isoformat(), "cloud_coverage": 30,
                                             "uv_index": None}],
                                  forecast_temperature_unit=None, forecast_wind_unit=None, tariff=(),
                                  load_history=load("observe").LoadHistory(), commands={}, draw_kw={},
                                  head_outdoor_units={"climate.sim": "own:climate.sim"})
            live.minute(snap, t.hour + t.minute / 60.0)
            if t.minute % 5 == 0:
                live.five_minutes(t)
            t += timedelta(minutes=1)
        self.assertEqual(seeded.heat.z, live.heat.z)
        self.assertEqual(seeded.heat.corrections, live.heat.corrections)
        self.assertGreater(got.corrections, 400)
        self.assertEqual(got.corrections, seeded.heat.corrections)
        self.assertAlmostEqual(got.days, (end - start).total_seconds() / 86400, places=3)
        self.assertTrue(all(math.isfinite(x) for x in seeded.heat.z))
        # rows where only an attribute changed are in the history, and replayed when written
        rows = h["climate.sim"]
        self.assertTrue(any(r.last_reported > r.last_changed for r in rows))

    def test_a_row_that_changed_only_attributes_is_applied_when_written(self) -> None:
        """A unit that starts cooling without its mode changing: the row is
        applied at the time it was written, not at its older last change [D002]."""
        t0 = T0
        later = t0 + timedelta(minutes=30)
        h = {"sensor.t": [models.RawState("sensor.t", "25.0", {"unit_of_measurement": "°C"}, t0, t0)],
             "climate.sim": [models.RawState("climate.sim", "cool", {"hvac_action": "idle"}, t0, t0),
                             models.RawState("climate.sim", "cool", {"hvac_action": "cooling"}, t0, later)]}
        seen: list[tuple[Any, str]] = []
        real = mem.RoomObserver

        class Spy(real):  # type: ignore[misc, valid-type]
            def build(self, **kw: Any) -> Any:
                seen.append((kw["now"], kw["states"]["climate.sim"].attributes["hvac_action"]))
                return super().build(**kw)

        mem.RoomObserver = Spy
        try:
            mem.replay(model.RoomModel(), room=room(), house=HOUSE, histories=h, start=t0, end=t0 + timedelta(hours=1),
                       tz=UTC, location=None, system_temperature_unit="°C")
        finally:
            mem.RoomObserver = real
        self.assertEqual({a for t, a in seen if t < later}, {"idle"})
        self.assertEqual({a for t, a in seen if t >= later}, {"cooling"})

    def test_cloud_cover_lets_sunny_steps_correct(self) -> None:
        h = self.history(1440)
        no_weather = {k: v for k, v in h.items() if k != "weather.home"}
        start = min(s.last_changed for v in h.values() for s in v)
        end = start + timedelta(days=1)
        a, b = model.RoomModel(), model.RoomModel()
        mem.replay(a, room=room(), house=HOUSE, histories=h, start=start, end=end, tz=UTC, location=None,
                   system_temperature_unit="°C")
        mem.replay(b, room=room(), house=HOUSE, histories=no_weather, start=start, end=end, tz=UTC, location=None,
                   system_temperature_unit="°C")
        self.assertGreater(a.heat.corrections, b.heat.corrections)

    def test_stop_and_nothing_to_replay(self) -> None:
        rm = model.RoomModel()
        got = mem.replay(rm, room=room(), house=HOUSE, histories={}, start=T0, end=T0 + timedelta(days=1), tz=UTC,
                         location=None, system_temperature_unit="°C")
        self.assertEqual((got.days, got.corrections, rm.heat), (0.0, 0, None))
        h = self.history(600)
        start = min(s.last_changed for v in h.values() for s in v)
        got = mem.replay(rm, room=room(), house=HOUSE, histories=h, start=start, end=start + timedelta(days=1),
                         tz=UTC, location=None, system_temperature_unit="°C", stop=lambda: True)
        self.assertEqual((got.days, rm.heat), (0.0, None))

    def test_a_second_seeding_counts_only_its_own_corrections(self) -> None:
        h = self.history(1440)
        start = min(s.last_changed for v in h.values() for s in v)
        rm = model.RoomModel()
        first = mem.replay(rm, room=room(), house=HOUSE, histories=h, start=start, end=start + timedelta(hours=12),
                           tz=UTC, location=None, system_temperature_unit="°C")
        second = mem.replay(rm, room=room(), house=HOUSE, histories=h, start=start + timedelta(hours=12),
                            end=start + timedelta(hours=24), tz=UTC, location=None, system_temperature_unit="°C")
        self.assertGreater(first.corrections, 0)
        self.assertEqual(first.corrections + second.corrections, rm.heat.corrections)


if __name__ == "__main__":
    unittest.main()
