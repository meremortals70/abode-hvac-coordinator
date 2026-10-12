"""Edges of the pure modules: the inputs a feed or a stored file can send.

Pure: no Home Assistant. Runs under pytest or `python -m unittest`, loaded the
same way as `test_core.py`, through a stand-in package so the integration's
`__init__.py` is never executed.
"""

from __future__ import annotations

import importlib
import math
import sys
import types
import unittest
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path
from unittest import mock

_SRC = Path(__file__).resolve().parents[1] / "custom_components" / "abode_hvac_coordinator"
if "hvac_core" not in sys.modules:
    _pkg = types.ModuleType("hvac_core")
    _pkg.__path__ = [str(_SRC)]
    sys.modules["hvac_core"] = _pkg

_capabilities = importlib.import_module("hvac_core.capabilities")
_const = importlib.import_module("hvac_core.const")
_forecast = importlib.import_module("hvac_core.forecast")
_forms = importlib.import_module("hvac_core.forms")
_hci = importlib.import_module("hvac_core.hci")
_power = importlib.import_module("hvac_core.power")
_sun = importlib.import_module("hvac_core.sun")
_weather = importlib.import_module("hvac_core.weather")

NOW = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
BRISBANE = timezone(timedelta(hours=10))


# ---------------------------------------------------------------------------
# forms.py - what the configuration menus print
# ---------------------------------------------------------------------------


class TestPowerManagementFromStoredValues(unittest.TestCase):
    """A stored value from any build reads as one of the three states."""

    def test_the_old_checkbox_true_reads_as_enforced(self):
        self.assertEqual(
            _forms.power_management_from_raw(True), _const.POWER_MANAGEMENT_ENFORCED
        )

    def test_the_old_checkbox_false_reads_as_off(self):
        self.assertEqual(
            _forms.power_management_from_raw(False), _const.POWER_MANAGEMENT_OFF
        )

    def test_each_current_state_reads_as_itself(self):
        for state in (
            _const.POWER_MANAGEMENT_OFF,
            _const.POWER_MANAGEMENT_GUIDANCE,
            _const.POWER_MANAGEMENT_ENFORCED,
        ):
            self.assertEqual(_forms.power_management_from_raw(state), state)

    def test_anything_unrecognised_reads_as_off_never_as_enforced(self):
        for value in ("Enforced", "strict", None, 1, 0.5, []):
            self.assertEqual(
                _forms.power_management_from_raw(value), _const.POWER_MANAGEMENT_OFF
            )


def _room(**extra):
    return {
        "name": "Living",
        _const.CONF_CLIMATE_ENTITIES: ["climate.living", "climate.dining"],
        _const.CONF_TEMPERATURE_ENTITY: "sensor.living_t",
        _const.CONF_HUMIDITY_ENTITY: "sensor.living_h",
        **extra,
    }


class TestDescribeRoom(unittest.TestCase):
    def test_each_head_names_its_outdoor_unit(self):
        text = _forms.describe_room(
            _room(**{_const.CONF_HEAD_GROUPS: {"climate.living": "outside_1"}})
        )
        self.assertIn("climate.living → outdoor unit: outside_1", text)
        self.assertNotIn("climate.dining →", text)

    def test_blinds_with_control_disabled_say_so(self):
        text = _forms.describe_room(
            _room(
                **{
                    _const.CONF_COVER_ENTITIES: ["cover.a", "cover.b"],
                    _const.CONF_ALLOW_COVER_CONTROL: False,
                }
            )
        )
        self.assertIn("Blinds: 2 configured, control disabled", text)

    def test_blinds_under_control_are_counted(self):
        text = _forms.describe_room(_room(**{_const.CONF_COVER_ENTITIES: ["cover.a"]}))
        self.assertIn("Blinds: 1", text)

    def test_an_overhang_is_described(self):
        text = _forms.describe_room(
            _room(
                **{
                    _const.CONF_OVERHANG_PROJECTION: 0.6,
                    _const.CONF_OVERHANG_HEIGHT: 0.4,
                }
            )
        )
        self.assertIn("Overhang: 0.6 m out, 0.4 m above the glass", text)

    def test_no_overhang_says_none(self):
        self.assertIn("Overhang: none", _forms.describe_room(_room()))

    def test_announcements_name_how_many_players(self):
        text = _forms.describe_room(
            _room(
                **{
                    _const.CONF_ANNOUNCE: True,
                    _const.CONF_ANNOUNCE_TARGETS: ["media_player.a", "media_player.b"],
                }
            )
        )
        self.assertIn("Announces before shutdown through 2 player(s)", text)

    def test_announcements_with_no_players_say_zero(self):
        text = _forms.describe_room(_room(**{_const.CONF_ANNOUNCE: True}))
        self.assertIn("through 0 player(s)", text)

    def test_a_lockout_is_shown_loudly(self):
        text = _forms.describe_room(
            _room(**{_const.CONF_LOCKOUT_REASON: "renovation"})
        )
        self.assertIn("**LOCKED OUT — renovation**", text)

    def test_no_bands_says_the_room_will_never_be_actuated(self):
        self.assertIn(
            "Bands: none — this room will never be actuated",
            _forms.describe_room(_room()),
        )

    def test_bands_are_listed(self):
        text = _forms.describe_room(
            _room(**{_const.CONF_BANDS: {"occupied": {"low": 25.0, "high": 28.0}}})
        )
        self.assertIn("Bands: occupied 25.0–28.0", text)


def _power_text(**overrides):
    values = {
        "battery_soc_entity_id": "sensor.soc",
        "battery_capacity_kwh": "13.5",
        "solar_entity_id": "sensor.solar",
        "house_load_entity_id": "sensor.load",
        "reserve_margin_kwh": "2",
        "grid_entity_id": "sensor.grid",
        "battery_max_discharge_kw": "5",
        "solar_forecasts": ["Solar North", "Solar West"],
    }
    values.update(overrides)
    return _forms.describe_power(**values)


class TestDescribePower(unittest.TestCase):
    """What the power step prints, including the 0.9.3 additions (DR-061, DR-063)."""

    def test_a_complete_setup_says_enforced_may_turn_the_unit_off(self):
        text = _power_text()
        self.assertIn("Solar forecast: Solar North, Solar West", text)
        self.assertIn(
            "Where even holding an enforced room cannot be afforded, it is "
            "turned off until it can.",
            text,
        )
        self.assertNotIn("Without a solar forecast", text)
        self.assertNotIn("Without the maximum discharge power", text)
        self.assertNotIn("Without the grid sensor", text)

    def test_without_a_forecast_it_says_the_morning_sun_is_not_counted(self):
        text = _power_text(solar_forecasts=None)
        self.assertIn("Solar forecast: Nothing selected", text)
        self.assertIn("at sunrise the morning's generation is not", text)

    def test_without_the_discharge_limit_it_says_what_bounds_the_throttle(self):
        text = _power_text(battery_max_discharge_kw=None)
        self.assertIn("Battery max discharge power: Not set", text)
        self.assertIn("bounded only by stored energy", text)

    def test_without_the_grid_sensor_it_says_no_breach_is_measured(self):
        text = _power_text(grid_entity_id=None)
        self.assertIn("Grid power sensor: Nothing selected", text)
        self.assertIn("no breach is measured afterward", text)

    def test_a_partial_setup_is_not_described_as_active(self):
        text = _power_text(house_load_entity_id=None)
        self.assertIn("House load sensor: Nothing selected", text)
        self.assertNotIn("Active:", text)


# ---------------------------------------------------------------------------
# hci.py - the comfort index and its inverse
# ---------------------------------------------------------------------------


class TestComfortIndexEdges(unittest.TestCase):
    def test_a_target_below_the_solvable_range_clamps_to_its_floor(self):
        self.assertEqual(_hci.dry_bulb_for_index(-50.0, 50.0), _hci.SOLVE_MIN_C)

    def test_a_target_above_the_solvable_range_clamps_to_its_ceiling(self):
        self.assertEqual(_hci.dry_bulb_for_index(90.0, 50.0), _hci.SOLVE_MAX_C)

    def test_the_solver_still_answers_when_it_runs_out_of_iterations(self):
        """The midpoint of the last bracket, inside the range, never an error."""
        with mock.patch.object(_hci, "_SOLVE_MAX_ITERATIONS", 2):
            answer = _hci.dry_bulb_for_index(26.0, 50.0)
        self.assertGreater(answer, _hci.SOLVE_MIN_C)
        self.assertLess(answer, _hci.SOLVE_MAX_C)

    def test_the_solver_inverts_the_index(self):
        target = _hci.comfort_index(26.0, 55.0)
        answer = _hci.dry_bulb_for_index(target, 55.0)
        self.assertAlmostEqual(answer, 26.0, places=2)

    def test_humidity_where_saturation_underflows_keeps_the_reading(self):
        """Just inside the formula's domain the saturation curve reaches zero."""
        self.assertEqual(
            _hci.relative_humidity_at_constant_vapour_pressure(25.0, 60.0, -237.69),
            60.0,
        )

    def test_humidity_outside_the_formulas_domain_keeps_the_reading(self):
        """A diverged learned rate must not overflow and stop the evaluation."""
        self.assertEqual(
            _hci.relative_humidity_at_constant_vapour_pressure(25.0, 60.0, -300.0),
            60.0,
        )

    def test_cooler_air_at_the_same_moisture_reads_more_humid(self):
        self.assertGreater(
            _hci.relative_humidity_at_constant_vapour_pressure(26.0, 55.0, 22.0), 55.0
        )

    def test_a_band_contains_its_edges(self):
        band = _hci.ComfortBand(24.0, 26.0)
        self.assertTrue(band.contains(24.0))
        self.assertTrue(band.contains(26.0))
        self.assertFalse(band.contains(26.01))
        self.assertEqual(band.midpoint, 25.0)


# ---------------------------------------------------------------------------
# sun.py - window geometry
# ---------------------------------------------------------------------------


class TestSunGeometryEdges(unittest.TestCase):
    def test_a_negative_overhang_is_no_overhang(self):
        self.assertIsNone(_sun.shading_elevation(-0.5, 0.4))

    def test_an_overhang_with_no_height_is_no_overhang(self):
        self.assertIsNone(_sun.shading_elevation(0.5, None))

    def test_a_sun_along_the_wall_is_not_shaded_by_the_overhang(self):
        """Ninety degrees off the window's normal, the overhang casts nothing."""
        self.assertTrue(
            _sun.sun_on_window(
                180.0, 30.0, 90.0, overhang_projection_m=1.0, overhang_height_m=0.2
            )
        )

    def test_a_high_sun_head_on_is_shaded_by_the_overhang(self):
        self.assertFalse(
            _sun.sun_on_window(
                90.0, 80.0, 90.0, overhang_projection_m=1.0, overhang_height_m=0.5
            )
        )


# ---------------------------------------------------------------------------
# power.py - the forecast and the budget
# ---------------------------------------------------------------------------


class TestSolarForecastAnswers(unittest.TestCase):
    """Whatever a forecast integration answers, nothing is guessed (DR-061)."""

    def test_an_answer_that_is_not_a_mapping_has_no_points(self):
        self.assertEqual(_power.solar_points_from_forecast(None, UTC), [])
        self.assertEqual(_power.solar_points_from_forecast([1, 2], UTC), [])

    def test_an_answer_without_hourly_watt_hours_has_no_points(self):
        self.assertEqual(_power.solar_points_from_forecast({"wh_hours": [1]}, UTC), [])

    def test_unparseable_and_non_finite_entries_are_skipped(self):
        points = _power.solar_points_from_forecast(
            {
                "wh_hours": {
                    "not a time": 100,
                    "2026-10-12T07:00:00+10:00": "lots",
                    "2026-10-12T08:00:00+10:00": float("nan"),
                    "2026-10-12T09:00:00+10:00": float("inf"),
                    "2026-10-12T10:00:00+10:00": 1500,
                }
            },
            UTC,
        )
        self.assertEqual(
            points, [(datetime(2026, 10, 12, 10, 0, tzinfo=BRISBANE), 1500.0)]
        )

    def test_a_time_without_an_offset_is_local(self):
        points = _power.solar_points_from_forecast(
            {"wh_hours": {"2026-10-12T10:00:00": 900}}, BRISBANE
        )
        self.assertEqual(points[0][0].utcoffset(), timedelta(hours=10))

    def test_the_energy_dashboard_lists_only_solar_sources_without_repeats(self):
        found = _power.energy_dashboard_solar_forecasts(
            {
                "energy_sources": [
                    {"type": "grid", "config_entry_solar_forecast": ["x"]},
                    "not a source",
                    {"type": "solar", "config_entry_solar_forecast": ["a", "b", 3]},
                    {"type": "solar", "config_entry_solar_forecast": ["b", "c"]},
                    {"type": "solar"},
                ]
            }
        )
        self.assertEqual(found, ["a", "b", "c"])

    def test_no_energy_settings_means_no_default(self):
        self.assertEqual(_power.energy_dashboard_solar_forecasts(None), [])
        self.assertEqual(_power.energy_dashboard_solar_forecasts({}), [])


class TestForecastSliceEdges(unittest.TestCase):
    def test_a_window_already_over_has_no_slices(self):
        self.assertEqual(_power.forecast_slices([(NOW, 1000.0)], NOW, NOW), [])

    def test_points_entirely_before_now_bring_no_sun(self):
        """Uncovered time is a slice with no energy, not a gap in the walk."""
        slices = _power.forecast_slices(
            [(NOW - timedelta(hours=3), 5000.0), (NOW - timedelta(hours=2), 5000.0)],
            NOW,
            NOW + timedelta(hours=1),
        )
        self.assertEqual(slices, [(1.0, 0.0)])

    def test_a_point_after_the_window_brings_no_sun(self):
        slices = _power.forecast_slices(
            [(NOW + timedelta(hours=5), 5000.0)], NOW, NOW + timedelta(hours=2)
        )
        self.assertEqual(slices, [(2.0, 0.0)])

    def test_two_points_at_the_same_time_count_as_one(self):
        slices = _power.forecast_slices(
            [(NOW, 1000.0), (NOW, 2000.0)], NOW, NOW + timedelta(hours=1)
        )
        self.assertEqual(sum(kwh for _, kwh in slices), 2.0)

    def test_a_negative_forecast_counts_as_no_sun(self):
        slices = _power.forecast_slices([(NOW, -500.0)], NOW, NOW + timedelta(hours=1))
        self.assertEqual(slices, [(1.0, 0.0)])


class TestBudgetEdges(unittest.TestCase):
    def test_no_time_left_in_the_window_allows_nothing(self):
        self.assertEqual(
            _power.forecast_allowance_kw(
                available_kwh=10.0,
                headroom_kwh=5.0,
                slices=[],
                other_house_kw=0.5,
                plant_kw=5.0,
            ),
            0.0,
        )

    def test_no_discharge_limit_means_no_plant_limit(self):
        self.assertIsNone(_power.plant_allowance_kw(None, 2.0, 0.5))

    def test_the_plant_limit_is_never_negative(self):
        self.assertEqual(_power.plant_allowance_kw(1.0, 0.0, 3.0), 0.0)

    def test_the_plant_limit_adds_the_sun_and_takes_the_house(self):
        self.assertEqual(_power.plant_allowance_kw(5.0, 2.0, 1.5), 5.5)


# ---------------------------------------------------------------------------
# weather.py - the forecast payload
# ---------------------------------------------------------------------------


def _hour(hours: int, temperature: float = 25.0, **fields) -> dict:
    return {
        "datetime": (NOW + timedelta(hours=hours)).isoformat(),
        "temperature": temperature,
        **fields,
    }


class TestWeatherPayloadEdges(unittest.TestCase):
    def test_a_bare_forecast_list_is_read(self):
        t = _weather.WeatherTrajectory.from_response({"forecast": [_hour(0)]}, NOW)
        self.assertEqual(len(t.points), 1)

    def test_a_response_with_no_forecast_list_is_refused(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.WeatherTrajectory.from_response({"weather.home": {"x": 1}}, NOW)

    def test_an_entity_entry_that_is_not_a_mapping_is_passed_over(self):
        t = _weather.WeatherTrajectory.from_response(
            {"weather.bad": "nothing", "weather.home": {"forecast": [_hour(0)]}}, NOW
        )
        self.assertEqual(len(t.points), 1)

    def test_an_empty_forecast_list_is_refused(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.WeatherTrajectory.from_response({"forecast": []}, NOW)

    def test_a_list_with_no_usable_point_is_refused(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.WeatherTrajectory.from_response({"forecast": ["x", 3]}, NOW)

    def test_a_non_mapping_response_is_refused(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.WeatherTrajectory.from_response(None, NOW)

    def test_a_point_already_holding_a_datetime_is_used_as_it_is(self):
        point = _weather.point_from_forecast({"datetime": NOW, "temperature": 20})
        self.assertEqual(point.at, NOW)

    def test_a_point_with_no_parseable_time_is_refused(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.point_from_forecast({"datetime": "tomorrow", "temperature": 20})

    def test_a_point_with_no_time_at_all_is_refused(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.point_from_forecast({"temperature": 20})

    def test_a_point_with_no_temperature_is_refused(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.point_from_forecast({"datetime": NOW.isoformat()})

    def test_an_optional_field_that_is_not_a_number_is_absent(self):
        point = _weather.point_from_forecast(
            {"datetime": NOW.isoformat(), "temperature": 20, "humidity": "damp"}
        )
        self.assertIsNone(point.humidity)

    def test_a_window_the_forecast_does_not_reach_has_no_mean(self):
        t = _weather.WeatherTrajectory.from_response({"forecast": [_hour(0)]}, NOW)
        self.assertIsNone(
            t.mean_between(NOW + timedelta(hours=5), NOW + timedelta(hours=8))
        )

    def test_a_forecast_that_stops_short_sees_no_load_ahead(self):
        t = _weather.WeatherTrajectory.from_response(
            {"forecast": [_hour(-10, 35.0)]}, NOW
        )
        verdict = _weather.demand_ahead(t, now=NOW, indoor_c=24.0)
        self.assertFalse(verdict.demand_ahead)
        self.assertIn("does not reach far enough ahead", verdict.reason)


# ---------------------------------------------------------------------------
# capabilities.py and forecast.py - small edges
# ---------------------------------------------------------------------------


class TestSmallEdges(unittest.TestCase):
    def test_no_heads_have_nothing_in_common(self):
        self.assertEqual(_capabilities._common([]), ())

    def test_a_window_that_starts_and_ends_together_covers_the_whole_day(self):
        hours = _forecast._hours_in_window(
            start_at=NOW,
            horizon_hours=2.0,
            window_start=time(14, 0),
            window_end=time(14, 0),
        )
        self.assertTrue(math.isclose(hours, 2.0))


if __name__ == "__main__":
    unittest.main()


class TestBudgetReadings(unittest.TestCase):
    """0.9.4, DR-067. What the budget reads, and when it turns a room off."""

    def test_the_mean_of_the_last_fifteen_minutes(self):
        samples = [
            (NOW - timedelta(minutes=20), 5000.0),
            (NOW - timedelta(minutes=10), 600.0),
            (NOW, 1000.0),
        ]
        self.assertEqual(
            _power.mean_recent(samples, _power.READING_AVERAGE_WINDOW), 800.0
        )

    def test_a_series_no_longer_added_to_keeps_its_average(self):
        """A room's own samples stop while it runs; its last average stands."""
        samples = [(NOW - timedelta(hours=2, minutes=m), 650.0) for m in (0, 5, 10)]
        self.assertEqual(
            _power.mean_recent(samples, _power.READING_AVERAGE_WINDOW), 650.0
        )

    def test_no_samples_is_no_average(self):
        self.assertIsNone(_power.mean_recent([], _power.READING_AVERAGE_WINDOW))

    def test_an_afforded_room_clears_the_shortfall(self):
        self.assertEqual(
            _power.hold_before_off(2, was_on=True, shortfall_since=NOW, now=NOW),
            (2, None),
        )

    def test_a_running_room_is_held_at_the_gentlest_bin(self):
        since = NOW - timedelta(minutes=9)
        self.assertEqual(
            _power.hold_before_off(None, was_on=True, shortfall_since=since, now=NOW),
            (0, since),
        )

    def test_a_new_shortfall_starts_the_hold_now(self):
        self.assertEqual(
            _power.hold_before_off(None, was_on=True, shortfall_since=None, now=NOW),
            (0, NOW),
        )

    def test_after_the_hold_the_room_is_turned_off(self):
        since = NOW - _power.OFF_HOLD
        self.assertEqual(
            _power.hold_before_off(None, was_on=True, shortfall_since=since, now=NOW),
            (None, since),
        )

    def test_a_room_already_off_stays_off(self):
        self.assertEqual(
            _power.hold_before_off(None, was_on=False, shortfall_since=None, now=NOW),
            (None, NOW),
        )
