"""observe.py: one snapshot of one room (Module 1)."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.pure._load import load

observe = load("observe")
models = load("models")

NOW = datetime(2026, 10, 27, 5, 45, tzinfo=UTC)  # 15:45 in Brisbane
BRISBANE = (-27.535, 152.97)
VT = models.ValueTag
CS = models.CompressorState
SRC = models.CompressorSource


def raw(entity_id: str, state: str, age_s: float = 10.0, reported_age_s: float | None = None, **attrs: Any) -> Any:
    changed = NOW - timedelta(seconds=age_s)
    reported = NOW - timedelta(seconds=reported_age_s if reported_age_s is not None else age_s)
    return models.RawState(entity_id, state, attrs, changed, reported)


def room(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "room_id": "r1",
        "name": "Office",
        "heads": (models.HeadConfig("climate.office"),),
        "temperature_sensor": "sensor.office_t",
        "humidity_sensor": "sensor.office_rh",
        "occupied_band": (24.0, 27.0),
        "sleep_band": (21.0, 24.0),
        "precool_band": (24.0, 27.0),
    }
    base.update(kw)
    return models.RoomConfig(**base)


HOUSE = models.HouseConfig(
    outdoor_temperature="sensor.out_t",
    outdoor_humidity="sensor.out_rh",
    outdoor_wind="sensor.out_w",
    house_load="sensor.load",
    solar_power="sensor.solar",
    battery_charge="sensor.soc",
    grid_power="sensor.grid",
)


def base_states() -> dict[str, Any]:
    states = [
        raw("sensor.office_t", "24.6", age_s=86400 * 3, unit_of_measurement="°C"),
        raw("sensor.office_rh", "58", unit_of_measurement="%"),
        raw("climate.office", "cool", temperature=23.0, current_temperature=24.0, current_humidity=55,
            fan_mode="auto", swing_mode="3"),
        raw("sensor.out_t", "29.0", unit_of_measurement="°C"),
        raw("sensor.out_rh", "50", unit_of_measurement="%"),
        raw("sensor.out_w", "18", unit_of_measurement="km/h"),
        raw("sensor.load", "1500", unit_of_measurement="W"),
        raw("sensor.solar", "3.2", unit_of_measurement="kW"),
        raw("sensor.soc", "80", unit_of_measurement="%"),
        raw("sensor.grid", "-0.4", unit_of_measurement="kW"),
    ]
    return {s.entity_id: s for s in states}


def build(
    r: Any | None = None,
    states: dict[str, Any] | None = None,
    observer: Any | None = None,
    now: datetime = NOW,
    **kw: Any,
) -> Any:
    args: dict[str, Any] = {
        "house": HOUSE,
        "states": states if states is not None else base_states(),
        "now": now,
        "choices": models.RoomChoices(),
        "location": BRISBANE,
        "forecast": [],
        "forecast_temperature_unit": "°C",
        "forecast_wind_unit": "km/h",
        "tariff": [],
        "load_history": observe.LoadHistory(),
        "commands": {},
        "draw_kw": {},
        "head_outdoor_units": {},
    }
    args.update(kw)
    obs = observer if observer is not None else observe.RoomObserver(r if r is not None else room())
    return obs.build(**args)


class Readings(unittest.TestCase):
    def test_room_readings_have_no_age_limit(self) -> None:
        s = build()
        self.assertEqual((s.room_temperature.value, s.room_temperature.tag), (24.6, VT.MEASURED))
        self.assertEqual(s.room_humidity.value, 58.0)

    def test_outdoor_too_old_after_3_hours(self) -> None:
        states = base_states()
        states["sensor.out_t"] = raw("sensor.out_t", "29", age_s=3 * 3600 + 1, unit_of_measurement="°C")
        states["sensor.out_rh"] = raw("sensor.out_rh", "50", age_s=3 * 3600 - 1, unit_of_measurement="%")
        s = build(states=states)
        self.assertEqual(s.outdoor_temperature.tag, VT.MISSING)
        self.assertEqual(s.outdoor_humidity.tag, VT.MEASURED)
        self.assertIn("sensor.out_t", s.stale)
        self.assertNotIn("sensor.out_rh", s.stale)

    def test_the_forecast_stands_in_for_a_missing_outdoor_reading(self) -> None:
        """No outdoor sensor, or one too old: the forecast's hour now stands
        in, tagged estimated, so the room still learns [D099]."""
        states = base_states()
        del states["sensor.out_t"]
        states["sensor.out_rh"] = raw("sensor.out_rh", "50", age_s=3 * 3600 + 1, unit_of_measurement="%")
        hour = NOW.replace(minute=0, second=0, microsecond=0)
        fc = [{"datetime": (hour - timedelta(hours=1)).isoformat(), "temperature": 20.0, "humidity": 40},
              {"datetime": hour.isoformat(), "temperature": 27.0, "humidity": 65},
              {"datetime": (hour + timedelta(hours=1)).isoformat(), "temperature": 31.0, "humidity": 70}]
        s = build(states=states, forecast=fc)
        self.assertEqual((s.outdoor_temperature.value, s.outdoor_temperature.tag), (27.0, VT.ESTIMATED))
        self.assertEqual((s.outdoor_humidity.value, s.outdoor_humidity.tag), (65.0, VT.ESTIMATED))
        self.assertIn("sensor.out_rh", s.stale)  # still reported as gone quiet
        sample = load("model.filter").minute_sample(s, 12.0)
        self.assertEqual((sample.outdoor, sample.outdoor_rh), (27.0, 65.0))  # what the model learns from
        measured = build(forecast=fc)
        self.assertEqual((measured.outdoor_temperature.value, measured.outdoor_temperature.tag), (29.0, VT.MEASURED))
        no_hour = build(states=states, forecast=fc[2:])  # nothing for the hour now
        self.assertEqual((no_hour.outdoor_temperature.value, no_hour.outdoor_temperature.tag), (None, VT.MISSING))
        gap = build(states=states, forecast=[{"datetime": hour.isoformat(), "humidity": 60}])
        self.assertIsNone(gap.outdoor_temperature.value)  # the hour has no temperature
        self.assertEqual(gap.outdoor_humidity.value, 60.0)

    def test_age_is_from_the_later_of_change_and_report(self) -> None:
        states = base_states()
        states["sensor.out_t"] = raw("sensor.out_t", "29", age_s=5 * 3600, reported_age_s=60, unit_of_measurement="°C")
        s = build(states=states)
        self.assertEqual(s.outdoor_temperature.tag, VT.MEASURED)
        self.assertAlmostEqual(s.outdoor_temperature.age_s, 60.0)

    def test_power_too_old_after_15_minutes(self) -> None:
        states = base_states()
        states["sensor.load"] = raw("sensor.load", "1500", age_s=15 * 60 + 1, unit_of_measurement="W")
        s = build(states=states)
        self.assertEqual(s.house_load_kw.tag, VT.MISSING)
        self.assertIn("sensor.load", s.stale)

    def test_units_converted(self) -> None:
        s = build()
        self.assertAlmostEqual(s.house_load_kw.value, 1.5)
        self.assertAlmostEqual(s.outdoor_wind_ms.value, 5.0)
        states = base_states()
        states["sensor.out_t"] = raw("sensor.out_t", "86", unit_of_measurement="°F")
        self.assertAlmostEqual(build(states=states).outdoor_temperature.value, 30.0)

    def test_missing_states(self) -> None:
        for bad in ("unavailable", "unknown", "abc"):
            states = base_states()
            states["sensor.out_rh"] = raw("sensor.out_rh", bad, unit_of_measurement="%")
            self.assertEqual(build(states=states).outdoor_humidity.tag, VT.MISSING, bad)
        states = base_states()
        states["sensor.load"] = raw("sensor.load", "1500", unit_of_measurement="BTU/h")
        self.assertEqual(build(states=states).house_load_kw.tag, VT.MISSING)

    def test_unit_stands_in_for_a_missing_room_reading(self) -> None:
        states = base_states()
        states["sensor.office_t"] = raw("sensor.office_t", "unavailable")
        states["sensor.office_rh"] = raw("sensor.office_rh", "unknown")
        s = build(states=states)
        self.assertEqual((s.room_temperature.value, s.room_temperature.tag), (24.0, VT.STOOD_IN))
        self.assertEqual((s.room_humidity.value, s.room_humidity.tag), (55.0, VT.STOOD_IN))

    def test_missing_with_no_stand_in(self) -> None:
        states = base_states()
        states["sensor.office_t"] = raw("sensor.office_t", "unavailable")
        states["climate.office"] = raw("climate.office", "cool", temperature=23.0)
        self.assertEqual(build(states=states).room_temperature.tag, VT.MISSING)


class Heads(unittest.TestCase):
    def test_head_values(self) -> None:
        h = build().heads[0]
        self.assertTrue(h.available)
        self.assertEqual((h.mode, h.setpoint, h.fan_mode, h.vertical_vane), ("cool", 23.0, "auto", "3"))
        self.assertEqual(h.outdoor_unit, "own:climate.office")

    def test_range_setpoint_is_the_middle(self) -> None:
        states = base_states()
        states["climate.office"] = raw("climate.office", "heat_cool", target_temp_low=21.0, target_temp_high=25.0)
        self.assertEqual(build(states=states).heads[0].setpoint, 23.0)

    def test_unavailable_head(self) -> None:
        states = base_states()
        states["climate.office"] = raw("climate.office", "unavailable")
        h = build(states=states).heads[0]
        self.assertFalse(h.available)
        self.assertEqual((h.compressor, h.compressor_source), (CS.UNKNOWN, SRC.UNKNOWN))

    def test_system_unit_fahrenheit(self) -> None:
        states = base_states()
        states["climate.office"] = raw("climate.office", "cool", temperature=73.4, current_temperature=75.2)
        h = build(states=states, system_temperature_unit="°F").heads[0]
        self.assertAlmostEqual(h.setpoint, 23.0)
        self.assertAlmostEqual(h.own_temperature, 24.0)


def freq_room() -> Any:
    return room(heads=(models.HeadConfig("climate.office", "ou1", "sensor.freq"),))


class Compressor(unittest.TestCase):
    def test_frequency_first(self) -> None:
        states = base_states()
        states["climate.office"] = raw("climate.office", "cool", hvac_action="idle")
        states["sensor.freq"] = raw("sensor.freq", "34", unit_of_measurement="Hz")
        h = build(freq_room(), states=states).heads[0]
        self.assertEqual((h.compressor, h.compressor_source), (CS.RUNNING, SRC.FREQUENCY))
        states["sensor.freq"] = raw("sensor.freq", "0", unit_of_measurement="Hz")
        h = build(freq_room(), states=states).heads[0]
        self.assertEqual((h.compressor, h.compressor_source), (CS.STOPPED, SRC.FREQUENCY))

    def test_hvac_action(self) -> None:
        for action, want in (("cooling", CS.RUNNING), ("heating", CS.RUNNING), ("drying", CS.RUNNING),
                             ("idle", CS.STOPPED), ("fan", CS.STOPPED), ("off", CS.STOPPED)):
            states = base_states()
            states["climate.office"] = raw("climate.office", "cool", hvac_action=action)
            h = build(states=states).heads[0]
            self.assertEqual((h.compressor, h.compressor_source), (want, SRC.HVAC_ACTION), action)

    def test_mode_alone_is_not_an_answer(self) -> None:
        h = build().heads[0]  # in cool, no action, no frequency, no step
        self.assertEqual((h.compressor, h.compressor_source), (CS.UNKNOWN, SRC.UNKNOWN))

    def test_off_or_fan_only_is_not_running(self) -> None:
        for mode in ("off", "fan_only"):
            states = base_states()
            states["climate.office"] = raw("climate.office", mode)
            h = build(states=states).heads[0]
            self.assertEqual((h.compressor, h.compressor_source), (CS.STOPPED, SRC.MODE), mode)
        for mode in ("cool", "heat", "dry", "heat_cool", "auto"):
            states = base_states()
            states["climate.office"] = raw("climate.office", mode)
            self.assertEqual(build(states=states).heads[0].compressor, CS.UNKNOWN, mode)

    def test_an_off_or_fan_head_is_stopped_whatever_a_shared_sensor_says(self) -> None:
        """One frequency sensor is often chosen for every head on an outdoor
        unit; it reads another head's compressor, so a head that is off or
        on its fan answers first (decision of 27 October 2026) [D025]."""
        for mode in ("off", "fan_only"):
            states = base_states()
            states["climate.office"] = raw("climate.office", mode, hvac_action="fan")
            states["sensor.freq"] = raw("sensor.freq", "34", unit_of_measurement="Hz")
            h = build(freq_room(), states=states).heads[0]
            self.assertEqual((h.compressor, h.compressor_source), (CS.STOPPED, SRC.MODE), mode)

    def test_unlisted_action_falls_through(self) -> None:
        states = base_states()
        states["climate.office"] = raw("climate.office", "heat", hvac_action="defrosting")
        self.assertEqual(build(states=states).heads[0].compressor_source, SRC.UNKNOWN)

    def _power(self, levels: list[float], end: datetime = NOW) -> Any:
        """The house power every 30 seconds, the last reading 30 seconds
        before end (this cycle's own reading is added after Observe)."""
        hist = observe.LoadHistory()
        start = end - timedelta(seconds=30 * len(levels))
        wobble = (0.03, -0.02, 0.01, -0.03, 0.02, -0.01)
        for k in range(40):  # the half-hour before: the house's noise, learned
            hist.add(start - timedelta(seconds=30 * (40 - k)), levels[0] + wobble[k % len(wobble)])
        for k, kw in enumerate(levels):
            hist.add(end - timedelta(seconds=30 * (len(levels) - k)), kw)
        return hist

    def _put_to_work(self, obs: Any) -> None:
        states = base_states()
        states["climate.office"] = raw("climate.office", "fan_only")
        self.assertEqual(build(observer=obs, states=states).heads[0].compressor, CS.STOPPED)

    def test_a_rise_in_the_power_is_the_compressor_starting(self) -> None:
        """Settled: off or fan-only is not running; in any other mode a rise
        in the house power is the compressor starting. The office on 7
        November 2026: 0.55 kW, then 1.02 kW about 2 minutes after cool."""
        obs = observe.RoomObserver(room())
        self._put_to_work(obs)
        h = build(observer=obs, load_history=self._power([0.6] * 8)).heads[0]
        self.assertEqual((h.compressor, h.compressor_source), (CS.STOPPED, SRC.LOAD_STEP))
        h = build(observer=obs, load_history=self._power([0.6] * 8 + [1.02, 1.02])).heads[0]
        self.assertEqual((h.compressor, h.compressor_source), (CS.RUNNING, SRC.LOAD_STEP))

    def test_the_inverter_slowing_is_not_a_stop(self) -> None:
        """An inverter slows near its setpoint: 1.31 kW falling to 0.79 kW
        while running (the office, 18:00 to 18:03 on 7 November 2026). Only
        a fall back to where the power stood stopped is a stop."""
        obs = observe.RoomObserver(room())
        self._put_to_work(obs)
        build(observer=obs, load_history=self._power([0.65] * 8 + [1.31, 1.31]))
        h = build(observer=obs, load_history=self._power([0.65] * 6 + [1.31, 1.31, 0.79, 0.79])).heads[0]
        self.assertEqual(h.compressor, CS.RUNNING)
        h = build(observer=obs, load_history=self._power([0.65] * 4 + [1.31, 0.79, 0.79, 0.79, 0.66, 0.66])).heads[0]
        self.assertEqual(h.compressor, CS.STOPPED)

    def test_a_learned_draw_sets_the_step(self) -> None:
        """Half the learned draw is a start: 1.2 kW learned, 0.5 kW is not,
        0.7 kW is."""
        draw = {"own:climate.office": 1.2}
        obs = observe.RoomObserver(room())
        self._put_to_work(obs)
        h = build(observer=obs, load_history=self._power([0.6] * 8 + [1.1, 1.1]), draw_kw=draw).heads[0]
        self.assertEqual(h.compressor, CS.STOPPED)
        obs = observe.RoomObserver(room())
        self._put_to_work(obs)
        h = build(observer=obs, load_history=self._power([0.6] * 8 + [1.3, 1.3]), draw_kw=draw).heads[0]
        self.assertEqual(h.compressor, CS.RUNNING)

    def test_a_small_movement_is_not_a_start(self) -> None:
        obs = observe.RoomObserver(room())
        self._put_to_work(obs)
        h = build(observer=obs, load_history=self._power([0.6] * 8 + [0.65, 0.65])).heads[0]
        self.assertEqual(h.compressor, CS.STOPPED)

    def test_nothing_is_judged_before_the_houses_noise_is_learned(self) -> None:
        obs = observe.RoomObserver(room())
        self._put_to_work(obs)
        hist = observe.LoadHistory()
        for k, kw in enumerate([0.6] * 8 + [1.3, 1.3]):
            hist.add(NOW - timedelta(seconds=30 * (10 - k)), kw)
        h = build(observer=obs, load_history=hist).heads[0]
        self.assertEqual(h.compressor, CS.STOPPED)  # still as the mode left it

    def test_seen_falling_first_it_was_running(self) -> None:
        """After a restart a head already in cool is not known; a fall in
        the power before any rise says it was running and stopped."""
        obs = observe.RoomObserver(room())
        h = build(observer=obs, load_history=self._power([1.2] * 8)).heads[0]
        self.assertEqual(h.compressor, CS.UNKNOWN)
        h = build(observer=obs, load_history=self._power([1.2] * 8 + [0.6, 0.6])).heads[0]
        self.assertEqual((h.compressor, h.compressor_source), (CS.STOPPED, SRC.LOAD_STEP))


class Sun(unittest.TestCase):
    def test_the_solar_power_measures_the_sun_where_the_forecast_cannot(self) -> None:
        """No cloud cover and no UV in the forecast: the sky as the house's
        solar power measures it stands in, and the sun counts as measured."""
        s = build(self.west(), forecast=self.forecast(), measured_clear=0.4).sun
        self.assertTrue(s.on_glass)
        self.assertEqual((s.f_clear, s.tag), (0.4, VT.MEASURED))
        s = build(self.west(), forecast=self.forecast()).sun
        self.assertEqual((s.f_clear, s.tag), (1.0, VT.ESTIMATED))
        s = build(self.west(), forecast=self.forecast(cloud_coverage=45), measured_clear=0.4).sun
        self.assertAlmostEqual(s.f_clear, 0.2 + 0.8 * 0.5)  # the forecast's own, where it has one

    def west(self, **kw: Any) -> Any:
        return room(window_facing="W", **kw)

    def forecast(self, **values: Any) -> list[dict[str, Any]]:
        return [{"datetime": (NOW - timedelta(minutes=45)).isoformat(), **values}]

    def test_geometry_west_window_afternoon(self) -> None:
        s = build(self.west(), forecast=self.forecast(cloud_coverage=45)).sun
        self.assertTrue(s.on_glass)
        self.assertAlmostEqual(s.f_clear, 0.2 + 0.8 * 0.5)
        self.assertEqual((s.transmission, s.s, s.tag), (1.0, s.f_clear, VT.MEASURED))

    def test_east_window_afternoon_is_off(self) -> None:
        s = build(room(window_facing="E")).sun
        self.assertEqual((s.on_glass, s.s), (False, 0.0))

    def test_cannot_say_counts_as_zero(self) -> None:
        s = build(room()).sun  # no window direction
        self.assertEqual((s.on_glass, s.s, s.tag), (None, 0.0, VT.MEASURED))
        s = build(self.west(), location=None).sun
        self.assertIsNone(s.on_glass)

    def test_clear_sky_fraction(self) -> None:
        self.assertAlmostEqual(observe.clear_sky_fraction(0.0, None), 1.0)
        self.assertAlmostEqual(observe.clear_sky_fraction(89.0, None), 0.2 + 0.8 * (1 - 89 / 90))
        self.assertEqual(observe.clear_sky_fraction(90.0, None), 0.2)
        self.assertEqual(observe.clear_sky_fraction(None, 4.0), 0.5)
        self.assertEqual(observe.clear_sky_fraction(None, 11.0), 1.0)
        self.assertIsNone(observe.clear_sky_fraction(None, None))

    def test_assumed_clear_sky_is_estimated(self) -> None:
        s = build(self.west()).sun
        self.assertEqual((s.f_clear, s.tag), (1.0, VT.ESTIMATED))

    def test_sun_sensor_replaces_geometry(self) -> None:
        states = base_states()
        states["binary_sensor.sun_office"] = raw("binary_sensor.sun_office", "off")
        s = build(self.west(sun_sensor="binary_sensor.sun_office"), states=states).sun
        self.assertEqual((s.on_glass, s.source), (False, "sensor"))
        states["binary_sensor.sun_office"] = raw("binary_sensor.sun_office", "unavailable")
        s = build(self.west(sun_sensor="binary_sensor.sun_office"), states=states).sun
        self.assertEqual((s.on_glass, s.source), (True, "geometry (sensor missing)"))

    def test_blinds(self) -> None:
        states = base_states()
        states["cover.b1"] = raw("cover.b1", "open", current_position=50)
        states["cover.b2"] = raw("cover.b2", "closed", current_position=0)
        s = build(self.west(blinds=("cover.b1", "cover.b2")), states=states,
                  forecast=self.forecast(cloud_coverage=0)).sun
        want = ((0.15 + 0.85 * 0.5) + 0.15) / 2
        self.assertAlmostEqual(s.transmission, want)
        self.assertAlmostEqual(s.s, want)

    def test_unknown_blind_counts_as_open(self) -> None:
        states = base_states()
        states["cover.b1"] = raw("cover.b1", "unavailable")
        snap = build(self.west(blinds=("cover.b1",)), states=states, forecast=self.forecast(cloud_coverage=0))
        self.assertEqual(snap.blind_positions[0].tag, VT.MISSING)
        self.assertEqual(snap.sun.transmission, 1.0)


class Outdoor(unittest.TestCase):
    def test_forecast_filled_and_converted(self) -> None:
        fc = [
            {"datetime": (NOW + timedelta(hours=1)).isoformat(), "temperature": 86.0, "wind_speed": 36.0},
            {"datetime": "not a date", "temperature": 20.0},
            {"datetime": NOW.isoformat(), "humidity": 70},
        ]
        s = build(forecast=fc, forecast_temperature_unit="°F", forecast_wind_unit="km/h")
        self.assertEqual(len(s.forecast), 2)
        first, second = s.forecast
        self.assertEqual(first.start, NOW)
        self.assertEqual((first.temperature, first.humidity), (29.0, 70.0))  # temperature filled
        self.assertAlmostEqual(first.wind_ms, 5.0)  # filled from the sensor
        self.assertAlmostEqual(second.temperature, 30.0)
        self.assertAlmostEqual(second.wind_ms, 10.0)
        self.assertEqual(second.humidity, 50.0)


class Occupancy(unittest.TestCase):
    def test_presence_openings_choices(self) -> None:
        states = base_states()
        states["binary_sensor.p"] = raw("binary_sensor.p", "on")
        states["binary_sensor.w"] = raw("binary_sensor.w", "on", age_s=120)
        states["binary_sensor.d"] = raw("binary_sensor.d", "open")
        ready = NOW + timedelta(hours=1)
        s = build(room(presence_sensor="binary_sensor.p", openings=("binary_sensor.w", "binary_sensor.d")),
                  states=states, choices=models.RoomChoices(leave_pressed_at=NOW, precool_ready_by=ready))
        self.assertEqual((s.presence.value, s.presence.tag), (True, VT.MEASURED))
        self.assertEqual((s.openings[0].value, s.openings[0].since), (True, NOW - timedelta(seconds=120)))
        self.assertEqual(s.openings[1].tag, VT.MISSING)
        self.assertEqual((s.leave_pressed_at, s.precool_ready_by), (NOW, ready))

    def test_unconfigured_is_none(self) -> None:
        s = build()
        self.assertIsNone(s.presence)
        self.assertEqual(s.openings, ())


class Tariff(unittest.TestCase):
    def test_now_and_ahead(self) -> None:
        def iv(h0: int, h1: int, price: float) -> Any:
            return models.TariffInterval(NOW + timedelta(hours=h0), NOW + timedelta(hours=h1), "r", price)
        later, now_iv, past = iv(2, 3, 0.4), iv(-1, 1, 0.2), iv(-3, -1, 0.1)
        s = build(tariff=[later, past, now_iv, iv(1, 2, 0.3)])
        self.assertEqual(s.tariff_now, now_iv)
        self.assertEqual([i.price for i in s.tariff_ahead], [0.3, 0.4])

    def test_an_interval_ends_where_the_next_begins(self) -> None:
        ended = models.TariffInterval(NOW - timedelta(hours=1), NOW, "old", 0.1)
        begun = models.TariffInterval(NOW, NOW + timedelta(hours=1), "new", 0.2)
        self.assertEqual(build(tariff=[ended, begun]).tariff_now, begun)


class History(unittest.TestCase):
    def test_at_or_before(self) -> None:
        h = observe.LoadHistory()
        h.add(NOW, 1.0)
        h.add(NOW + timedelta(seconds=10), 2.0)
        h.add(NOW + timedelta(seconds=20), None)
        self.assertIsNone(h.at_or_before(NOW - timedelta(seconds=1)))
        self.assertEqual(h.at_or_before(NOW), 1.0)
        self.assertEqual(h.at_or_before(NOW + timedelta(seconds=9)), 1.0)
        self.assertEqual(h.at_or_before(NOW + timedelta(seconds=10)), 2.0)
        self.assertEqual(h.at_or_before(NOW + timedelta(seconds=30)), 2.0)

    def test_keeps_ten_minutes(self) -> None:
        h = observe.LoadHistory()
        h.add(NOW, 1.0)
        h.add(NOW + timedelta(seconds=601), 2.0)
        self.assertEqual(len(h.samples), 1)


if __name__ == "__main__":
    unittest.main()
