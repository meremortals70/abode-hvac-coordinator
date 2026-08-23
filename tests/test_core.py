"""Tests for the pure modules. No Home Assistant required.

Run from the repository root:   python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path

# The package __init__ imports Home Assistant, which is not installed here and
# is not needed: hci, models, modes and tariff are pure. Register a stand-in
# package pointing at the source directory so the relative imports inside those
# modules resolve without __init__.py ever being executed.
_SRC = Path(__file__).resolve().parents[1] / "custom_components" / "abode_hvac_coordinator"
_pkg = types.ModuleType("hvac_core")
_pkg.__path__ = [str(_SRC)]
sys.modules["hvac_core"] = _pkg

_hci = importlib.import_module("hvac_core.hci")
_models = importlib.import_module("hvac_core.models")
_modes = importlib.import_module("hvac_core.modes")
_tariff = importlib.import_module("hvac_core.tariff")

ComfortBand = _hci.ComfortBand
comfort_index = _hci.comfort_index
dry_bulb_for_index = _hci.dry_bulb_for_index
ActuatorStep = _models.ActuatorStep
Mode = _models.Mode
RoomConfig = _models.RoomConfig
RoomInputs = _models.RoomInputs
evaluate_room = _modes.evaluate_room
TariffSeries = _tariff.TariffSeries
Interval = _tariff.Interval
interval_from_response = _tariff.interval_from_response
TariffPayloadError = _tariff.TariffPayloadError

NOW = datetime(2026, 8, 8, 14, 30, tzinfo=UTC)

# Test fixtures only. No site data lives in source or in tests.
BANDS = {
    Mode.SLEEP: ComfortBand(24.0, 26.0),
    Mode.OCCUPIED: ComfortBand(25.0, 28.0),
    Mode.PRECOOL: ComfortBand(25.0, 28.0),
}

#: The wall clock the tariff fixture belongs to. A window is a wall-clock
#: concept, so the series has to be told which clock, or a 16:00 peak built at
#: +10:00 collapses to an 06:00 window.
FIXTURE_TZ = timezone(timedelta(hours=10))


#: A day as Abode Power Tariffs publishes it: half-hourly, dollars per kWh.
def _response(day: str = "2026-08-08", tz: str = "+10:00") -> dict:
    """Build a half-hourly response for one day. Fixture values only."""
    intervals = []
    for slot in range(48):
        hour, minute = divmod(slot * 30, 60)
        end_hour, end_minute = divmod((slot + 1) * 30, 60)
        peak = hour >= 12
        end = (
            f"{day}T{end_hour:02d}:{end_minute:02d}:00{tz}"
            if end_hour < 24
            else f"2026-08-09T00:00:00{tz}"
        )
        intervals.append(
            {
                "start_time": f"{day}T{hour:02d}:{minute:02d}:00{tz}",
                "end_time": end,
                "duration": 30,
                "per_kwh": 0.489 if peak else 0.225,
                "export_per_kwh": 0.05,
                "rate": "peak" if peak else "off_peak",
                "constraints": (
                    ["no_grid_import", "precool_opportunity"] if peak else []
                ),
                "coasting_permitted": bool(peak),
                "allowance_kwh": None,
                "day_pattern": "Every day",
                "forecast": False,
            }
        )
    return {"intervals": intervals}


def room(**overrides) -> RoomConfig:
    base = {
        "room_id": "office",
        "name": "Office",
        "climate_entity_ids": ("climate.office",),
        "bands": BANDS,
    }
    base.update(overrides)
    return RoomConfig(**base)


class TestComfortIndex(unittest.TestCase):
    def test_humidity_raises_the_index_at_the_same_temperature(self):
        dry = comfort_index(24.0, 35.0)
        humid = comfort_index(24.0, 85.0)
        self.assertGreater(humid, dry)

    def test_index_is_monotonic_in_temperature(self):
        values = [comfort_index(t, 60.0) for t in range(16, 32)]
        self.assertEqual(values, sorted(values))

    def test_inverse_round_trips(self):
        for target in (18.0, 20.0, 23.5, 26.0):
            for rh in (30.0, 55.0, 80.0):
                dry_bulb = dry_bulb_for_index(target, rh)
                self.assertAlmostEqual(
                    comfort_index(dry_bulb, rh), target, places=2
                )

    def test_humid_night_gives_a_lower_setpoint_than_a_dry_one(self):
        """The whole reason the user never sets a setpoint."""
        humid = dry_bulb_for_index(19.0, 85.0)
        dry = dry_bulb_for_index(19.0, 40.0)
        self.assertLess(humid, dry)

    def test_band_rejects_inverted_bounds(self):
        with self.assertRaises(ValueError):
            ComfortBand(25.0, 22.0)


class TestModePrecedence(unittest.TestCase):
    def test_lockout_beats_everything(self):
        trace = evaluate_room(
            room(lockout_reason="upstairs renovation"),
            RoomInputs(
                now=NOW,
                temperature_c=34.0,
                relative_humidity=80.0,
                presence=True,
                heading_home=True,
            ),
        )
        self.assertIs(trace.mode, Mode.LOCKOUT)
        self.assertIs(trace.actuator, ActuatorStep.OFF)
        self.assertIn("upstairs renovation", " ".join(trace.reasons))

    def test_precondition_beats_presence(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=28.0,
                relative_humidity=60.0,
                presence=False,
                heading_home=True,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECONDITION)

    def test_unknown_presence_holds_occupied(self):
        trace = evaluate_room(
            room(),
            RoomInputs(now=NOW, temperature_c=26.0, relative_humidity=60.0),
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)

    def test_sleep_needs_presence_and_schedule(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=22.0,
                relative_humidity=60.0,
                presence=True,
                sleep_schedule_active=True,
            ),
        )
        self.assertIs(trace.mode, Mode.SLEEP)
        self.assertEqual(trace.band_low, 24.0)

    def test_coast_carries_the_displaced_band(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=23.0,
                relative_humidity=55.0,
                presence=True,
                predicted_to_hold=True,
            ),
        )
        self.assertIs(trace.mode, Mode.COAST)
        self.assertIs(trace.base_mode, Mode.OCCUPIED)
        self.assertEqual(trace.band_low, 25.0)
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_cheap_window_does_not_coast_even_when_it_would_hold(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=23.0,
                relative_humidity=55.0,
                presence=True,
                predicted_to_hold=True,
                coasting_permitted=False,
            ),
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)
        self.assertTrue(any("coast" in r for r in trace.rejected))

    def test_a_cheaper_window_imminent_coasts_even_when_it_would_not_hold_forever(
        self,
    ):
        """0.8.11, finding 12. `predicted_to_hold` is False (the model does
        not trust the band to hold indefinitely unaided) but a cheaper
        tariff window begins soon enough that the model has separately said
        the band holds until then — the cheapest way to deliver the same
        comfort outcome is to wait for it.
        """
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=23.0,
                relative_humidity=55.0,
                presence=True,
                predicted_to_hold=False,
                cheaper_window_imminent=True,
            ),
        )
        self.assertIs(trace.mode, Mode.COAST)
        self.assertIs(trace.base_mode, Mode.OCCUPIED)
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_a_window_that_forbids_coasting_also_forbids_the_price_defer(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=23.0,
                relative_humidity=55.0,
                presence=True,
                predicted_to_hold=False,
                cheaper_window_imminent=True,
                coasting_permitted=False,
            ),
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)
        self.assertTrue(any("coast" in r for r in trace.rejected))

    def test_no_cheaper_window_and_no_unaided_hold_runs_now(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=23.0,
                relative_humidity=55.0,
                presence=True,
                predicted_to_hold=False,
                cheaper_window_imminent=False,
            ),
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)

    def test_precool_needs_both_the_window_and_demand_ahead(self):
        without = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                precool_opportunity=True,
            ),
        )
        self.assertIsNot(without.mode, Mode.PRECOOL)

        with_demand = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                precool_opportunity=True,
                forecast_demand_ahead=True,
            ),
        )
        self.assertIs(with_demand.mode, Mode.PRECOOL)

    def test_precool_targets_the_low_bound(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                precool_opportunity=True,
                forecast_demand_ahead=True,
                air_moving=True,
            ),
        )
        expected = dry_bulb_for_index(25.0, 55.0)
        self.assertAlmostEqual(trace.target_dry_bulb_c, expected, places=3)


class TestActuatorOrdering(unittest.TestCase):
    """Cheapest first: covers, fan, dry, compressor. Nothing skips a step."""

    def test_open_window_stops_everything(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=32.0,
                relative_humidity=70.0,
                presence=True,
                opening_open=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_covers_come_first_when_there_is_sun_to_block(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=70.0,
                presence=True,
                has_covers=True,
                direct_sun=True,
                cover_position=100.0,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COVERS)
        self.assertEqual(trace.demand, "cool")

    def test_covers_are_not_moved_when_no_sun_is_on_the_room(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                direct_sun=False,
            ),
        )
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("no sun on this room" in r for r in trace.rejected))

    def test_fan_when_marginally_above_band(self):
        # Band high is 28.0; the fan margin is 0.5 HCI. 28 C at 35% reads 28.35
        # with the air already moving, which is the case where a fan is the
        # right answer rather than an escalation.
        temp = 28.0
        rh = 35.0
        self.assertGreater(comfort_index(temp, rh), 28.0)
        self.assertLess(comfort_index(temp, rh), 28.5)
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=temp,
                relative_humidity=rh,
                presence=True,
                air_moving=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.FAN)

    def test_dry_mode_when_the_load_is_latent(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=28.0,
                relative_humidity=80.0,
                presence=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.DRY)
        self.assertTrue(any("fan" in r for r in trace.rejected))

    def test_compressor_only_after_everything_else_is_ruled_out(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=33.0,
                relative_humidity=35.0,
                presence=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        rejected = " ".join(trace.rejected)
        self.assertIn("covers", rejected)
        self.assertIn("fan", rejected)
        self.assertIn("dry", rejected)

    def test_heating_never_reaches_for_fan_or_dry(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=14.0,
                relative_humidity=50.0,
                presence=True,
            ),
        )
        self.assertEqual(trace.demand, "heat")
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("neither adds heat" in r for r in trace.rejected))

    def test_covers_admit_gain_when_the_room_is_too_cold(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=14.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                direct_sun=True,
                cover_position=0.0,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("admitting" in r for r in trace.reasons))

    def test_precool_stops_once_it_reaches_the_low_bound(self):
        """Precool drives to the low bound, then stops. It does not run on."""
        inputs = {
            "now": NOW,
            "relative_humidity": 50.0,
            "presence": True,
            "precool_opportunity": True,
            "forecast_demand_ahead": True,
        }
        # Below the low bound of 25.0 — nothing left to bank. 23 C at 50%
        # reads 23.6.
        cold = evaluate_room(room(), RoomInputs(temperature_c=23.0, **inputs))
        self.assertIs(cold.mode, Mode.PRECOOL)
        self.assertIsNone(cold.demand)
        self.assertIs(cold.actuator, ActuatorStep.NONE)

        # Inside the band but above the low bound — still banking. 26 C at
        # 50% reads 27.5, between the 25.0 low and the 28.0 high.
        warm = evaluate_room(room(), RoomInputs(temperature_c=26.0, **inputs))
        self.assertIs(warm.mode, Mode.PRECOOL)
        self.assertEqual(warm.demand, "cool")
        self.assertIsNot(warm.actuator, ActuatorStep.NONE)

    def test_no_actuation_without_a_reading(self):
        trace = evaluate_room(room(), RoomInputs(now=NOW, presence=True))
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertIsNone(trace.hci)

    def test_trace_is_always_produced(self):
        trace = evaluate_room(room(), RoomInputs(now=NOW))
        self.assertEqual(trace.room_id, "office")
        self.assertIn("mode", trace.as_attributes())


if __name__ == "__main__":
    unittest.main()


class TestTariffSeries(unittest.TestCase):
    """The series replaces the schedule this controller used to hold itself."""

    def setUp(self):
        self.series = TariffSeries.from_response(_response(), NOW, FIXTURE_TZ)

    def _at(self, hour: int, minute: int = 0):
        return datetime(
            2026, 8, 8, hour, minute, tzinfo=timezone(timedelta(hours=10))
        )

    def test_every_half_hour_of_the_day_resolves(self):
        for slot in range(48):
            hour, minute = divmod(slot * 30, 60)
            self.assertIsNotNone(self.series.interval_at(self._at(hour, minute)))

    def test_the_rate_in_force_is_the_publishers_label(self):
        self.assertEqual(self.series.interval_at(self._at(3)).rate, "off_peak")
        self.assertEqual(self.series.interval_at(self._at(18)).rate, "peak")

    def test_constraints_are_carried_through_unchanged(self):
        interval = self.series.interval_at(self._at(18))
        self.assertIn("no_grid_import", interval.constraints)
        self.assertIn("precool_opportunity", interval.constraints)

    def test_prices_stay_in_dollars(self):
        # abode_power_tariffs publishes per_kwh in dollars. Converting here is
        # how the wrong unit ends up on a dashboard.
        self.assertEqual(self.series.interval_at(self._at(18)).per_kwh, 0.489)

    def test_an_instant_past_the_horizon_resolves_to_nothing(self):
        self.assertIsNone(
            self.series.interval_at(self._at(0) + timedelta(days=2))
        )

    def test_the_end_of_an_interval_belongs_to_the_next_one(self):
        self.assertEqual(self.series.interval_at(self._at(12)).rate, "peak")
        self.assertEqual(self.series.interval_at(self._at(11, 30)).rate, "off_peak")


class TestCheaperIntervalAhead(unittest.TestCase):
    """0.8.11, finding 12."""

    def _series(self, prices: list[float]) -> TariffSeries:
        """A run of consecutive 30-minute intervals starting at midnight,
        one per given price."""
        response = {
            "intervals": [
                {
                    "start_time": f"2026-08-08T{h:02d}:{m:02d}:00+10:00",
                    "end_time": f"2026-08-08T{eh:02d}:{em:02d}:00+10:00",
                    "duration": 30,
                    "per_kwh": price,
                    "export_per_kwh": 0.05,
                    "rate": "rate",
                    "constraints": [],
                    "coasting_permitted": True,
                    "allowance_kwh": None,
                    "day_pattern": "Every day",
                    "forecast": False,
                }
                for index, price in enumerate(prices)
                for h, m in [divmod(index * 30, 60)]
                for eh, em in [divmod((index + 1) * 30, 60)]
            ]
        }
        return TariffSeries.from_response(response, NOW, FIXTURE_TZ)

    def _at(self, hour: int, minute: int = 0):
        return datetime(
            2026, 8, 8, hour, minute, tzinfo=timezone(timedelta(hours=10))
        )

    def test_a_cheaper_interval_within_the_horizon_is_found(self):
        series = self._series([0.40, 0.40, 0.20, 0.20])
        found = series.cheaper_interval_ahead(
            self._at(0, 0), 0.40, timedelta(hours=1)
        )
        self.assertEqual(found, self._at(1, 0))

    def test_no_cheaper_interval_returns_none(self):
        series = self._series([0.40, 0.40, 0.40, 0.40])
        found = series.cheaper_interval_ahead(
            self._at(0, 0), 0.40, timedelta(hours=1)
        )
        self.assertIsNone(found)

    def test_a_cheaper_interval_beyond_the_horizon_is_not_found(self):
        series = self._series([0.40, 0.40, 0.40, 0.20])
        found = series.cheaper_interval_ahead(
            self._at(0, 0), 0.40, timedelta(minutes=30)
        )
        self.assertIsNone(found)

    def test_a_missing_price_is_skipped_not_treated_as_free(self):
        series = self._series([0.40, 0.40, 0.20, 0.20])
        # Blank out the cheap interval's price directly on the built series.
        intervals = list(series.intervals)
        intervals[2] = replace(intervals[2], per_kwh=None)
        patched = TariffSeries(tuple(intervals), NOW)
        found = patched.cheaper_interval_ahead(
            self._at(0, 0), 0.40, timedelta(hours=1, minutes=30)
        )
        self.assertEqual(found, self._at(1, 30))


class TestSeriesCollapsesToPeriods(unittest.TestCase):
    """Forty-eight slices are two periods. The forecast wants the periods."""

    def setUp(self):
        self.windows = TariffSeries.from_response(
            _response(), NOW, FIXTURE_TZ
        ).windows()

    def test_consecutive_identical_intervals_become_one_window(self):
        self.assertEqual(len(self.windows), 2)

    def test_the_collapsed_window_spans_the_whole_run(self):
        first, second = self.windows
        self.assertEqual(first.start, time(0, 0))
        self.assertEqual(first.end, time(12, 0))
        self.assertEqual(second.start, time(12, 0))
        self.assertEqual(second.end, time(0, 0))

    def test_a_collapsed_window_keeps_its_rules(self):
        _, peak = self.windows
        self.assertEqual(peak.rate, "peak")
        self.assertTrue(peak.coasting_permitted)
        self.assertIn("no_grid_import", peak.constraints)

    def test_a_price_change_mid_run_does_not_split_the_window(self):
        # Rate, constraints and coasting decide a period. Price does not: a
        # window that repriced partway through was not one period in the plan.
        response = _response()
        response["intervals"][4]["per_kwh"] = 0.30
        windows = TariffSeries.from_response(response, NOW, FIXTURE_TZ).windows()
        self.assertEqual(len(windows), 2)

    def test_a_gap_in_the_series_starts_a_new_window(self):
        response = _response()
        del response["intervals"][6]
        windows = TariffSeries.from_response(response, NOW, FIXTURE_TZ).windows()
        self.assertEqual(len(windows), 3)


class TestUnrecognisedConstraints(unittest.TestCase):
    """A constraint meant for another system is reported, never dropped."""

    def test_a_constraint_this_controller_does_not_act_on_is_surfaced(self):
        response = _response()
        response["intervals"][0]["constraints"] = ["grid_charge_battery"]
        series = TariffSeries.from_response(response, NOW, FIXTURE_TZ)
        self.assertEqual(series.unrecognised_constraints(), frozenset())

    def test_an_unknown_constraint_is_named(self):
        response = _response()
        response["intervals"][0]["constraints"] = ["run_the_pool_pump"]
        series = TariffSeries.from_response(response, NOW, FIXTURE_TZ)
        self.assertIn("run_the_pool_pump", series.unrecognised_constraints())


class TestHoursUntilClear(unittest.TestCase):
    """How long a room needs carrying on battery or solar before a constraint lifts."""

    def _at(self, hour: int, minute: int = 0):
        return datetime(
            2026, 8, 8, hour, minute, tzinfo=timezone(timedelta(hours=10))
        )

    def test_zero_when_the_constraint_is_not_currently_in_force(self):
        # off_peak, 00:00-12:00, carries neither constraint in the fixture.
        series = TariffSeries.from_response(_response(), NOW, FIXTURE_TZ)
        self.assertEqual(
            series.hours_until_clear("no_grid_import", self._at(3)), 0.0
        )

    def test_counts_forward_to_the_interval_that_drops_the_constraint(self):
        # The standard one-day fixture's peak runs right to the edge of the
        # fetched series, so it can never demonstrate clearing — extend it
        # with the next day's first off-peak slot, six hours after 18:00.
        response = _response()
        response["intervals"].append(
            {
                "start_time": "2026-08-09T00:00:00+10:00",
                "end_time": "2026-08-09T00:30:00+10:00",
                "duration": 30,
                "per_kwh": 0.225,
                "export_per_kwh": 0.05,
                "rate": "off_peak",
                "constraints": [],
                "coasting_permitted": True,
                "allowance_kwh": None,
                "day_pattern": "Every day",
                "forecast": False,
            }
        )
        series = TariffSeries.from_response(response, NOW, FIXTURE_TZ)
        self.assertAlmostEqual(
            series.hours_until_clear("no_grid_import", self._at(18)), 6.0
        )

    def test_none_when_the_series_never_clears_within_its_horizon(self):
        response = _response()
        for interval in response["intervals"]:
            interval["constraints"] = ["no_grid_import"]
        series = TariffSeries.from_response(response, NOW, FIXTURE_TZ)
        self.assertIsNone(
            series.hours_until_clear("no_grid_import", self._at(18))
        )

    def test_a_constraint_never_declared_reads_as_already_clear(self):
        series = TariffSeries.from_response(_response(), NOW, FIXTURE_TZ)
        self.assertEqual(
            series.hours_until_clear("grid_charge_battery", self._at(18)), 0.0
        )


class TestMalformedTariffPayloads(unittest.TestCase):
    """A bad payload must say why, not fail silently or take rooms down."""

    def test_a_response_that_is_not_a_mapping_is_rejected(self):
        with self.assertRaises(TariffPayloadError):
            TariffSeries.from_response(None, NOW)

    def test_a_response_with_no_intervals_is_rejected(self):
        with self.assertRaises(TariffPayloadError):
            TariffSeries.from_response({"intervals": []}, NOW)

    def test_an_interval_with_no_rate_is_rejected(self):
        with self.assertRaises(TariffPayloadError):
            interval_from_response(
                {"start_time": "2026-08-08T00:00:00+10:00",
                 "end_time": "2026-08-08T00:30:00+10:00"}
            )

    def test_an_unparseable_timestamp_is_rejected(self):
        with self.assertRaises(TariffPayloadError):
            interval_from_response(
                {"start_time": "half past three", "end_time": "later", "rate": "peak"}
            )

    def test_a_non_numeric_price_is_rejected(self):
        with self.assertRaises(TariffPayloadError):
            interval_from_response(
                {
                    "start_time": "2026-08-08T00:00:00+10:00",
                    "end_time": "2026-08-08T00:30:00+10:00",
                    "rate": "peak",
                    "per_kwh": "free",
                }
            )

    def test_an_absent_price_is_allowed(self):
        interval = interval_from_response(
            {
                "start_time": "2026-08-08T00:00:00+10:00",
                "end_time": "2026-08-08T00:30:00+10:00",
                "rate": "peak",
            }
        )
        self.assertIsNone(interval.per_kwh)
        self.assertTrue(interval.coasting_permitted)


class TestSeriesFeedsTheForecast(unittest.TestCase):
    """What the series produces must be what build_forecast accepts."""

    def test_collapsed_windows_are_the_shape_the_forecast_takes(self):
        windows = TariffSeries.from_response(_response(), NOW, FIXTURE_TZ).windows()
        shaped = [(w.start, w.end, w.rate, w.constraints) for w in windows]
        for start, end, rate, constraints in shaped:
            self.assertIsInstance(start, time)
            self.assertIsInstance(end, time)
            self.assertIsInstance(rate, str)
            self.assertIsInstance(constraints, frozenset)


class TestUnoccupiedAndHeadingHome(unittest.TestCase):
    """An unoccupied room is off. Heading home is the only thing that overrides it."""

    def test_unoccupied_never_actuates_however_hot(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=34.0,
                relative_humidity=80.0,
                presence=False,
            ),
        )
        self.assertIs(trace.mode, Mode.UNOCCUPIED)
        self.assertIs(trace.actuator, ActuatorStep.OFF)
        self.assertTrue(any("unoccupied" in r for r in trace.rejected))

    def test_unoccupied_has_no_band(self):
        trace = evaluate_room(
            room(),
            RoomInputs(now=NOW, temperature_c=30.0, relative_humidity=60.0, presence=False),
        )
        self.assertIsNone(trace.band_low)
        self.assertIsNone(trace.band_high)

    def test_heading_home_overrides_unoccupied(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=60.0,
                presence=False,
                heading_home=True,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECONDITION)

    def test_precondition_uses_the_occupied_comfort_band(self):
        """There is one comfort definition per room, and it is the band."""
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=55.0,
                presence=False,
                heading_home=True,
                air_moving=True,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECONDITION)
        self.assertEqual(trace.band_low, 25.0)
        self.assertEqual(trace.band_high, 28.0)
        expected = dry_bulb_for_index(ComfortBand(25.0, 28.0).midpoint, 55.0)
        self.assertAlmostEqual(trace.target_dry_bulb_c, expected, places=3)

    def test_room_with_no_bands_never_actuates(self):
        trace = evaluate_room(
            room(bands={}),
            RoomInputs(
                now=NOW,
                temperature_c=34.0,
                relative_humidity=80.0,
                presence=True,
            ),
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertIsNone(trace.band_low)

    def test_heading_home_with_no_occupied_band_does_nothing(self):
        trace = evaluate_room(
            room(bands={Mode.SLEEP: ComfortBand(24.0, 26.0)}),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=60.0,
                presence=False,
                heading_home=True,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECONDITION)
        self.assertIsNone(trace.band_low)
        self.assertIsNone(trace.target_dry_bulb_c)
        self.assertIs(trace.actuator, ActuatorStep.NONE)


class TestClamping(unittest.TestCase):
    def test_unreachable_target_is_clamped_and_recorded(self):
        """A band the humidity makes unreachable must not command 45 C."""
        trace = evaluate_room(
            room(bands={Mode.OCCUPIED: ComfortBand(44.0, 46.0)}),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=20.0,
                presence=True,
            ),
        )
        self.assertEqual(trace.target_dry_bulb_c, 40.0)
        self.assertTrue(any("clamped" in r for r in trace.rejected))

    def test_normal_target_is_not_flagged_as_clamped(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
            ),
        )
        self.assertFalse(any("clamped" in r for r in trace.rejected))


class TestPrecoolIgnoresPresentOccupancy(unittest.TestCase):
    """Precool banks against a load that is coming, not one that is here.

    The free window is the middle of the day, when the room is usually empty.
    The load it is banking against arrives in the evening. Gating precool on
    someone being in the room now would stop it doing the one job it has.
    """

    def test_precool_runs_in_an_empty_room(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=60.0,
                presence=False,
                precool_opportunity=True,
                forecast_demand_ahead=True,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECOOL)
        self.assertIsNot(trace.actuator, ActuatorStep.NONE)

    def test_precool_still_needs_a_load_coming(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=60.0,
                presence=False,
                precool_opportunity=True,
                forecast_demand_ahead=False,
            ),
        )
        self.assertIs(trace.mode, Mode.UNOCCUPIED)


class TestSleepWithFailedSensor(unittest.TestCase):
    def test_unknown_presence_at_night_holds_sleep_not_day(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                sleep_schedule_active=True,
            ),
        )
        self.assertIs(trace.mode, Mode.SLEEP)
        self.assertEqual(trace.band_low, 24.0)


class TestSleepSchedule(unittest.TestCase):
    def test_sleep_requires_the_schedule_to_be_active(self):
        awake = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                sleep_schedule_active=False,
            ),
        )
        self.assertIs(awake.mode, Mode.OCCUPIED)

        asleep = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=55.0,
                presence=True,
                sleep_schedule_active=True,
            ),
        )
        self.assertIs(asleep.mode, Mode.SLEEP)
        self.assertEqual(asleep.band_low, 24.0)


_forms = importlib.import_module("hvac_core.forms")
_const = importlib.import_module("hvac_core.const")


class TestRoomForm(unittest.TestCase):
    def test_room_id_is_slugged_from_the_name(self):
        room = _forms.room_from_input(
            {"name": "Main Bedroom", "climate_entity_ids": ["climate.a"]}
        )
        self.assertEqual(room["room_id"], "main_bedroom")

    def test_an_unticked_room_carries_no_lockout_reason(self):
        """The reason is set by the lockout step, which requires the tick box."""
        room = _forms.room_from_input(
            {"name": "Office", "climate_entity_ids": ["climate.a"]}
        )
        self.assertIsNone(room["lockout_reason"])

    def test_optional_entities_default_to_absent(self):
        room = _forms.room_from_input(
            {"name": "Office", "climate_entity_ids": ["climate.a"]}
        )
        self.assertIsNone(room["sleep_schedule_entity_id"])
        self.assertEqual(room["opening_entity_ids"], [])


class TestBandForm(unittest.TestCase):
    def test_only_complete_pairs_are_kept(self):
        bands = _forms.bands_from_input(
            {"occupied_low": 24.0, "occupied_high": 27.0, "sleep_low": 21.0}
        )
        self.assertEqual(set(bands), {"occupied"})

    def test_inverted_band_is_invalid(self):
        self.assertFalse(
            _forms.bands_are_valid({"occupied": {"low": 27.0, "high": 24.0}})
        )

    def test_equal_bounds_are_invalid(self):
        self.assertFalse(
            _forms.bands_are_valid({"occupied": {"low": 24.0, "high": 24.0}})
        )

    def test_defaults_are_seeded_and_valid(self):
        """A fresh room arrives with sensible numbers, not six empty boxes."""
        suggestions = _forms.default_band_suggestions()
        self.assertEqual(suggestions["occupied_low"], 24.0)
        self.assertEqual(suggestions["occupied_high"], 27.0)
        self.assertEqual(suggestions["sleep_low"], 21.0)
        self.assertTrue(_forms.bands_are_valid(_const.DEFAULT_BANDS))

    def test_defaults_have_no_unoccupied_band(self):
        """An unoccupied room is off, so it has no band to seed."""
        self.assertNotIn("unoccupied", _const.DEFAULT_BANDS)

    def test_stored_bands_round_trip_through_the_form(self):
        stored = {"occupied": {"low": 24.0, "high": 27.0}}
        suggestions = _forms.bands_as_suggestions(stored)
        self.assertEqual(_forms.bands_from_input(suggestions), stored)


class TestLockoutReasons(unittest.TestCase):
    def test_built_in_reasons_are_offered(self):
        self.assertIn("Under renovation", _forms.known_lockout_reasons([]))

    def test_a_built_in_reason_is_not_stored_as_custom(self):
        self.assertEqual(
            _forms.extend_lockout_reasons([], {"lockout_reason": "Under renovation"}),
            [],
        )

    def test_a_typed_reason_becomes_available_globally(self):
        stored = _forms.extend_lockout_reasons(
            [], {"lockout_reason": "Waiting on sparky"}
        )
        self.assertEqual(stored, ["Waiting on sparky"])
        self.assertIn("Waiting on sparky", _forms.known_lockout_reasons(stored))

    def test_a_reason_is_not_stored_twice(self):
        self.assertEqual(
            _forms.extend_lockout_reasons(
                ["Waiting on sparky"], {"lockout_reason": "Waiting on sparky"}
            ),
            ["Waiting on sparky"],
        )

    def test_a_room_without_a_reason_leaves_the_list_alone(self):
        self.assertEqual(
            _forms.extend_lockout_reasons(["Waiting on sparky"], {"lockout_reason": None}),
            ["Waiting on sparky"],
        )

    def test_known_reasons_are_deduplicated(self):
        known = _forms.known_lockout_reasons(["Under renovation", "Waiting on sparky"])
        self.assertEqual(known.count("Under renovation"), 1)


class TestCoversEscalate(unittest.TestCase):
    """Covers must hand over once they have nowhere useful left to go."""

    def _hot_room(self, **overrides):
        inputs = {
            "now": NOW,
            "temperature_c": 33.0,
            "relative_humidity": 35.0,
            "presence": True,
            "has_covers": True,
            "direct_sun": True,
        }
        inputs.update(overrides)
        return evaluate_room(room(), RoomInputs(**inputs))

    def test_open_covers_are_used_first(self):
        trace = self._hot_room(cover_position=100.0)
        self.assertIs(trace.actuator, ActuatorStep.COVERS)

    def test_already_closed_covers_escalate_to_the_next_step(self):
        trace = self._hot_room(cover_position=0.0)
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("already closed" in r for r in trace.rejected))

    def test_nearly_closed_covers_count_as_closed(self):
        trace = self._hot_room(cover_position=3.0)
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)

    def test_unknown_position_skips_the_cover_step(self):
        """0.8.6. An unknown position is not a reason to command the covers.

        This asserted the opposite until 0.8.6, and the opposite is what
        turned the air conditioning off for the whole afternoon: choosing
        COVERS commands the climate entity off for that cycle, and a room
        whose covers never report a position chose COVERS on every cycle the
        sun was on the glass. There is no state that clears it — the room
        heats, so the demand persists.
        """
        trace = self._hot_room(cover_position=None)
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(
            any("reports its position" in r for r in trace.rejected), trace.rejected
        )

    def test_unknown_position_does_not_stall_the_ladder(self):
        """0.8.6. The room still gets cooled; only the cover step is skipped."""
        trace = self._hot_room(cover_position=None, relative_humidity=40.0)
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)

    def test_already_open_covers_escalate_when_heating(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=14.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                direct_sun=True,
                cover_position=100.0,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("already open" in r for r in trace.rejected))

    def test_partly_open_covers_are_still_worth_closing(self):
        trace = self._hot_room(cover_position=60.0)
        self.assertIs(trace.actuator, ActuatorStep.COVERS)


class TestSemiTransparentBlinds(unittest.TestCase):
    """Light level cannot gate covers: a sheer blind reads bright when shut."""

    def test_a_bright_room_with_no_sun_on_it_does_not_move_covers(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                cover_position=0.0,
                # Sheer blind fully closed, room still bright.
                direct_sun=False,
            ),
        )
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("no sun on this room" in r for r in trace.rejected))

    def test_a_dim_room_with_sun_on_it_still_moves_covers(self):
        """Blackout blind open at dawn: low lux, sun genuinely on the glass."""
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                cover_position=100.0,
                direct_sun=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COVERS)

    def test_unknown_sun_does_not_move_covers(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=50.0,
                presence=True,
                has_covers=True,
                cover_position=100.0,
                direct_sun=None,
            ),
        )
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("cannot tell whether the sun" in r for r in trace.rejected))


class TestUnitCapabilities(unittest.TestCase):
    """The decision must never choose a mode the unit does not have."""

    def _hot(self, **overrides):
        inputs = {
            "now": NOW,
            "temperature_c": 33.0,
            "relative_humidity": 80.0,
            "presence": True,
        }
        inputs.update(overrides)
        return evaluate_room(room(), RoomInputs(**inputs))

    def test_dry_is_skipped_on_a_unit_without_it(self):
        trace = self._hot(can_dry=False)
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("no dry mode" in r for r in trace.rejected))

    def test_fan_is_skipped_on_a_unit_without_it(self):
        # Marginally above band, where fan would otherwise be chosen.
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=28.0,
                relative_humidity=35.0,
                presence=True,
                can_fan_only=False,
            ),
        )
        self.assertIsNot(trace.actuator, ActuatorStep.FAN)
        self.assertTrue(any("no fan-only mode" in r for r in trace.rejected))

    def test_a_cooling_only_unit_does_nothing_when_asked_to_heat(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=14.0,
                relative_humidity=50.0,
                presence=True,
                can_heat=False,
            ),
        )
        self.assertEqual(trace.demand, "heat")
        self.assertIs(trace.actuator, ActuatorStep.OFF)
        self.assertTrue(any("cannot heat" in r for r in trace.rejected))

    def test_a_heating_only_unit_does_nothing_when_asked_to_cool(self):
        trace = self._hot(can_cool=False, can_dry=False)
        self.assertEqual(trace.demand, "cool")
        self.assertIs(trace.actuator, ActuatorStep.OFF)
        self.assertTrue(any("cannot cool" in r for r in trace.rejected))

    def test_an_unavailable_unit_actuates_nothing(self):
        trace = self._hot(
            can_cool=False, can_heat=False, can_dry=False, can_fan_only=False
        )
        self.assertIs(trace.actuator, ActuatorStep.OFF)


class TestCoverControlOverride(unittest.TestCase):
    """A per-room tick that keeps semi-transparent blinds still, always."""

    def _hot_room(self, **overrides):
        inputs = {
            "now": NOW,
            "temperature_c": 33.0,
            "relative_humidity": 35.0,
            "presence": True,
            "has_covers": True,
            "direct_sun": True,
            "cover_position": 100.0,
        }
        inputs.update(overrides)
        return evaluate_room(room(), RoomInputs(**inputs))

    def test_disabled_override_skips_covers_even_when_they_would_help(self):
        trace = self._hot_room(allow_cover_control=False)
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("control disabled" in r for r in trace.rejected))

    def test_default_is_enabled_and_unchanged(self):
        """No override configured behaves exactly as before this field existed."""
        trace = self._hot_room()
        self.assertIs(trace.actuator, ActuatorStep.COVERS)

    def test_override_disabled_on_a_room_with_no_covers_configured(self):
        """The two rejections are distinct reasons, not the same one twice."""
        trace = self._hot_room(has_covers=False, allow_cover_control=False)
        self.assertIsNot(trace.actuator, ActuatorStep.COVERS)
        self.assertTrue(any("none configured" in r for r in trace.rejected))
        self.assertFalse(any("control disabled" in r for r in trace.rejected))


class TestPowerAvailability(unittest.TestCase):
    """The inline power-aware compressor gate inside select_actuator itself.

    `power_available` arrives on RoomInputs already resolved by the
    coordinator — these tests exercise only what select_actuator does with
    it, not how the coordinator computes it.
    """

    def _hot(self, **overrides):
        inputs = {
            "now": NOW,
            "temperature_c": 33.0,
            "relative_humidity": 80.0,
            "presence": True,
            # Isolates the compressor step: without this, the latent load at
            # 80% RH routes to dry mode before the power gate is reached.
            "can_dry": False,
        }
        inputs.update(overrides)
        return evaluate_room(room(), RoomInputs(**inputs))

    def _cold(self, **overrides):
        inputs = {
            "now": NOW,
            "temperature_c": 14.0,
            "relative_humidity": 50.0,
            "presence": True,
        }
        inputs.update(overrides)
        return evaluate_room(room(), RoomInputs(**inputs))

    def test_power_unavailable_blocks_cooling(self):
        trace = self._hot(power_available=False)
        self.assertEqual(trace.demand, "cool")
        self.assertIs(trace.actuator, ActuatorStep.OFF)
        self.assertTrue(
            any("no grid import permitted" in r for r in trace.rejected)
        )

    def test_power_unavailable_blocks_heating(self):
        trace = self._cold(power_available=False)
        self.assertEqual(trace.demand, "heat")
        self.assertIs(trace.actuator, ActuatorStep.OFF)
        self.assertTrue(
            any("no grid import permitted" in r for r in trace.rejected)
        )

    def test_default_is_available_and_unchanged(self):
        """No power management configured behaves as before this field existed."""
        trace = self._hot()
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)

    def test_power_unavailable_does_not_block_covers_or_fan(self):
        """The gate sits at the compressor step, not earlier in the ladder."""
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=33.0,
                relative_humidity=35.0,
                presence=True,
                has_covers=True,
                direct_sun=True,
                cover_position=100.0,
                power_available=False,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.COVERS)


_hci = importlib.import_module("hvac_core.hci")
_thermal = importlib.import_module("hvac_core.thermal")
_forecast = importlib.import_module("hvac_core.forecast")


def _interval(**overrides):
    base = {
        "elapsed_hours": 0.25,
        "indoor_start_c": 24.0,
        "indoor_end_c": 24.0,
        "humidity_start": 60.0,
        "humidity_end": 60.0,
        "outdoor_c": 30.0,
        "direct_sun": False,
        "compressor": 0,
        "drying": False,
    }
    base.update(overrides)
    return _thermal.Observation(**base)


class TestThermalLearning(unittest.TestCase):
    def test_a_fresh_model_refuses_to_predict(self):
        """Day one: no prediction, so the caller falls back to hysteresis."""
        model = _thermal.ThermalModel()
        self.assertFalse(model.converged)
        self.assertIsNone(model.drift_rate(24.0, 30.0, direct_sun=False))
        self.assertIsNone(
            model.holds_through(
                24.0, 30.0, direct_sun=False, hours=1.0, lower_c=22.0, upper_c=26.0
            )
        )

    def test_intervals_that_are_too_short_or_long_are_ignored(self):
        model = _thermal.ThermalModel()
        model.observe(_interval(elapsed_hours=0.0001))
        model.observe(_interval(elapsed_hours=5.0))
        self.assertEqual(model.k_loss.samples, 0)

    def test_heat_loss_converges_from_passive_intervals(self):
        model = _thermal.ThermalModel()
        # Outdoor 6 C above indoor, room gains 0.6 C/h -> k_loss 0.1
        for _ in range(60):
            model.observe(
                _interval(
                    indoor_start_c=24.0,
                    indoor_end_c=24.15,
                    outdoor_c=30.0,
                )
            )
        self.assertTrue(model.k_loss.converged)
        self.assertAlmostEqual(model.k_loss.value, 0.1, places=1)

    def test_compressor_authority_is_learned_only_while_it_runs(self):
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(
                _interval(
                    indoor_start_c=26.0,
                    indoor_end_c=25.5,
                    compressor=-1,
                )
            )
        self.assertTrue(model.k_sensible.converged)
        self.assertAlmostEqual(model.k_sensible.value, 2.0, places=1)
        # A compressor interval must teach nothing about passive loss.
        self.assertEqual(model.k_loss.samples, 0)

    def test_a_room_moving_against_the_compressor_is_not_discarded(self):
        """0.8.9, finding 9: the negative tail is real information now.

        Before this build, an interval where the room moved against the
        compressor — a door open, a heat load — was dropped, which truncated
        the noise distribution and biased what remained high. It is real
        information about that operating point on that day, and dropping it
        is exactly what let a bin sit above zero when the true rate there was
        at or below it.
        """
        model = _thermal.ThermalModel()
        for _ in range(30):
            model.observe(
                _interval(indoor_start_c=26.0, indoor_end_c=26.5, compressor=-1)
            )
        self.assertTrue(model.k_sensible.converged)
        self.assertLess(model.k_sensible.value, 0.0)

    def test_latent_is_learned_separately_from_sensible(self):
        """The whole reason this model is not a heating-climate model.

        A rainy interval: dry bulb falls while humidity climbs. Sensible load
        drops as latent load rises, and one coefficient cannot describe both.
        """
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(
                _interval(
                    humidity_start=80.0,
                    humidity_end=78.0,
                    drying=True,
                )
            )
        self.assertTrue(model.k_latent.converged)
        self.assertAlmostEqual(model.k_latent.value, 8.0, places=0)
        # Drying taught nothing about the sensible term.
        self.assertEqual(model.k_sensible.samples, 0)

    def test_level_indoor_and_outdoor_teaches_nothing(self):
        """Nothing driving means the residual is noise over nearly zero."""
        model = _thermal.ThermalModel()
        for _ in range(30):
            model.observe(_interval(indoor_start_c=24.0, outdoor_c=24.0))
        self.assertEqual(model.k_loss.samples, 0)

    def test_the_filter_keeps_listening_after_converging(self):
        """Process noise: a house changes, and a locked filter is wrong."""
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(_interval(indoor_end_c=24.15, outdoor_c=30.0))
        settled = model.k_loss.value
        for _ in range(120):
            model.observe(_interval(indoor_end_c=24.3, outdoor_c=30.0))
        self.assertGreater(model.k_loss.value, settled)


class TestThermalPrediction(unittest.TestCase):
    def _converged(self):
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(_interval(indoor_end_c=24.15, outdoor_c=30.0))
            model.observe(
                _interval(indoor_start_c=26.0, indoor_end_c=25.5, compressor=-1)
            )
        return model

    def test_a_room_holds_when_the_drift_is_small(self):
        model = self._converged()
        self.assertTrue(
            model.holds_through(
                24.0, 25.0, direct_sun=False, hours=1.0, lower_c=22.0, upper_c=27.0
            )
        )

    def test_a_room_does_not_hold_against_a_big_difference(self):
        model = self._converged()
        self.assertFalse(
            model.holds_through(
                24.0, 40.0, direct_sun=False, hours=1.0, lower_c=22.0, upper_c=24.5
            )
        )

    def test_no_outdoor_reading_means_no_prediction(self):
        model = self._converged()
        self.assertIsNone(model.drift_rate(24.0, None, direct_sun=False))

    def test_pull_down_time_accounts_for_the_room_fighting_back(self):
        """On a hot day the unit fights the drift, so it takes longer."""
        model = self._converged()
        mild = model.hours_to_reach(28.0, 25.0, 26.0, direct_sun=False)
        hot = model.hours_to_reach(28.0, 25.0, 40.0, direct_sun=False)
        self.assertIsNotNone(mild)
        self.assertIsNotNone(hot)
        self.assertGreater(hot, mild)

    def test_a_target_already_reached_takes_no_time(self):
        model = self._converged()
        self.assertEqual(model.hours_to_reach(25.0, 25.0, 30.0, direct_sun=False), 0.0)

    def test_energy_rises_with_a_harder_job(self):
        model = self._converged()
        easy = model.energy_for(
            26.0, 25.0, 26.0, direct_sun=False, hours=4.0, rated_kw=1.2
        )
        hard = model.energy_for(
            32.0, 25.0, 38.0, direct_sun=False, hours=4.0, rated_kw=1.2
        )
        self.assertLess(easy, hard)

    def test_a_direction_the_unit_cannot_do_contributes_nothing(self):
        # 20 C indoor, 24 C target: this is a heating pull. A cooling-only
        # unit will never be commanded to attempt it. Outdoor held equal to
        # the target so the hold phase (a separate question) contributes
        # nothing here and the pull phase is isolated.
        model = self._converged()
        cooling_only = model.energy_for(
            20.0, 24.0, 24.0, direct_sun=False, hours=8.0, rated_kw=3.0,
            can_heat=False, can_cool=True,
        )
        self.assertEqual(cooling_only, 0.0)

    def test_a_capable_unit_is_unaffected_by_the_flags(self):
        model = self._converged()
        bidirectional = model.energy_for(
            32.0, 25.0, 38.0, direct_sun=False, hours=4.0, rated_kw=1.2,
            can_heat=True, can_cool=True,
        )
        default = model.energy_for(
            32.0, 25.0, 38.0, direct_sun=False, hours=4.0, rated_kw=1.2
        )
        self.assertEqual(bidirectional, default)

    def test_hold_phase_only_counts_a_correctable_drift_direction(self):
        # Target 24, outdoor 18: the room drifts down toward 18 while holding
        # at 24, so holding needs heating. A cooling-only unit contributes
        # nothing to the hold phase; a bidirectional one does.
        model = self._converged()
        cooling_only = model.energy_for(
            24.0, 24.0, 18.0, direct_sun=False, hours=8.0, rated_kw=3.0,
            can_heat=False, can_cool=True,
        )
        bidirectional = model.energy_for(
            24.0, 24.0, 18.0, direct_sun=False, hours=8.0, rated_kw=3.0,
            can_heat=True, can_cool=True,
        )
        self.assertEqual(cooling_only, 0.0)
        self.assertGreater(bidirectional, 0.0)


class TestApproachBins(unittest.TestCase):
    """0.8.9, finding 9: `k_sensible` binned by the gap to the setpoint."""

    def _observe(self, model, setpoint_c, indoor_c, *, count=25, rate_c=1.0):
        """`count` intervals holding `setpoint_c` fixed, cooling toward it."""
        for _ in range(count):
            model.observe(
                _interval(
                    indoor_start_c=indoor_c,
                    indoor_end_c=indoor_c - rate_c * 0.25,
                    compressor=-1,
                    commanded_setpoint_c=setpoint_c,
                )
            )

    def test_an_interval_is_attributed_to_the_mean_approach_bin(self):
        # Setpoint 22, room 24 -> 23.75: mean approach ~1.9, "working" bin.
        model = _thermal.ThermalModel()
        self._observe(model, setpoint_c=22.0, indoor_c=24.0)
        self.assertTrue(model.k_sensible_bins[2].converged)
        for other in (0, 1, 3):
            self.assertEqual(model.k_sensible_bins[other].samples, 0)

    def test_each_interval_bins_on_its_own_commanded_setpoint(self):
        """A bin is chosen from one interval's own approach, never blended.

        The anchor-reset behaviour that keeps a commanded-setpoint change
        from spanning one interval belongs to the coordinator's `_learn` —
        exercised in test_init.py, since it needs the regulator state
        `_learn` reads. What `thermal.py` owns, and what this asserts, is
        that two intervals at different setpoints are attributed
        independently rather than averaged into one bin.
        """
        model = _thermal.ThermalModel()
        for _ in range(25):
            model.observe(
                _interval(
                    indoor_start_c=24.0, indoor_end_c=23.75, compressor=-1,
                    commanded_setpoint_c=23.5,  # mean approach ~0.4 -> at_setpoint
                )
            )
        for _ in range(25):
            model.observe(
                _interval(
                    indoor_start_c=30.0, indoor_end_c=29.0, compressor=-1,
                    commanded_setpoint_c=20.0,  # mean approach ~9.5 -> pulldown
                )
            )
        self.assertTrue(model.k_sensible_bins[0].converged)
        self.assertTrue(model.k_sensible_bins[3].converged)
        self.assertEqual(model.k_sensible_bins[1].samples, 0)
        self.assertEqual(model.k_sensible_bins[2].samples, 0)

    def test_an_unconverged_bin_falls_back_to_pooled(self):
        model = _thermal.ThermalModel()
        # Pool converges from the "close" bin.
        self._observe(model, setpoint_c=22.0, indoor_c=22.8, count=25)
        self.assertTrue(model.k_sensible.converged)
        # The pulldown bin (index 3) has never been observed.
        self.assertFalse(model.k_sensible_bins[3].converged)
        self.assertEqual(model._sensible_rate(3), model.k_sensible.value)

    def test_a_converged_bin_is_preferred_to_pooled(self):
        model = _thermal.ThermalModel()
        # Pool from a mix of two operating points.
        self._observe(model, setpoint_c=22.0, indoor_c=22.8, count=25, rate_c=1.0)
        self._observe(model, setpoint_c=22.0, indoor_c=30.0, count=25, rate_c=3.0)
        pooled = model.k_sensible.value
        pulldown_bin = model.k_sensible_bins[3].value
        self.assertNotAlmostEqual(pooled, pulldown_bin, places=2)
        self.assertEqual(model._sensible_rate(3), pulldown_bin)

    def test_a_bin_at_or_below_zero_makes_hours_to_reach_none(self):
        model = _thermal.ThermalModel()
        # The room loses to the load at the "working" approach every time.
        for _ in range(25):
            model.observe(
                _interval(
                    indoor_start_c=24.0, indoor_end_c=24.5, compressor=-1,
                    commanded_setpoint_c=22.0,
                )
            )
        self.assertTrue(model.k_sensible.converged)
        self.assertLessEqual(model.k_sensible_bins[2].value, 0.0)
        self.assertIsNone(
            model.hours_to_reach(24.0, 22.0, None, direct_sun=False)
        )

    def test_hours_to_reach_uses_each_bins_own_rate(self):
        model = _thermal.ThermalModel()
        # Converge three bins at different rates: pulldown fast, working
        # slower, close slower again — a real inverter's shape.
        self._observe(model, setpoint_c=15.0, indoor_c=30.0, rate_c=4.0)  # pulldown
        self._observe(model, setpoint_c=15.0, indoor_c=17.0, rate_c=2.0)  # working
        self._observe(model, setpoint_c=15.0, indoor_c=15.8, rate_c=1.0)  # close
        for index in (1, 2, 3):
            self.assertTrue(model.k_sensible_bins[index].converged)

        piecewise = model.hours_to_reach(30.0, 15.0, None, direct_sun=False)
        # A single pooled rate across the whole 15 C pull is a strictly worse
        # estimate than crossing each bin at its own rate — this asserts the
        # piecewise walk is actually being used rather than collapsing to a
        # single-rate answer, by comparing against a hand-solved uniform-rate
        # estimate at the pulldown bin's own rate.
        pulldown_only = 15.0 / model.k_sensible_bins[3].value
        self.assertIsNotNone(piecewise)
        self.assertGreater(piecewise, pulldown_only)

    def test_an_08_8_store_loads_with_bins_empty_and_predictions_unchanged(self):
        """A pre-0.8.9 record has no `k_sensible_bins` or `k_rh_cooling`."""
        legacy = {
            "k_loss": {"value": 0.15, "variance": 0.01, "samples": 60},
            "k_sensible": {"value": 2.4, "variance": 0.01, "samples": 60},
            "k_latent": {"value": 8.0, "variance": 0.01, "samples": 60},
        }
        model = _thermal.ThermalModel.from_dict(legacy)
        self.assertAlmostEqual(model.k_sensible.value, 2.4)
        self.assertFalse(model.k_rh_cooling.converged)
        for coefficient in model.k_sensible_bins:
            self.assertEqual(coefficient.samples, 0)
        self.assertEqual(model._sensible_rate(3), 2.4)
        self.assertAlmostEqual(
            model.hours_to_reach(30.0, 20.0, None, direct_sun=False),
            10.0 / 2.4,
        )


class TestRhCoolingLearning(unittest.TestCase):
    """0.8.9, finding 17: humidity response while cooling, measured."""

    def test_learns_from_a_cooling_interval_not_a_drying_one(self):
        model = _thermal.ThermalModel()
        for _ in range(25):
            model.observe(
                _interval(
                    humidity_start=60.0, humidity_end=62.0, compressor=-1,
                )
            )
        self.assertTrue(model.k_rh_cooling.converged)
        drying_model = _thermal.ThermalModel()
        for _ in range(25):
            drying_model.observe(
                _interval(
                    humidity_start=80.0, humidity_end=70.0, drying=True,
                )
            )
        self.assertFalse(drying_model.k_rh_cooling.converged)

    def test_it_can_be_negative_and_survives_the_filter(self):
        model = _thermal.ThermalModel()
        for _ in range(25):
            model.observe(
                _interval(
                    humidity_start=70.0, humidity_end=64.0, compressor=-1,
                )
            )
        self.assertTrue(model.k_rh_cooling.converged)
        self.assertLess(model.k_rh_cooling.value, 0.0)


class TestDrawModel(unittest.TestCase):
    """0.8.9, finding 14: learned per-compressor draw."""

    def test_one_clean_observation_moves_the_estimate(self):
        model = _thermal.DrawModel()
        before = model.bins[3].value
        model.observe(3, 2.4, quality=1.0)
        self.assertNotAlmostEqual(model.bins[3].value, before, places=3)
        self.assertGreater(model.bins[3].value, before)

    def test_a_low_quality_observation_moves_it_much_less(self):
        clean = _thermal.DrawModel()
        clean.observe(3, 3.0, quality=1.0)
        noisy = _thermal.DrawModel()
        noisy.observe(3, 3.0, quality=0.05)
        start = _thermal.DrawModel().bins[3].value
        self.assertGreater(
            abs(clean.bins[3].value - start), abs(noisy.bins[3].value - start)
        )

    def test_a_large_unrelated_load_is_absorbed_not_rejected(self):
        model = _thermal.DrawModel()
        # A kettle-sized outlier, entered at low quality.
        model.observe(3, 4.5, quality=_thermal.MIN_DRAW_QUALITY)
        self.assertEqual(model.bins[3].samples, 1)

    def test_a_sustained_change_moves_more_than_one_outlier_of_the_same_size(self):
        outlier = _thermal.DrawModel()
        outlier.observe(3, 5.0, quality=0.3)
        sustained = _thermal.DrawModel()
        for _ in range(15):
            sustained.observe(3, 5.0, quality=0.3)
        start = _thermal.DrawModel().bins[3].value
        self.assertGreater(
            abs(sustained.bins[3].value - start), abs(outlier.bins[3].value - start)
        )

    def test_few_readings_should_be_scored_a_wider_variance_than_many(self):
        # This is a property of how the coordinator scores quality, not of
        # DrawModel itself — DrawModel only ever sees the resulting quality
        # figure. Asserted here at the level DrawModel actually owns: a
        # lower quality score produces a smaller posterior gain.
        few = _thermal.Coefficient(3.0)
        many = _thermal.Coefficient(3.0)
        few.update(4.0, 1.0, variance_scale=1.0 / 0.2)
        many.update(4.0, 1.0, variance_scale=1.0 / 0.9)
        self.assertLess(abs(few.value - 3.0), abs(many.value - 3.0))

    def test_an_unconverged_bin_falls_back_to_pooled_group_draw(self):
        model = _thermal.DrawModel()
        for _ in range(25):
            model.pooled.update(2.8, 1.0)
        self.assertTrue(model.pooled.converged)
        self.assertFalse(model.bins[3].converged)
        self.assertAlmostEqual(model.draw_kw(3, 1.2), 2.8, places=1)

    def test_an_unpopulated_group_falls_back_to_the_callers_constant(self):
        model = _thermal.DrawModel()
        self.assertEqual(model.draw_kw(3, 1.2), 1.2)

    def test_two_bins_are_independent(self):
        model = _thermal.DrawModel()
        for _ in range(25):
            model.observe(0, 0.3, quality=1.0)
            model.observe(3, 3.2, quality=1.0)
        self.assertTrue(model.bins[0].converged)
        self.assertTrue(model.bins[3].converged)
        self.assertLess(model.bins[0].value, model.bins[3].value)

    def test_round_trips_through_persistence(self):
        model = _thermal.DrawModel()
        for _ in range(25):
            model.observe(3, 2.9, quality=1.0)
        restored = _thermal.DrawModel.from_dict(model.as_dict())
        self.assertAlmostEqual(restored.bins[3].value, model.bins[3].value)
        self.assertTrue(restored.bins[3].converged)

    def test_diagnostics_name_every_bin_and_the_pooled_figure(self):
        model = _thermal.DrawModel()
        diagnostics = model.diagnostics()
        self.assertEqual(set(diagnostics), {"bins", "pooled"})
        self.assertEqual(
            set(diagnostics["bins"]),
            {"at_setpoint", "close", "working", "pulldown"},
        )
        for entry in (*diagnostics["bins"].values(), diagnostics["pooled"]):
            self.assertEqual(
                set(entry), {"value", "variance", "samples", "converged"}
            )


class TestThermalPersistence(unittest.TestCase):
    def test_a_model_survives_a_round_trip(self):
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(_interval(indoor_end_c=24.15, outdoor_c=30.0))
        restored = _thermal.ThermalModel.from_dict(model.as_dict())
        self.assertAlmostEqual(restored.k_loss.value, model.k_loss.value)
        self.assertEqual(restored.k_loss.samples, model.k_loss.samples)
        self.assertTrue(restored.k_loss.converged)

    def test_unreadable_stored_state_starts_fresh(self):
        """Losing this costs convergence time, not correctness."""
        for junk in (None, "corrupt", {"k_loss": "not a dict"}, {}):
            model = _thermal.ThermalModel.from_dict(junk)
            self.assertFalse(model.k_loss.converged)
            self.assertEqual(model.k_loss.samples, 0)

    def test_a_non_finite_stored_coefficient_starts_fresh(self):
        """`float("nan")` and `float("inf")` both parse without raising, so a
        corrupted store file would otherwise load a broken number silently
        and poison every prediction built on it."""
        for broken in ("nan", "inf", "-inf"):
            restored = _thermal.Coefficient.from_dict(
                {"value": broken, "variance": 0.01, "samples": 50}, default=0.15
            )
            self.assertEqual(restored.value, 0.15)
            self.assertEqual(restored.samples, 0)

            restored = _thermal.Coefficient.from_dict(
                {"value": 0.2, "variance": broken, "samples": 50}, default=0.15
            )
            self.assertEqual(restored.value, 0.15)
            self.assertEqual(restored.samples, 0)

    def test_diagnostics_name_every_coefficient(self):
        diagnostics = _thermal.ThermalModel().diagnostics()
        self.assertEqual(
            set(diagnostics),
            {
                "k_loss",
                "k_solar",
                "k_sensible",
                "k_latent",
                "k_rh_cooling",
                "k_sensible_bins",
            },
        )
        self.assertIn("converged", diagnostics["k_loss"])
        self.assertEqual(
            set(diagnostics["k_sensible_bins"]),
            {"at_setpoint", "close", "working", "pulldown"},
        )
        self.assertIn("converged", diagnostics["k_sensible_bins"]["pulldown"])


class TestDemandForecast(unittest.TestCase):
    """The published contract. No vendor concepts may appear in it."""

    def _room_input(self, **overrides):
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(_interval(indoor_end_c=24.15, outdoor_c=30.0))
            model.observe(
                _interval(indoor_start_c=26.0, indoor_end_c=25.5, compressor=-1)
            )
        base = {
            "room_id": "office",
            "model": model,
            "indoor_c": 28.0,
            "target_c": 25.0,
            "outdoor_c": 33.0,
            "direct_sun": False,
            "will_run": True,
        }
        base.update(overrides)
        return _forecast.RoomForecastInput(**base)

    def test_a_room_that_will_not_run_contributes_nothing(self):
        projection = _forecast.project_room(
            self._room_input(will_run=False), horizon_hours=8
        )
        self.assertEqual(projection.kwh, 0.0)
        self.assertIn("will not run", projection.reason)

    def test_an_unconverged_room_is_flagged_not_hidden(self):
        fresh = self._room_input()
        fresh = _forecast.RoomForecastInput(
            room_id=fresh.room_id,
            model=_thermal.ThermalModel(),
            indoor_c=28.0,
            target_c=25.0,
            outdoor_c=33.0,
            direct_sun=False,
            will_run=True,
        )
        projection = _forecast.project_room(fresh, horizon_hours=8)
        self.assertFalse(projection.modelled)
        self.assertGreater(projection.kwh, 0.0)
        self.assertIn("not converged", projection.reason)

    def test_no_reading_projects_nothing_and_says_so(self):
        projection = _forecast.project_room(
            self._room_input(indoor_c=None), horizon_hours=8
        )
        self.assertEqual(projection.kwh, 0.0)
        self.assertFalse(projection.modelled)

    def test_the_forecast_carries_no_vendor_concepts(self):
        forecast = _forecast.build_forecast(
            NOW,
            [self._room_input()],
            [
                (time(0, 0), time(16, 0), "standard", frozenset()),
                (time(16, 0), time(0, 0), "peak", frozenset({"no_grid_import"})),
            ],
            horizon_hours=8,
        )
        text = repr(forecast.as_attributes()).lower()
        for vendor in ("powerwall", "tesla", "sungrow", "fronius", "byd", "reserve"):
            self.assertNotIn(vendor, text)

    def test_windows_are_broken_out_and_carry_their_constraints(self):
        forecast = _forecast.build_forecast(
            NOW,  # 14:30
            [self._room_input()],
            [
                (time(0, 0), time(16, 0), "standard", frozenset()),
                (time(16, 0), time(0, 0), "peak", frozenset({"no_grid_import"})),
            ],
            horizon_hours=8,
        )
        rates = {window.rate for window in forecast.windows}
        self.assertEqual(rates, {"standard", "peak"})
        peak = next(w for w in forecast.windows if w.rate == "peak")
        self.assertIn("no_grid_import", peak.constraints)
        self.assertAlmostEqual(peak.hours, 6.5, places=1)

    def test_window_hours_sum_to_the_horizon(self):
        forecast = _forecast.build_forecast(
            NOW,
            [self._room_input()],
            [
                (time(0, 0), time(16, 0), "standard", frozenset()),
                (time(16, 0), time(0, 0), "peak", frozenset()),
            ],
            horizon_hours=8,
        )
        self.assertAlmostEqual(
            sum(window.hours for window in forecast.windows), 8.0, places=1
        )

    def test_window_energy_sums_to_the_total(self):
        forecast = _forecast.build_forecast(
            NOW,
            [self._room_input(), self._room_input(room_id="living")],
            [
                (time(0, 0), time(16, 0), "standard", frozenset()),
                (time(16, 0), time(0, 0), "peak", frozenset()),
            ],
            horizon_hours=8,
        )
        self.assertAlmostEqual(
            sum(window.kwh for window in forecast.windows),
            forecast.total_kwh,
            places=1,
        )

    def test_a_wrapping_window_is_counted_correctly(self):
        forecast = _forecast.build_forecast(
            NOW,  # 14:30, horizon to 22:30
            [self._room_input()],
            [(time(21, 0), time(9, 0), "overnight", frozenset())],
            horizon_hours=8,
        )
        self.assertAlmostEqual(forecast.windows[0].hours, 1.5, places=1)

    def test_no_tariff_configured_still_produces_a_total(self):
        forecast = _forecast.build_forecast(NOW, [self._room_input()], [], 8)
        self.assertGreater(forecast.total_kwh, 0.0)
        self.assertEqual(forecast.windows, [])

    def test_fully_modelled_is_false_when_any_room_is_guessing(self):
        fresh = _forecast.RoomForecastInput(
            room_id="guest",
            model=_thermal.ThermalModel(),
            indoor_c=28.0,
            target_c=25.0,
            outdoor_c=33.0,
            direct_sun=False,
            will_run=True,
        )
        forecast = _forecast.build_forecast(NOW, [self._room_input(), fresh], [], 8)
        self.assertFalse(forecast.fully_modelled)


_sun = importlib.import_module("hvac_core.sun")


class TestSunGeometry(unittest.TestCase):
    """Sun on the glass, from position and window direction. No sensor needed."""

    def test_a_north_window_gets_midday_sun_in_the_southern_hemisphere(self):
        # Brisbane midday: sun due north, high.
        self.assertTrue(_sun.sun_on_window(0.0, 60.0, _sun.WINDOW_DIRECTIONS["north"]))

    def test_a_south_window_does_not_get_that_sun(self):
        self.assertFalse(_sun.sun_on_window(0.0, 60.0, _sun.WINDOW_DIRECTIONS["south"]))

    def test_a_west_window_gets_the_afternoon(self):
        self.assertTrue(_sun.sun_on_window(270.0, 25.0, _sun.WINDOW_DIRECTIONS["west"]))

    def test_a_west_window_does_not_get_the_morning(self):
        self.assertFalse(_sun.sun_on_window(90.0, 25.0, _sun.WINDOW_DIRECTIONS["west"]))

    def test_the_sun_below_the_horizon_is_on_no_window(self):
        for direction in _sun.WINDOW_DIRECTIONS.values():
            self.assertFalse(_sun.sun_on_window(180.0, -5.0, direction))

    def test_a_sun_barely_up_does_not_count(self):
        """One degree of elevation is not worth moving a blind for."""
        self.assertFalse(_sun.sun_on_window(90.0, 1.0, _sun.WINDOW_DIRECTIONS["east"]))

    def test_the_edge_of_the_acceptance_angle(self):
        east = _sun.WINDOW_DIRECTIONS["east"]
        self.assertTrue(_sun.sun_on_window(0.0, 30.0, east))
        self.assertFalse(_sun.sun_on_window(359.0, 30.0, east))

    def test_wrapping_past_north_is_handled(self):
        north = _sun.WINDOW_DIRECTIONS["north"]
        self.assertTrue(_sun.sun_on_window(350.0, 30.0, north))
        self.assertTrue(_sun.sun_on_window(10.0, 30.0, north))

    def test_no_direction_configured_means_no_answer(self):
        """Not 'no sun'. The evaluator must not move covers on a guess."""
        self.assertIsNone(_sun.sun_on_window(180.0, 45.0, None))

    def test_no_sun_position_means_no_answer(self):
        self.assertIsNone(_sun.sun_on_window(None, None, 0.0))

    def test_every_offered_direction_resolves(self):
        for name in _sun.WINDOW_DIRECTIONS:
            self.assertIsNotNone(_sun.azimuth_for_direction(name))
        self.assertIsNone(_sun.azimuth_for_direction("upwards"))


class TestLockoutIsOneField(unittest.TestCase):
    """One dropdown answers both questions: no toggle, no second screen."""

    def test_not_locked_out_stores_no_reason(self):
        room = _forms.room_from_input(
            {
                "name": "Office",
                "climate_entity_ids": ["climate.o"],
                "lockout_reason": _const.NOT_LOCKED_OUT,
            }
        )
        self.assertIsNone(room["lockout_reason"])

    def test_choosing_a_reason_locks_the_room_out(self):
        room = _forms.room_from_input(
            {
                "name": "Study",
                "climate_entity_ids": ["climate.s"],
                "lockout_reason": "Under renovation",
            }
        )
        self.assertEqual(room["lockout_reason"], "Under renovation")

    def test_the_not_locked_out_option_comes_first(self):
        options = _forms.known_lockout_reasons([])
        self.assertEqual(options[0], _const.NOT_LOCKED_OUT)

    def test_not_locked_out_is_never_stored_as_a_custom_reason(self):
        self.assertEqual(
            _forms.extend_lockout_reasons(
                [], {"lockout_reason": _const.NOT_LOCKED_OUT}
            ),
            [],
        )

    def test_blank_is_treated_as_not_locked_out(self):
        room = _forms.room_from_input(
            {"name": "Office", "climate_entity_ids": ["climate.o"], "lockout_reason": "  "}
        )
        self.assertIsNone(room["lockout_reason"])


_grace = importlib.import_module("hvac_core.grace")


class TestOccupancyGrace(unittest.TestCase):
    """Raw presence is the wrong signal for a compressor."""

    def setUp(self):
        self.state = _grace.GraceState()
        self.settings = _grace.GraceSettings()
        self.t = NOW

    def _step(self, present, minutes=0):
        self.t = self.t + timedelta(minutes=minutes)
        return _grace.evaluate_grace(self.state, present, self.t, self.settings)

    def test_a_grab_and_go_visit_never_starts_the_room(self):
        """Someone drops a laptop off and leaves. No compressor start."""
        self.assertFalse(self._step(True).occupied)
        self.assertFalse(self._step(True, 1).occupied)
        self.assertFalse(self._step(False, 0.5).occupied)
        self.assertFalse(self.state.occupied)

    def test_sustained_presence_starts_the_room(self):
        self.assertFalse(self._step(True).occupied)
        self.assertTrue(self._step(True, 2).occupied)

    def test_answering_the_front_door_does_not_stop_the_room(self):
        """The delivery case. Five minutes away must not shut the room down."""
        self._step(True)
        self.assertTrue(self._step(True, 2).occupied)
        self._step(False)
        self.assertTrue(self._step(False, 2).occupied)
        result = self._step(False, 3)
        self.assertTrue(result.occupied)
        self.assertIn("holding in case they return", result.reason)

    def test_returning_resets_the_absence(self):
        self._step(True)
        self._step(True, 2)
        self._step(False)
        self._step(False, 8)
        self.assertTrue(self._step(True, 1).occupied)
        # Away again: the clock restarts, so 8 minutes is not cumulative.
        self._step(False)
        self.assertTrue(self._step(False, 8).occupied)

    def test_a_long_absence_finally_stops_the_room(self):
        self._step(True)
        self._step(True, 2)
        # The vacancy clock starts when they leave, so departure is registered
        # first and the elapsed time is measured from there.
        self._step(False)
        self.assertFalse(self._step(False, 11).occupied)

    def test_a_returning_occupant_starts_again_without_waiting_twice(self):
        self._step(True)
        self._step(True, 2)
        self._step(False)
        self._step(False, 11)
        self.assertFalse(self._step(True, 0.5).occupied)
        self.assertTrue(self._step(True, 2).occupied)

    def test_unknown_presence_holds_whatever_the_room_was(self):
        self._step(True)
        self._step(True, 2)
        result = self._step(None, 30)
        self.assertTrue(result.occupied)
        self.assertIn("presence unknown", result.reason)

    def test_unknown_presence_does_not_start_an_empty_room(self):
        self.assertFalse(self._step(None, 30).occupied)


class TestGraceAnnouncements(unittest.TestCase):
    def setUp(self):
        self.state = _grace.GraceState()
        self.settings = _grace.GraceSettings(announce=True)
        self.t = NOW

    def _step(self, present, minutes=0):
        self.t = self.t + timedelta(minutes=minutes)
        return _grace.evaluate_grace(self.state, present, self.t, self.settings)

    def test_two_warnings_before_shutting_down(self):
        self._step(True)
        self._step(True, 2)

        self._step(False)
        first = self._step(False, 11)
        self.assertIs(first.announcement, _grace.Announcement.FIRST_WARNING)
        self.assertTrue(first.occupied, "must not shut off on the first warning")

        quiet = self._step(False, 1)
        self.assertIs(quiet.announcement, _grace.Announcement.NONE)
        self.assertTrue(quiet.occupied)

        final = self._step(False, 3)
        self.assertIs(final.announcement, _grace.Announcement.FINAL_WARNING)
        self.assertFalse(final.occupied)

    def test_coming_back_after_the_warning_cancels_the_shutdown(self):
        self._step(True)
        self._step(True, 2)
        self._step(False)
        self.assertIs(
            self._step(False, 11).announcement, _grace.Announcement.FIRST_WARNING
        )
        self.assertTrue(self._step(True, 1).occupied)
        self.assertIsNone(self.state.warned_at)
        # And a fresh absence warns again rather than shutting off silently.
        self._step(False)
        self.assertIs(
            self._step(False, 11).announcement, _grace.Announcement.FIRST_WARNING
        )

    def test_announcements_off_shuts_down_without_speaking(self):
        self.settings = _grace.GraceSettings(announce=False)
        self._step(True)
        self._step(True, 2)
        self._step(False)
        result = self._step(False, 11)
        self.assertFalse(result.occupied)
        self.assertIs(result.announcement, _grace.Announcement.NONE)

    def test_defaults_are_sensible_out_of_the_box(self):
        defaults = _grace.GraceSettings()
        self.assertEqual(defaults.occupied_after, timedelta(minutes=2))
        self.assertEqual(defaults.vacant_after, timedelta(minutes=10))
        self.assertEqual(defaults.warning_grace, timedelta(minutes=3))
        self.assertFalse(defaults.announce, "a house should not start talking")

    def test_settings_come_from_minutes(self):
        settings = _grace.GraceSettings.from_minutes(5, 20, 2, True)
        self.assertEqual(settings.occupied_after, timedelta(minutes=5))
        self.assertEqual(settings.vacant_after, timedelta(minutes=20))
        self.assertTrue(settings.announce)

    def test_missing_values_fall_back_to_defaults(self):
        settings = _grace.GraceSettings.from_minutes()
        self.assertEqual(settings.vacant_after, timedelta(minutes=10))


class TestRadiantComfort(unittest.TestCase):
    """Air temperature and humidity cannot see sun, still air or equipment."""

    def test_sun_through_glass_raises_the_index(self):
        shaded = comfort_index(24.0, 60.0)
        sunlit = comfort_index(24.0, 60.0, radiant=1.0)
        self.assertGreater(sunlit, shaded)

    def test_a_half_closed_blind_passes_about_half(self):
        """A 50% blind is not 'no sun'. This is the case that was wrong."""
        fraction = _hci.radiant_load(
            direct_sun=True, cover_position=50.0, has_covers=True
        )
        self.assertGreater(fraction, 0.4)
        self.assertLess(fraction, 0.7)

    def test_a_closed_blind_still_passes_some(self):
        """It absorbs the energy and re-radiates it inward."""
        fraction = _hci.radiant_load(
            direct_sun=True, cover_position=0.0, has_covers=True
        )
        self.assertGreater(fraction, 0.0)
        self.assertLess(fraction, 0.3)

    def test_no_sun_means_no_radiant_load_whatever_the_blind(self):
        for position in (0.0, 50.0, 100.0):
            self.assertEqual(
                _hci.radiant_load(
                    direct_sun=False, cover_position=position, has_covers=True
                ),
                0.0,
            )

    def test_a_room_with_no_covers_takes_all_of_it(self):
        self.assertEqual(
            _hci.radiant_load(direct_sun=True, cover_position=None, has_covers=False),
            1.0,
        )

    def test_still_air_and_heat_load_each_raise_the_index(self):
        base = comfort_index(24.0, 60.0)
        self.assertGreater(comfort_index(24.0, 60.0, still_air=True), base)
        self.assertGreater(comfort_index(24.0, 60.0, heat_load=True), base)

    def test_the_office_case(self):
        """Sitting in a sunlit room behind a half blind, no airflow, PC on.

        The air-only index calls this comfortable. The corrected index does
        not, which is the whole reason the corrections exist.
        """
        radiant = _hci.radiant_load(
            direct_sun=True, cover_position=50.0, has_covers=True
        )
        air_only = comfort_index(24.0, 60.0)
        felt = comfort_index(
            24.0, 60.0, radiant=radiant, still_air=True, heat_load=True
        )
        self.assertLess(air_only, 27.5, "air-only index reads as comfortable")
        self.assertGreater(felt, 27.5, "corrected index reads as warm")

    def test_a_sunlit_room_is_asked_for_colder_air(self):
        shaded = dry_bulb_for_index(25.5, 60.0)
        sunlit = dry_bulb_for_index(25.5, 60.0, radiant=1.0, still_air=True)
        self.assertLess(sunlit, shaded)

    def test_the_inverse_round_trips_with_corrections(self):
        for radiant in (0.0, 0.5, 1.0):
            for still in (True, False):
                target = dry_bulb_for_index(
                    26.0, 60.0, radiant=radiant, still_air=still
                )
                self.assertAlmostEqual(
                    comfort_index(target, 60.0, radiant=radiant, still_air=still),
                    26.0,
                    places=2,
                )

    def test_the_trace_shows_what_the_corrections_added(self):
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=24.0,
                relative_humidity=60.0,
                presence=True,
                has_covers=True,
                cover_position=50.0,
                direct_sun=True,
                heat_load=True,
            ),
        )
        self.assertIsNotNone(trace.hci_base)
        self.assertGreater(trace.hci, trace.hci_base)
        self.assertTrue(any("index raised" in r for r in trace.reasons))
        attributes = trace.as_attributes()
        self.assertIn("hci_air_only", attributes)
        self.assertIn("radiant_fraction", attributes)


class TestOverhangShading(unittest.TestCase):
    """An eave shades a window whenever the sun is high. Ignoring it is wrong."""

    def test_a_typical_eave_shades_from_about_two_thirds_up(self):
        cutoff = _sun.shading_elevation(0.9, 2.1)
        self.assertGreater(cutoff, 60.0)
        self.assertLess(cutoff, 72.0)

    def test_a_deeper_eave_shades_from_lower(self):
        shallow = _sun.shading_elevation(0.5, 2.1)
        deep = _sun.shading_elevation(1.5, 2.1)
        self.assertGreater(shallow, deep)

    def test_no_overhang_described_means_no_shading(self):
        self.assertIsNone(_sun.shading_elevation(None, 2.1))
        self.assertIsNone(_sun.shading_elevation(0, 2.1))

    def test_a_high_summer_sun_is_shaded_by_the_eave(self):
        north = _sun.WINDOW_DIRECTIONS["north"]
        self.assertFalse(
            _sun.sun_on_window(
                0.0, 78.0, north, overhang_projection_m=0.9, overhang_height_m=2.1
            )
        )

    def test_a_low_winter_sun_reaches_under_the_eave(self):
        north = _sun.WINDOW_DIRECTIONS["north"]
        self.assertTrue(
            _sun.sun_on_window(
                0.0, 35.0, north, overhang_projection_m=0.9, overhang_height_m=2.1
            )
        )

    def test_without_an_overhang_the_high_sun_still_counts(self):
        north = _sun.WINDOW_DIRECTIONS["north"]
        self.assertTrue(_sun.sun_on_window(0.0, 78.0, north))

    def test_oblique_sun_slips_under_an_eave_that_would_shade_it_head_on(self):
        """The eave projects less usefully when the sun is off to one side."""
        west = _sun.WINDOW_DIRECTIONS["west"]
        head_on = _sun.sun_on_window(
            270.0, 70.0, west, overhang_projection_m=0.9, overhang_height_m=2.1
        )
        oblique = _sun.sun_on_window(
            200.0, 70.0, west, overhang_projection_m=0.9, overhang_height_m=2.1
        )
        self.assertFalse(head_on)
        self.assertTrue(oblique)


class TestConfigurationIsReadable(unittest.TestCase):
    """A configuration you must edit to inspect is one nobody checks."""

    def _room(self, **overrides):
        base = {
            "room_id": "office",
            "name": "Office",
            "climate_entity_ids": ("climate.office",),
            "temperature_entity_id": "sensor.office_temp",
            "bands": {"occupied": {"low": 24.0, "high": 27.0}},
            "occupied_after_minutes": 2,
            "vacant_after_minutes": 10,
        }
        base.update(overrides)
        return base

    def test_a_room_summary_names_what_is_set_and_what_is_not(self):
        described = _forms.describe_room(self._room())
        self.assertIn("Office", described)
        self.assertIn("climate.office", described)
        self.assertIn("sensor.office_temp", described)
        self.assertIn("Humidity: —", described)
        self.assertIn("Overhang: none", described)

    def test_a_locked_out_room_says_so_prominently(self):
        described = _forms.describe_room(
            self._room(lockout_reason="Under renovation")
        )
        self.assertIn("LOCKED OUT", described)
        self.assertIn("Under renovation", described)

    def test_a_room_with_no_bands_is_flagged(self):
        described = _forms.describe_room(self._room(bands={}))
        self.assertIn("never be actuated", described)

    def test_an_overhang_is_described_with_both_measurements(self):
        described = _forms.describe_room(
            self._room(overhang_projection_m=0.9, overhang_height_m=2.1)
        )
        self.assertIn("0.9", described)
        self.assertIn("2.1", described)

    def test_the_full_summary_separates_rooms_tariff_and_house(self):
        summary = _forms.describe_configuration(
            [self._room()], "Home electricity plan", "sensor.outdoor"
        )
        self.assertIn("**Rooms**", summary)
        self.assertIn("**House**", summary)
        self.assertIn("Home electricity plan", summary)
        self.assertIn("Abode Power Tariffs", summary)
        self.assertIn("sensor.outdoor", summary)

    def test_an_empty_installation_says_so_rather_than_showing_nothing(self):
        summary = _forms.describe_configuration([], None, None)
        self.assertIn("None configured", summary)
        self.assertIn("Nothing selected", summary)

    def test_the_summary_never_reprints_the_plan(self):
        # The plan lives in Abode Power Tariffs. A second copy here is a second
        # thing to keep in step, and it would go stale first.
        summary = _forms.describe_configuration(
            [self._room()], "Home electricity plan", "sensor.outdoor"
        )
        self.assertNotIn("c/kWh", summary)
        self.assertNotIn("Feed-in", summary)


class TestGlobalConfigurationIsSeparate(unittest.TestCase):
    """House-wide settings are not room settings and should not look like them."""

    def test_the_global_summary_names_the_tariff_source_and_the_outdoor_feed(self):
        summary = _forms.describe_global("Home electricity plan", "sensor.outdoor")
        self.assertIn("**Tariff**", summary)
        self.assertIn("Home electricity plan", summary)
        self.assertIn("Abode Power Tariffs", summary)
        self.assertIn("**Outdoor temperature**", summary)
        self.assertIn("sensor.outdoor", summary)

    def test_unconfigured_global_settings_say_so(self):
        summary = _forms.describe_global(None, None)
        self.assertIn("Nothing selected", summary)

    def test_no_tariff_states_what_is_lost_rather_than_just_being_blank(self):
        summary = _forms.describe_global(None, None)
        self.assertIn("holds comfort", summary)

    def test_the_rooms_summary_is_separate_from_the_global_one(self):
        rooms = _forms.describe_rooms(
            [
                {
                    "room_id": "office",
                    "name": "Office",
                    "climate_entity_ids": ("climate.office",),
                    "bands": {"occupied": {"low": 24.0, "high": 27.0}},
                }
            ]
        )
        self.assertIn("Office", rooms)
        self.assertNotIn("Tariff", rooms)

    def test_no_rooms_says_so(self):
        self.assertIn("No rooms configured", _forms.describe_rooms([]))


# ---------------------------------------------------------------------------
# Layer 2 regulation, staleness, psychrometrics and scheduling.
# ---------------------------------------------------------------------------

_regulate = importlib.import_module("hvac_core.regulate")
_staleness = importlib.import_module("hvac_core.staleness")
_psychro = importlib.import_module("hvac_core.psychro")
_scheduling = importlib.import_module("hvac_core.scheduling")


class TestOuterLoopRegulation(unittest.TestCase):
    """Layer 2 trims the setpoint until the room, not the head, reads target."""

    def _drive(self, state, *, error_c, minutes, regulating=True, steps=1):
        """Run the loop for a number of equal intervals at a constant error."""
        at = NOW
        for _ in range(steps):
            at += timedelta(minutes=minutes)
            _regulate.integrate(
                state,
                target_c=24.0,
                room_c=24.0 + error_c,
                now=at,
                regulating=regulating,
            )
        return at

    def test_the_first_cycle_only_anchors_and_does_not_integrate(self):
        state = _regulate.RegulatorState()
        self._drive(state, error_c=2.0, minutes=1)
        self.assertEqual(state.trim_c, 0.0)

    def test_a_room_warmer_than_target_pulls_the_setpoint_down(self):
        state = _regulate.RegulatorState()
        self._drive(state, error_c=2.0, minutes=10, steps=4)
        self.assertLess(state.trim_c, 0.0)

    def test_a_room_colder_than_target_pushes_the_setpoint_up(self):
        state = _regulate.RegulatorState()
        self._drive(state, error_c=-2.0, minutes=10, steps=4)
        self.assertGreater(state.trim_c, 0.0)

    def test_error_inside_the_deadband_is_not_integrated(self):
        state = _regulate.RegulatorState()
        self._drive(state, error_c=0.2, minutes=10, steps=6)
        self.assertEqual(state.trim_c, 0.0)

    def test_the_trim_cannot_exceed_its_limit(self):
        state = _regulate.RegulatorState()
        self._drive(state, error_c=5.0, minutes=15, steps=200)
        self.assertLessEqual(abs(state.trim_c), _regulate.MAX_TRIM_C)

    def test_a_pinned_trim_says_the_unit_is_not_keeping_up(self):
        state = _regulate.RegulatorState()
        self._drive(state, error_c=5.0, minutes=15, steps=200)
        self.assertTrue(any("not keeping up" in note for note in state.notes))

    def test_nothing_is_integrated_while_the_compressor_is_not_running(self):
        # Anti-windup. A room that is off, coasting or held by an open window
        # has an error no actuator is addressing.
        state = _regulate.RegulatorState()
        self._drive(state, error_c=4.0, minutes=15, steps=20, regulating=False)
        self.assertEqual(state.trim_c, 0.0)

    def test_a_long_gap_is_capped_rather_than_delivered_in_one_step(self):
        # A blocked coordinator or a restart must not dump an hour of
        # accumulated error into a single update.
        capped = _regulate.RegulatorState()
        self._drive(capped, error_c=2.0, minutes=1)
        _regulate.integrate(
            capped, target_c=24.0, room_c=26.0,
            now=NOW + timedelta(hours=6), regulating=True,
        )
        expected = (
            _regulate.INTEGRAL_GAIN_PER_HOUR
            * 2.0
            * _regulate.MAX_INTEGRATION_HOURS
        )
        self.assertAlmostEqual(abs(capped.trim_c), expected, places=6)

    def test_the_commanded_setpoint_is_the_target_plus_the_trim(self):
        state = _regulate.RegulatorState(trim_c=-1.4)
        self.assertEqual(_regulate.commanded_setpoint(state, 24.0), 22.6)

    def test_no_target_commands_nothing(self):
        state = _regulate.RegulatorState(trim_c=-1.4)
        self.assertIsNone(_regulate.commanded_setpoint(state, None))


class TestShortCycleGuard(unittest.TestCase):
    """Short cycling is the most damaging thing a controller can do to a split."""

    def test_holding_the_current_state_is_always_permitted(self):
        state = _regulate.CompressorState(running=True, changed_at=NOW)
        permitted, reason = _regulate.permit_transition(
            state, want_running=True, now=NOW + timedelta(seconds=30)
        )
        self.assertTrue(permitted)
        self.assertIsNone(reason)

    def test_stopping_inside_the_minimum_run_is_refused(self):
        state = _regulate.CompressorState(running=True, changed_at=NOW)
        permitted, reason = _regulate.permit_transition(
            state, want_running=False, now=NOW + timedelta(minutes=3)
        )
        self.assertFalse(permitted)
        self.assertIn("short-cycle guard", reason)

    def test_stopping_after_the_minimum_run_is_permitted(self):
        state = _regulate.CompressorState(running=True, changed_at=NOW)
        permitted, _ = _regulate.permit_transition(
            state, want_running=False, now=NOW + _regulate.MIN_RUN
        )
        self.assertTrue(permitted)

    def test_starting_inside_the_minimum_off_is_refused(self):
        state = _regulate.CompressorState(running=False, changed_at=NOW)
        permitted, reason = _regulate.permit_transition(
            state, want_running=True, now=NOW + timedelta(minutes=1)
        )
        self.assertFalse(permitted)
        self.assertIn("min", reason)

    def test_a_unit_with_no_history_may_transition_immediately(self):
        state = _regulate.CompressorState()
        permitted, _ = _regulate.permit_transition(
            state, want_running=True, now=NOW
        )
        self.assertTrue(permitted)

    def test_a_transition_is_only_recorded_when_the_state_actually_changes(self):
        state = _regulate.CompressorState(running=True, changed_at=NOW)
        _regulate.note_transition(state, running=True, now=NOW + timedelta(hours=1))
        self.assertEqual(state.changed_at, NOW)


class TestStaleness(unittest.TestCase):
    """A reading that is present is not the same as a reading that is current."""

    def test_a_recent_reading_is_fresh(self):
        verdict = _staleness.assess(
            NOW - timedelta(minutes=5), NOW, _staleness.INDOOR_TOLERANCE
        )
        self.assertTrue(verdict.fresh)

    def test_a_reading_past_its_tolerance_is_not(self):
        verdict = _staleness.assess(
            NOW - timedelta(hours=5), NOW, _staleness.INDOOR_TOLERANCE
        )
        self.assertFalse(verdict.fresh)
        self.assertIn("treated as no reading", verdict.reason)

    def test_the_reason_names_the_age_and_the_tolerance(self):
        verdict = _staleness.assess(
            NOW - timedelta(hours=5), NOW, _staleness.INDOOR_TOLERANCE
        )
        self.assertIn("5.0 h", verdict.reason)
        self.assertIn("2 h", verdict.reason)

    def test_a_reading_with_no_timestamp_is_treated_as_fresh(self):
        self.assertTrue(
            _staleness.assess(None, NOW, _staleness.INDOOR_TOLERANCE).fresh
        )

    def test_clock_skew_into_the_future_is_not_a_staleness_fault(self):
        verdict = _staleness.assess(
            NOW + timedelta(minutes=2), NOW, _staleness.INDOOR_TOLERANCE
        )
        self.assertTrue(verdict.fresh)

    def test_presence_is_tolerated_far_longer_than_temperature(self):
        # An mmWave sensor in a room nobody enters legitimately says nothing.
        self.assertGreater(
            _staleness.PRESENCE_TOLERANCE, _staleness.INDOOR_TOLERANCE
        )


class TestDewPoint(unittest.TestCase):
    """Dry bulb alone cannot answer either question this is here for."""

    def test_saturated_air_has_its_dew_point_at_the_dry_bulb(self):
        self.assertAlmostEqual(_psychro.dew_point_c(24.0, 100.0), 24.0, places=1)

    def test_a_drier_room_has_a_lower_dew_point(self):
        self.assertLess(
            _psychro.dew_point_c(24.0, 40.0), _psychro.dew_point_c(24.0, 80.0)
        )

    def test_a_broken_sensor_reporting_zero_does_not_raise(self):
        self.assertLess(_psychro.dew_point_c(24.0, 0.0), 0.0)

    def test_a_known_point_matches_the_magnus_formula(self):
        # 30 C at 60% RH is 21.4 C dew point.
        self.assertAlmostEqual(_psychro.dew_point_c(30.0, 60.0), 21.4, places=1)


class TestOutdoorApparentTemperature(unittest.TestCase):
    """One formula, two conditions, so indoor and outdoor are comparable."""

    def test_at_zero_wind_it_is_the_comfort_index(self):
        # Not a coincidence to be preserved by hand: the comfort index IS the
        # Bureau's apparent temperature with the wind term dropped. If these
        # ever diverge, comparing indoor to outdoor stops being meaningful.
        self.assertEqual(
            _hci.apparent_temperature(26.0, 60.0, 0.0),
            comfort_index(26.0, 60.0),
        )

    def test_wind_makes_the_same_air_feel_cooler(self):
        still = _hci.apparent_temperature(26.0, 60.0, 0.0)
        breezy = _hci.apparent_temperature(26.0, 60.0, 4.0)
        self.assertAlmostEqual(still - breezy, 4.0 * _hci.WIND_COEFF, places=6)

    def test_it_matches_the_bureau_formula(self):
        # AT = Ta + 0.33e - 0.70ws - 4.00
        expected = (
            26.0
            + 0.33 * _hci.vapour_pressure_hpa(26.0, 60.0)
            - 0.70 * 3.0
            - 4.00
        )
        self.assertAlmostEqual(
            _hci.apparent_temperature(26.0, 60.0, 3.0), expected, places=6
        )

    def test_a_negative_wind_reading_is_not_treated_as_a_bonus(self):
        self.assertEqual(
            _hci.apparent_temperature(26.0, 60.0, -5.0),
            _hci.apparent_temperature(26.0, 60.0, 0.0),
        )


class TestFreeCoolingAdvisory(unittest.TestCase):
    """The test is how it will feel, not whether the dry bulb is lower."""

    def _advice(self, **overrides):
        base = {
            "indoor_hci": 29.0,
            "indoor_c": 27.0,
            "indoor_rh": 60.0,
            "outdoor_c": 22.0,
            "outdoor_rh": 45.0,
            "outdoor_wind_ms": None,
            "demand": "cool",
        }
        base.update(overrides)
        return _psychro.free_cooling(**base)

    def test_cooler_and_drier_outdoor_air_is_advised(self):
        self.assertTrue(self._advice().advised)

    def test_cooler_but_wetter_outdoor_air_is_refused(self):
        # A breeze off wet air feels better on arrival. It is still wet air.
        advice = self._advice(
            indoor_hci=27.0, indoor_c=26.0, indoor_rh=45.0,
            outdoor_c=22.0, outdoor_rh=95.0, outdoor_wind_ms=8.0,
        )
        self.assertFalse(advice.advised)
        self.assertIn("the moisture stays", advice.reason)

    def test_the_dew_point_veto_beats_a_large_felt_benefit(self):
        # This is the whole point of keeping two tests. Wind can make wet air
        # feel excellent on arrival; the water is still there afterwards.
        advice = self._advice(
            indoor_hci=30.0, indoor_c=28.0, indoor_rh=45.0,
            outdoor_c=24.0, outdoor_rh=98.0, outdoor_wind_ms=12.0,
        )
        self.assertFalse(advice.advised)

    def test_wind_can_turn_a_marginal_evening_into_an_open_window(self):
        still = self._advice(outdoor_c=27.5, outdoor_rh=40.0)
        breezy = self._advice(
            outdoor_c=27.5, outdoor_rh=40.0, outdoor_wind_ms=9.0
        )
        self.assertFalse(still.advised)
        self.assertTrue(breezy.advised)

    def test_outdoor_wind_is_damped_before_it_is_believed(self):
        # Ten metres in the clear is not what reaches a person in a room.
        advice = self._advice(outdoor_c=27.5, outdoor_rh=40.0, outdoor_wind_ms=10.0)
        undamped = _hci.apparent_temperature(27.5, 40.0, 10.0)
        self.assertGreater(advice.outdoor_apparent_c, undamped)

    def test_no_wind_reading_assumes_still_air_and_says_so(self):
        advice = self._advice(outdoor_c=27.5, outdoor_rh=40.0)
        self.assertIn("no wind reading", advice.reason)

    def test_a_wind_reading_is_named_in_the_reason(self):
        advice = self._advice(outdoor_c=27.5, outdoor_rh=40.0, outdoor_wind_ms=9.0)
        self.assertIn("9.0 m/s", advice.reason)

    def test_warmer_outdoor_air_is_refused(self):
        advice = self._advice(
            indoor_hci=26.0, indoor_c=24.0, indoor_rh=50.0,
            outdoor_c=33.0, outdoor_rh=40.0,
        )
        self.assertFalse(advice.advised)

    def test_a_sunlit_room_is_more_willing_to_open_up(self):
        # The indoor side is the comfort index with its corrections, so a room
        # that feels hotter than its air temperature says so here too.
        plain = self._advice(indoor_hci=27.0, outdoor_c=25.0, outdoor_rh=45.0)
        sunlit = self._advice(indoor_hci=30.0, outdoor_c=25.0, outdoor_rh=45.0)
        self.assertFalse(plain.advised)
        self.assertTrue(sunlit.advised)

    def test_a_room_not_asking_to_be_cooled_gets_no_advice(self):
        self.assertFalse(self._advice(demand=None).advised)

    def test_missing_outdoor_humidity_refuses_rather_than_guessing(self):
        advice = self._advice(outdoor_rh=None)
        self.assertFalse(advice.advised)
        self.assertIn("needs a comfort reading", advice.reason)

    def test_no_comfort_reading_refuses(self):
        self.assertFalse(self._advice(indoor_hci=None).advised)


class TestCondensationRisk(unittest.TestCase):
    """Surfaces below the dew point sweat, and mould follows."""

    def test_a_setpoint_near_the_dew_point_is_flagged(self):
        risk = _psychro.condensation_risk(
            indoor_c=28.0, indoor_rh=85.0, setpoint_c=25.0
        )
        self.assertTrue(risk.at_risk)
        self.assertIn("dehumidify", risk.reason)

    def test_a_dry_room_is_not_flagged(self):
        risk = _psychro.condensation_risk(
            indoor_c=26.0, indoor_rh=40.0, setpoint_c=24.0
        )
        self.assertFalse(risk.at_risk)
        self.assertIsNone(risk.reason)

    def test_nothing_is_claimed_without_a_reading(self):
        risk = _psychro.condensation_risk(
            indoor_c=None, indoor_rh=85.0, setpoint_c=22.0
        )
        self.assertFalse(risk.at_risk)
        self.assertIsNone(risk.dew_point_c)


class TestPreconditionDeadline(unittest.TestCase):
    """A deadline four hours out should cost nothing until it is close."""

    def test_a_distant_deadline_defers_the_start(self):
        plan = _scheduling.plan_precondition(
            now=NOW, deadline=NOW + timedelta(hours=4), hours_needed=0.75
        )
        self.assertFalse(plan.start_now)
        self.assertIn("waiting", plan.reason)

    def test_a_deadline_inside_the_estimate_starts_the_pull(self):
        plan = _scheduling.plan_precondition(
            now=NOW, deadline=NOW + timedelta(minutes=50), hours_needed=0.75
        )
        self.assertTrue(plan.start_now)

    def test_the_margin_is_added_to_the_estimate(self):
        # 40 min of pull plus 15 min margin means a 50 min deadline starts now.
        plan = _scheduling.plan_precondition(
            now=NOW, deadline=NOW + timedelta(minutes=50), hours_needed=40 / 60
        )
        self.assertTrue(plan.start_now)

    def test_a_request_with_no_deadline_starts_immediately(self):
        plan = _scheduling.plan_precondition(
            now=NOW, deadline=None, hours_needed=0.5
        )
        self.assertTrue(plan.start_now)
        self.assertIn("no deadline", plan.reason)

    def test_an_unconverged_model_starts_rather_than_risking_the_deadline(self):
        plan = _scheduling.plan_precondition(
            now=NOW, deadline=NOW + timedelta(hours=6), hours_needed=None
        )
        self.assertTrue(plan.start_now)
        self.assertIn("cannot estimate", plan.reason)

    def test_a_deadline_already_past_starts_now(self):
        plan = _scheduling.plan_precondition(
            now=NOW, deadline=NOW - timedelta(minutes=5), hours_needed=2.0
        )
        self.assertTrue(plan.start_now)


class TestSleepRamp(unittest.TestCase):
    """Three degrees of band should not arrive in one step at bedtime."""

    DAY = ComfortBand(24.0, 27.0)
    NIGHT = ComfortBand(21.0, 24.0)

    def test_the_band_starts_at_the_mode_it_came_from(self):
        band, _ = _scheduling.ramped_band(
            from_band=self.DAY, to_band=self.NIGHT, changed_at=NOW, now=NOW
        )
        self.assertAlmostEqual(band.low, 24.0)

    def test_the_band_is_halfway_at_the_halfway_point(self):
        band, _ = _scheduling.ramped_band(
            from_band=self.DAY, to_band=self.NIGHT,
            changed_at=NOW, now=NOW + _scheduling.SLEEP_RAMP / 2,
        )
        self.assertAlmostEqual(band.low, 22.5)
        self.assertAlmostEqual(band.high, 25.5)

    def test_the_band_arrives_after_the_ramp(self):
        band, reason = _scheduling.ramped_band(
            from_band=self.DAY, to_band=self.NIGHT,
            changed_at=NOW, now=NOW + _scheduling.SLEEP_RAMP,
        )
        self.assertEqual(band.low, 21.0)
        self.assertIsNone(reason)

    def test_the_ramp_says_how_long_is_left(self):
        _, reason = _scheduling.ramped_band(
            from_band=self.DAY, to_band=self.NIGHT,
            changed_at=NOW, now=NOW + timedelta(minutes=15),
        )
        self.assertIn("45 min to go", reason)

    def test_a_room_with_only_one_band_is_unchanged(self):
        band, reason = _scheduling.ramped_band(
            from_band=None, to_band=self.NIGHT, changed_at=NOW, now=NOW
        )
        self.assertIs(band, self.NIGHT)
        self.assertIsNone(reason)


# ---------------------------------------------------------------------------
# Weather forecast, and the latent/sensible split it sits beside.
# ---------------------------------------------------------------------------

_weather = importlib.import_module("hvac_core.weather")


def _hourly(temps, start=None, **fields):
    """Build a forecast response from a list of hourly temperatures."""
    begin = start or NOW
    return {
        "weather.home": {
            "forecast": [
                {
                    "datetime": (begin + timedelta(hours=i)).isoformat(),
                    "temperature": t,
                    **fields,
                }
                for i, t in enumerate(temps)
            ]
        }
    }


class TestForecastTrajectory(unittest.TestCase):
    """The forecast answers what the thermal model cannot: what happens next."""

    def test_a_response_keyed_by_entity_is_read(self):
        t = _weather.WeatherTrajectory.from_response(_hourly([20, 25, 30]), NOW)
        self.assertEqual(len(t.points), 3)

    def test_a_bare_forecast_list_is_also_read(self):
        t = _weather.WeatherTrajectory.from_response(
            {"forecast": [{"datetime": NOW.isoformat(), "temperature": 21.0}]}, NOW
        )
        self.assertEqual(len(t.points), 1)

    def test_the_hottest_hour_in_a_window_is_found(self):
        t = _weather.WeatherTrajectory.from_response(_hourly([24, 31, 38, 33]), NOW)
        peak = t.peak_between(NOW, NOW + timedelta(hours=4))
        self.assertEqual(peak[0], 38.0)
        self.assertEqual(peak[1], NOW + timedelta(hours=2))

    def test_an_hourly_forecast_answers_for_every_minute_of_its_hour(self):
        t = _weather.WeatherTrajectory.from_response(_hourly([24, 31]), NOW)
        self.assertEqual(t.temperature_at(NOW + timedelta(minutes=40)), 24.0)
        self.assertEqual(t.temperature_at(NOW + timedelta(minutes=70)), 31.0)

    def test_before_the_forecast_starts_there_is_no_answer(self):
        t = _weather.WeatherTrajectory.from_response(_hourly([24]), NOW)
        self.assertIsNone(t.temperature_at(NOW - timedelta(hours=1)))

    def test_well_past_the_end_there_is_no_answer(self):
        t = _weather.WeatherTrajectory.from_response(_hourly([24]), NOW)
        self.assertIsNone(t.temperature_at(NOW + timedelta(hours=5)))

    def test_a_point_with_no_temperature_is_rejected(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.point_from_forecast({"datetime": NOW.isoformat()})

    def test_an_empty_forecast_is_rejected(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.WeatherTrajectory.from_response({"forecast": []}, NOW)

    def test_a_response_that_is_not_a_mapping_is_rejected(self):
        with self.assertRaises(_weather.ForecastPayloadError):
            _weather.WeatherTrajectory.from_response(None, NOW)

    def test_missing_optional_fields_do_not_lose_the_forecast(self):
        # A feed without cloud cover still answers the question precool asks.
        t = _weather.WeatherTrajectory.from_response(_hourly([24, 38]), NOW)
        self.assertIsNone(t.solar_fraction_at(NOW))
        self.assertEqual(t.temperature_at(NOW), 24.0)


class TestSolarFraction(unittest.TestCase):
    """Cloud cover and UV are what feeds publish. Irradiance is not."""

    def test_clear_sky_passes_everything(self):
        point = _weather.point_from_forecast(
            {"datetime": NOW.isoformat(), "temperature": 30.0, "cloud_coverage": 0}
        )
        self.assertAlmostEqual(point.solar_fraction, 1.0)

    def test_overcast_still_passes_diffuse_radiation(self):
        point = _weather.point_from_forecast(
            {"datetime": NOW.isoformat(), "temperature": 24.0, "cloud_coverage": 100}
        )
        self.assertAlmostEqual(point.solar_fraction, _weather.OVERCAST_TRANSMISSION)

    def test_partial_cloud_is_between(self):
        point = _weather.point_from_forecast(
            {"datetime": NOW.isoformat(), "temperature": 27.0, "cloud_coverage": 45}
        )
        self.assertGreater(point.solar_fraction, _weather.OVERCAST_TRANSMISSION)
        self.assertLess(point.solar_fraction, 1.0)

    def test_uv_is_used_only_when_cloud_cover_is_absent(self):
        both = _weather.point_from_forecast(
            {
                "datetime": NOW.isoformat(),
                "temperature": 30.0,
                "cloud_coverage": 100,
                "uv_index": 9,
            }
        )
        self.assertAlmostEqual(both.solar_fraction, _weather.OVERCAST_TRANSMISSION)

    def test_uv_at_night_reads_as_no_sun(self):
        point = _weather.point_from_forecast(
            {"datetime": NOW.isoformat(), "temperature": 19.0, "uv_index": 0}
        )
        self.assertEqual(point.solar_fraction, 0.0)


class TestPrecoolSeesTheAfternoon(unittest.TestCase):
    """The case the whole module exists for."""

    def test_a_mild_midday_before_a_hot_afternoon_is_demand(self):
        # 26 outside, 25 inside, 38 coming. The old current-conditions test
        # said no demand and the free window went unused.
        verdict = _weather.demand_ahead(
            _weather.WeatherTrajectory.from_response(
                _hourly([26, 29, 33, 36, 38, 37, 34]), NOW
            ),
            now=NOW,
            indoor_c=25.0,
        )
        self.assertTrue(verdict.demand_ahead)
        self.assertEqual(verdict.peak_c, 38.0)

    def test_a_mild_day_throughout_is_not_demand(self):
        verdict = _weather.demand_ahead(
            _weather.WeatherTrajectory.from_response(
                _hourly([24, 25, 26, 26, 25]), NOW
            ),
            now=NOW,
            indoor_c=25.0,
        )
        self.assertFalse(verdict.demand_ahead)
        self.assertIn("not enough of a load", verdict.reason)

    def test_a_peak_beyond_the_lookahead_is_not_counted(self):
        verdict = _weather.demand_ahead(
            _weather.WeatherTrajectory.from_response(
                _hourly([24] * 14 + [40]), NOW
            ),
            now=NOW,
            indoor_c=25.0,
        )
        self.assertFalse(verdict.demand_ahead)

    def test_no_forecast_says_so_rather_than_saying_no(self):
        verdict = _weather.demand_ahead(None, now=NOW, indoor_c=25.0)
        self.assertFalse(verdict.demand_ahead)
        self.assertIn("falling back", verdict.reason)

    def test_no_indoor_reading_refuses(self):
        verdict = _weather.demand_ahead(
            _weather.WeatherTrajectory.from_response(_hourly([38]), NOW),
            now=NOW,
            indoor_c=None,
        )
        self.assertFalse(verdict.demand_ahead)

    def test_the_reason_names_the_peak_and_when_it_falls(self):
        verdict = _weather.demand_ahead(
            _weather.WeatherTrajectory.from_response(_hourly([26, 38]), NOW),
            now=NOW,
            indoor_c=25.0,
        )
        self.assertIn("38.0 C", verdict.reason)


class TestConstantVapourPressure(unittest.TestCase):
    """0.8.9, finding 17: the floor case when `k_rh_cooling` has not converged.

    `sensitivity_to_temperature`/`sensitivity_to_humidity` are deleted —
    replaced by projecting both routes forward and evaluating the index at
    each end, which is what `TestLatentRouteUsesTheLearnedRates` below
    exercises end to end. This class covers the one new pure helper on its
    own terms.
    """

    def test_moisture_held_fixed_raises_relative_humidity_as_it_cools(self):
        # Same vapour pressure, colder air: relative humidity must rise.
        rh = _hci.relative_humidity_at_constant_vapour_pressure(26.0, 60.0, 23.0)
        self.assertGreater(rh, 60.0)

    def test_no_temperature_change_leaves_relative_humidity_unchanged(self):
        rh = _hci.relative_humidity_at_constant_vapour_pressure(26.0, 60.0, 26.0)
        self.assertAlmostEqual(rh, 60.0, places=6)


class TestLatentRouteUsesTheLearnedRates(unittest.TestCase):
    """The model learns the two rates separately. Now the decision uses them."""

    def _inputs(self, **overrides):
        base = {
            "now": NOW,
            "temperature_c": 27.0,
            "relative_humidity": 70.0,
            "presence": True,
            "can_dry": True,
            "can_fan_only": True,
        }
        base.update(overrides)
        return RoomInputs(**base)

    def _step(self, **overrides):
        trace = _models.DecisionTrace(room_id="office", at=NOW, mode=Mode.OCCUPIED)
        band = ComfortBand(24.0, 26.0)
        inputs = self._inputs(**overrides)
        hci = comfort_index(inputs.temperature_c, inputs.relative_humidity)
        return _modes.select_actuator(Mode.OCCUPIED, band, hci, inputs, trace), trace

    def test_a_strong_latent_response_chooses_dry(self):
        step, trace = self._step(
            k_sensible_c_per_hour=1.0, k_latent_rh_per_hour=20.0
        )
        self.assertIs(step, ActuatorStep.DRY)
        self.assertTrue(any("load is latent" in r for r in trace.reasons))

    def test_a_strong_sensible_response_chooses_the_compressor(self):
        step, trace = self._step(
            k_sensible_c_per_hour=4.0, k_latent_rh_per_hour=2.0
        )
        self.assertIs(step, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("load is sensible" in r for r in trace.rejected))

    def test_the_same_humidity_decides_differently_at_different_temperatures(self):
        # 65% at 22 C and 65% at 30 C are different loads. The old threshold
        # treated them identically; this is the whole point of the change.
        # Identical learned rates, identical humidity, opposite answers. At
        # 22 C a point of humidity moves the index by 0.087 and cooling wins;
        # at 30 C it moves it by 0.140 and drying wins. The old threshold saw
        # "65%" both times.
        rates = {"k_sensible_c_per_hour": 2.2, "k_latent_rh_per_hour": 22.6}
        cool_room, _ = self._step(temperature_c=22.0, relative_humidity=65.0, **rates)
        warm_room, _ = self._step(temperature_c=30.0, relative_humidity=65.0, **rates)
        self.assertIs(cool_room, ActuatorStep.COMPRESSOR)
        self.assertIs(warm_room, ActuatorStep.DRY)

    def test_an_unconverged_model_falls_back_and_says_so(self):
        step, trace = self._step(relative_humidity=70.0)
        self.assertIs(step, ActuatorStep.DRY)
        self.assertTrue(any("has not converged" in r for r in trace.reasons))

    def test_the_fallback_threshold_still_applies_below_it(self):
        step, trace = self._step(temperature_c=30.0, relative_humidity=50.0)
        self.assertIs(step, ActuatorStep.COMPRESSOR)
        self.assertTrue(any("fallback threshold" in r for r in trace.rejected))

    def test_a_room_with_no_latent_response_never_dries(self):
        step, trace = self._step(
            k_sensible_c_per_hour=2.0, k_latent_rh_per_hour=0.0
        )
        self.assertIs(step, ActuatorStep.COMPRESSOR)
        self.assertTrue(
            any("never shown a latent response" in r for r in trace.rejected)
        )

    def test_converged_k_rh_cooling_is_used_in_the_cooling_projection(self):
        """State 1: the room's own measured response, not the constant-
        vapour-pressure floor."""
        step, trace = self._step(
            k_sensible_c_per_hour=2.0,
            k_latent_rh_per_hour=6.0,
            k_rh_cooling_per_hour=-4.0,  # net condensation while cooling
        )
        self.assertTrue(
            any("measured humidity response" in r for r in trace.reasons + trace.rejected)
        )
        # A negative k_rh_cooling means cooling removes moisture too, which
        # can only help cooling's case relative to the unconverged floor.
        floor_step, _ = self._step(
            k_sensible_c_per_hour=2.0, k_latent_rh_per_hour=6.0
        )
        self.assertIs(step, ActuatorStep.COMPRESSOR)
        self.assertIs(floor_step, ActuatorStep.COMPRESSOR)

    def test_unconverged_k_rh_cooling_gives_an_improvement_of_exactly_k_sensible(self):
        """State 2: constant absolute humidity is a floor, not an answer.

        At constant vapour pressure, `comfort_index(T, RH) -
        comfort_index(T - k_sensible, RH')` collapses to exactly
        `k_sensible` — the vapour term cancels because `RH'` was solved to
        hold it fixed. This is the "1.0 derivative" the review pointed at,
        expressed without a derivative.
        """
        inputs = self._inputs(
            temperature_c=27.0,
            relative_humidity=70.0,
            k_sensible_c_per_hour=1.7,
            k_latent_rh_per_hour=6.0,
        )
        trace = _models.DecisionTrace(room_id="office", at=NOW, mode=Mode.OCCUPIED)
        _modes.select_actuator(
            Mode.OCCUPIED, ComfortBand(24.0, 26.0),
            comfort_index(27.0, 70.0), inputs, trace,
        )
        message = "\n".join(trace.reasons + trace.rejected)
        self.assertIn("condensation not yet measured", message)
        self.assertIn("1.70", message)

    def test_a_humid_room_where_drying_now_wins(self):
        """A room the old constant-RH derivative sent to cooling.

        A humid room with a middling sensible rate and a strong latent one:
        under the deleted `sensitivity_to_temperature`, which overstates
        cooling by up to 64% in hot, humid conditions, cooling used to look
        artificially strong enough to win. Projected as two index
        evaluations instead, it does not.
        """
        step, trace = self._step(
            temperature_c=30.0,
            relative_humidity=80.0,
            k_sensible_c_per_hour=1.2,
            k_latent_rh_per_hour=10.0,
        )
        self.assertIs(step, ActuatorStep.DRY)
        self.assertTrue(any("load is latent" in r for r in trace.reasons))

    def test_the_25_percent_handicap_still_applies(self):
        """Cooling within the DRY_MODE_ADVANTAGE margin still loses the tie."""
        # Tuned so cooling is faster in raw HCI/hour terms (1.30 vs 1.12)
        # but not by the required 25% margin (1.12 * 1.25 = 1.395 > 1.30).
        step, _trace = self._step(
            temperature_c=30.0,
            relative_humidity=75.0,
            k_sensible_c_per_hour=1.3,
            k_latent_rh_per_hour=8.0,
        )
        self.assertIs(step, ActuatorStep.DRY)

    def test_the_trace_names_the_deciding_state(self):
        # State 3: k_sensible/k_latent themselves unconverged.
        _, unconverged = self._step(relative_humidity=70.0)
        self.assertTrue(
            any("has not converged" in r for r in unconverged.reasons)
        )
        # State 2: converged rates, k_rh_cooling not.
        _, floor = self._step(
            k_sensible_c_per_hour=2.0, k_latent_rh_per_hour=6.0
        )
        message = "\n".join(floor.reasons + floor.rejected)
        self.assertIn("condensation not yet measured", message)
        # State 1: k_rh_cooling converged too.
        _, measured = self._step(
            k_sensible_c_per_hour=2.0,
            k_latent_rh_per_hour=6.0,
            k_rh_cooling_per_hour=1.0,
        )
        message = "\n".join(measured.reasons + measured.rejected)
        self.assertIn("measured humidity response", message)


class TestNaiveTimestampsDoNotCrashTheLoop(unittest.TestCase):
    """A forecast without an offset took every room down. It must not.

    `TypeError: can't compare offset-naive and offset-aware datetimes`, raised
    out of `peak_between` and straight through the update loop, so one
    integration's timestamp format stopped the whole controller.
    """

    BRISBANE = timezone(timedelta(hours=10))

    def test_a_naive_forecast_point_is_made_aware(self):
        point = _weather.point_from_forecast(
            {"datetime": "2026-08-08T14:00:00", "temperature": 33.0},
            self.BRISBANE,
        )
        self.assertIsNotNone(point.at.tzinfo)

    def test_a_naive_forecast_can_be_compared_against_an_aware_clock(self):
        response = {
            "forecast": [
                {"datetime": f"2026-08-08T{h:02d}:00:00", "temperature": 24.0 + h}
                for h in range(12, 20)
            ]
        }
        trajectory = _weather.WeatherTrajectory.from_response(
            response, NOW, self.BRISBANE
        )
        now = datetime(2026, 8, 8, 12, 0, tzinfo=self.BRISBANE)
        # The call that raised.
        peak = trajectory.peak_between(now, now + timedelta(hours=10))
        self.assertIsNotNone(peak)

    def test_a_naive_timestamp_is_read_as_local_not_utc(self):
        # Assuming UTC would move a Brisbane afternoon ten hours and still look
        # plausible, which is worse than the crash because nothing reports it.
        point = _weather.point_from_forecast(
            {"datetime": "2026-08-08T14:00:00", "temperature": 33.0},
            self.BRISBANE,
        )
        self.assertEqual(point.at.utcoffset(), timedelta(hours=10))

    def test_an_offset_that_is_present_is_left_alone(self):
        point = _weather.point_from_forecast(
            {"datetime": "2026-08-08T14:00:00+00:00", "temperature": 33.0},
            self.BRISBANE,
        )
        self.assertEqual(point.at.utcoffset(), timedelta(0))

    def test_a_naive_tariff_interval_is_made_aware(self):
        interval = interval_from_response(
            {
                "start_time": "2026-08-08T16:00:00",
                "end_time": "2026-08-08T16:30:00",
                "rate": "peak",
            },
            self.BRISBANE,
        )
        self.assertIsNotNone(interval.start.tzinfo)
        self.assertTrue(
            interval.contains(datetime(2026, 8, 8, 16, 10, tzinfo=self.BRISBANE))
        )

    def test_tariff_windows_are_local_wall_time_not_utc(self):
        # A 16:00 peak carried in UTC collapses to a 06:00 window, which the
        # demand forecast would compare against the local clock and believe.
        response = {
            "intervals": [
                {
                    "start_time": "2026-08-08T06:00:00+00:00",
                    "end_time": "2026-08-08T06:30:00+00:00",
                    "rate": "peak",
                    "constraints": ["no_grid_import"],
                    "coasting_permitted": False,
                }
            ]
        }
        series = TariffSeries.from_response(response, NOW, self.BRISBANE)
        self.assertEqual(series.windows()[0].start, time(16, 0))


class TestDewPointIsAlwaysPublished(unittest.TestCase):
    """A live install showed `dew_point_c: null` on a room with both sensors.

    Free cooling declines to compute it when the room is not asking to be
    cooled, and condensation declines when there is no setpoint. An unoccupied
    room hits both, so it published nothing — despite having the readings, and
    despite an empty shut-up room being exactly where mould grows.
    """

    def _trace(self, **overrides):
        base = {
            "now": NOW,
            "temperature_c": 21.0,
            "relative_humidity": 78.0,
            "presence": False,
        }
        base.update(overrides)
        room = RoomConfig(
            room_id="office",
            name="Office",
            climate_entity_ids=("climate.office",),
            bands={Mode.OCCUPIED: ComfortBand(24.0, 27.0)},
        )
        return evaluate_room(room, RoomInputs(**base))

    def test_an_unoccupied_room_still_publishes_its_dew_point(self):
        trace = self._trace()
        self.assertEqual(trace.mode, Mode.UNOCCUPIED)
        self.assertIsNotNone(trace.dew_point_c)

    def test_the_value_is_correct(self):
        trace = self._trace()
        self.assertAlmostEqual(trace.dew_point_c, _psychro.dew_point_c(21.0, 78.0), 1)

    def test_a_room_with_no_humidity_reading_publishes_nothing(self):
        self.assertIsNone(self._trace(relative_humidity=None).dew_point_c)

    def test_an_occupied_room_cooling_still_publishes_it(self):
        trace = self._trace(temperature_c=29.0, relative_humidity=70.0, presence=True)
        self.assertIsNotNone(trace.dew_point_c)


class TestSolarTermIsNotSilentlyDropped(unittest.TestCase):
    """0.8.6. A sunlit room with an unconverged solar term cannot be predicted.

    `drift_rate` added the solar term only when `k_solar` had converged but
    returned a rate either way, so a west-facing room in the afternoon got a
    drift estimate built from heat loss alone — missing its largest
    contribution. `holds_through` then said the band would hold and the room
    entered COAST with the sun full on the glass.
    """

    def _loss_only(self):
        """k_loss converged, k_solar never observed."""
        model = _thermal.ThermalModel()
        for _ in range(60):
            model.observe(_interval(indoor_end_c=24.15, outdoor_c=30.0))
        return model

    def test_k_loss_converges_and_k_solar_does_not(self):
        model = self._loss_only()
        self.assertTrue(model.k_loss.converged)
        self.assertFalse(model.k_solar.converged)

    def test_a_shaded_room_still_predicts(self):
        model = self._loss_only()
        self.assertIsNotNone(model.drift_rate(24.0, 30.0, direct_sun=False))

    def test_a_sunlit_room_refuses_to_predict(self):
        model = self._loss_only()
        self.assertIsNone(model.drift_rate(24.0, 30.0, direct_sun=True))

    def test_coast_is_therefore_refused_in_the_sun(self):
        model = self._loss_only()
        self.assertIsNone(
            model.holds_through(
                24.0, 25.0, direct_sun=True, hours=1.0, lower_c=22.0, upper_c=27.0
            )
        )

    def test_a_converged_solar_term_predicts_again(self):
        model = self._loss_only()
        for _ in range(60):
            model.observe(
                _interval(indoor_end_c=24.35, outdoor_c=30.0, direct_sun=True)
            )
        self.assertTrue(model.k_solar.converged)
        self.assertIsNotNone(model.drift_rate(24.0, 30.0, direct_sun=True))


class TestDryModeIsNotAPassiveInterval(unittest.TestCase):
    """0.8.6. Dry mode energises the compressor, so the room is not drifting.

    `observe` ran the passive update whenever `compressor` was zero, and
    `compressor` reports a sensible direction that dry mode does not have. So
    every interval spent drying was folded into `k_loss` and `k_solar` as
    though nothing had been driving the room.
    """

    def test_a_drying_interval_teaches_nothing_about_heat_loss(self):
        model = _thermal.ThermalModel()
        model.observe(_interval(indoor_end_c=23.5, drying=True, humidity_end=55.0))
        self.assertEqual(model.k_loss.samples, 0)
        self.assertEqual(model.k_solar.samples, 0)

    def test_it_still_teaches_the_latent_coefficient(self):
        model = _thermal.ThermalModel()
        model.observe(_interval(drying=True, humidity_end=55.0))
        self.assertEqual(model.k_latent.samples, 1)

    def test_an_idle_interval_still_teaches_heat_loss(self):
        model = _thermal.ThermalModel()
        model.observe(_interval(indoor_end_c=24.15))
        self.assertEqual(model.k_loss.samples, 1)


class TestStoppingTheCompressor(unittest.TestCase):
    """0.8.7. NONE means leave the unit alone; OFF means stop it.

    They were one value, and the actuator inferred a stop from the mode —
    catching lockout and unoccupied and silently missing the other three.
    """

    def test_coasting_stops_the_unit(self):
        """Coasting held by running is not coasting."""
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=26.0,
                relative_humidity=50.0,
                presence=True,
                predicted_to_hold=True,
            ),
        )
        self.assertIs(trace.mode, Mode.COAST)
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_a_deferred_precondition_stops_the_unit(self):
        """The deferral is the feature. Leaving it on is starting."""
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=32.0,
                relative_humidity=60.0,
                presence=False,
                heading_home=True,
                precondition_deadline=NOW + timedelta(hours=6),
                precondition_ready=False,
            ),
        )
        self.assertIs(trace.mode, Mode.PRECONDITION)
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_within_band_leaves_the_unit_alone(self):
        """The majority of the controller's life, and it must not stop.

        The unit holds the trimmed setpoint against its own sensor between
        our thirty-second decisions. Stopping here would cycle the compressor
        every time a room reached comfort.
        """
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=25.5,
                relative_humidity=45.0,
                presence=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertIn("within band", trace.reasons)

    def test_a_missing_reading_holds_rather_than_stops(self):
        """A dead sensor is not grounds for withdrawing comfort.

        Finding 3's reasoning: a guard that fails closed against comfort on
        every question it cannot answer is the fault 0.8.6 was spent
        reversing.
        """
        trace = evaluate_room(
            room(),
            RoomInputs(now=NOW, temperature_c=None, presence=True),
        )
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertTrue(
            any("not reporting" in r for r in trace.rejected), trace.rejected
        )

    def test_the_trace_says_which_input_is_missing(self):
        """One line covering both said nothing about where to look."""
        no_band = evaluate_room(
            room(bands={}),
            RoomInputs(
                now=NOW,
                temperature_c=30.0,
                relative_humidity=60.0,
                presence=True,
            ),
        )
        self.assertIs(no_band.actuator, ActuatorStep.NONE)
        self.assertTrue(
            any("no band configured" in r for r in no_band.rejected),
            no_band.rejected,
        )
        self.assertFalse(
            any("not reporting" in r for r in no_band.rejected),
            no_band.rejected,
        )

    def test_a_missing_reading_names_the_entity(self):
        """0.8.9: a fault to go looking at, not a shrugged-off configuration
        state — named so the trace says which sensor to check."""
        missing_temp = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=None,
                relative_humidity=60.0,
                temperature_entity_id="sensor.office_temperature",
                humidity_entity_id="sensor.office_humidity",
                presence=True,
            ),
        )
        self.assertTrue(
            any(
                "sensor.office_temperature" in r and "not reporting" in r
                for r in missing_temp.rejected
            ),
            missing_temp.rejected,
        )
        self.assertFalse(
            any("sensor.office_humidity" in r for r in missing_temp.rejected)
        )

        missing_both = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=None,
                relative_humidity=None,
                temperature_entity_id="sensor.office_temperature",
                humidity_entity_id="sensor.office_humidity",
                presence=True,
            ),
        )
        self.assertTrue(
            any(
                "sensor.office_temperature" in r and "sensor.office_humidity" in r
                for r in missing_both.rejected
            ),
            missing_both.rejected,
        )

    def test_an_open_window_stops_a_running_unit(self):
        """The interlock's whole purpose.

        Refusing to start is not the same thing: the thermostat sees return
        air above setpoint, runs continuously, and never reaches it. Nothing
        bounds it and nothing appears in the log.
        """
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=32.0,
                relative_humidity=70.0,
                presence=True,
                opening_open=True,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_the_power_refusal_reaches_the_hardware(self):
        """It is the only mechanism holding `no_grid_import`.

        Until 0.8.7 the refusal reached the trace and stopped there, so a
        room already running kept running through the whole window.
        """
        trace = evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=32.0,
                relative_humidity=60.0,
                presence=True,
                power_available=False,
            ),
        )
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_off_is_not_a_rung_on_the_cost_ladder(self):
        """The ordering is ways to deliver comfort. Stopping is not one."""
        self.assertNotIn(ActuatorStep.OFF, _models.ACTUATOR_ORDER)


class TestOpeningDebounce(unittest.TestCase):
    """0.8.7. A door held open for twenty seconds is not a window left open.

    Stopping immediately costs a compressor stop and then the five-minute
    minimum off. Never stopping was the defect. The interlock fires at once;
    only the stop waits.
    """

    def _trace(self, open_for):
        return evaluate_room(
            room(),
            RoomInputs(
                now=NOW,
                temperature_c=32.0,
                relative_humidity=70.0,
                presence=True,
                opening_open=True,
                opening_open_since=None if open_for is None else NOW - open_for,
            ),
        )

    def test_a_door_open_briefly_leaves_the_unit_alone(self):
        trace = self._trace(timedelta(seconds=20))
        self.assertIs(trace.actuator, ActuatorStep.NONE)

    def test_nothing_is_actuated_into_an_open_room_either_way(self):
        """The interlock itself is not debounced, only the stop."""
        trace = self._trace(timedelta(seconds=20))
        self.assertIsNot(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(
            any("an opening in this room is open" in r for r in trace.rejected),
            trace.rejected,
        )

    def test_the_trace_says_how_long_is_left(self):
        trace = self._trace(timedelta(seconds=20))
        self.assertTrue(
            any("in case it closes" in r for r in trace.rejected), trace.rejected
        )
        self.assertFalse(
            any("another 0 minute" in r for r in trace.rejected), trace.rejected
        )

    def test_an_opening_left_open_stops_the_unit(self):
        trace = self._trace(timedelta(minutes=3))
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_the_boundary_stops_the_unit(self):
        trace = self._trace(_modes.OPENING_STOP_DEBOUNCE)
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_an_unknown_age_stops_the_unit(self):
        """An unknown age is not a young one.

        A stale contact or a missing state gives no age. Holding on that would
        leave a unit running against an opening nobody can date.
        """
        trace = self._trace(None)
        self.assertIs(trace.actuator, ActuatorStep.OFF)


class TestOutdoorUnits(unittest.TestCase):
    """0.8.8. A room has heads; heads sit on outdoor units.

    Two independent things. A room can be served by two heads, and a head can
    share its compressor with a head in another room. Neither implies the
    other, and both occur.
    """

    def test_a_head_with_no_group_is_its_own_compressor(self):
        """A house that declares nothing behaves as it always did."""
        config = room(climate_entity_ids=("climate.office",))
        self.assertEqual(config.group_of("climate.office"), "climate.office")
        self.assertEqual(config.groups, ("climate.office",))

    def test_two_heads_in_one_room_on_one_unit_are_one_compressor(self):
        """The lounge: two indoor units, one outdoor unit, one room."""
        config = room(
            climate_entity_ids=("climate.lounge_n", "climate.lounge_s"),
            head_groups={
                "climate.lounge_n": "Lounge pair",
                "climate.lounge_s": "Lounge pair",
            },
        )
        self.assertEqual(config.groups, ("Lounge pair",))

    def test_two_heads_in_one_room_on_two_units_are_two_compressors(self):
        """Also a real case, and the reason the question is per head."""
        config = room(
            climate_entity_ids=("climate.lounge_n", "climate.lounge_s"),
        )
        self.assertEqual(
            config.groups, ("climate.lounge_n", "climate.lounge_s")
        )

    def test_two_rooms_naming_the_same_unit_share_a_compressor(self):
        """Study and guest. Membership is derived from the name, not an object."""
        study = room(
            room_id="study",
            climate_entity_ids=("climate.study",),
            head_groups={"climate.study": "Study and guest"},
        )
        guest = room(
            room_id="guest",
            climate_entity_ids=("climate.guest",),
            head_groups={"climate.guest": "Study and guest"},
        )
        self.assertEqual(study.groups, guest.groups)

    def test_a_group_is_listed_once_per_room(self):
        config = room(
            climate_entity_ids=("climate.a", "climate.b", "climate.c"),
            head_groups={"climate.a": "Pair", "climate.b": "Pair"},
        )
        self.assertEqual(config.groups, ("Pair", "climate.c"))


class TestSensibleRateAt(unittest.TestCase):
    """0.8.10. Closes the gap disclosed at the 0.8.9 handover: the dry-versus-
    cool comparison read the pooled `k_sensible` regardless of approach.
    """

    def test_a_converged_bin_is_preferred_to_the_pooled_figure(self):
        model = _thermal.ThermalModel()
        model.k_sensible = _thermal.Coefficient(value=2.0, variance=0.01, samples=40)
        model.k_sensible_bins[0] = _thermal.Coefficient(
            value=0.3, variance=0.01, samples=40
        )
        # Close to setpoint: the at_setpoint bin (index 0) applies.
        self.assertAlmostEqual(model.sensible_rate_at(0.2), 0.3)

    def test_an_unconverged_bin_falls_back_to_the_pooled_figure(self):
        model = _thermal.ThermalModel()
        model.k_sensible = _thermal.Coefficient(value=2.0, variance=0.01, samples=40)
        # No bin has converged. Pooled describes full tilt and is what
        # every prior build actually used for this decision.
        self.assertAlmostEqual(model.sensible_rate_at(0.2), 2.0)

    def test_nothing_converged_returns_none(self):
        """None tells the caller to fall back to the humidity threshold —
        never the raw seed, which would look like a decision."""
        model = _thermal.ThermalModel()
        self.assertIsNone(model.sensible_rate_at(0.2))

    def test_a_room_near_setpoint_and_one_mid_pulldown_get_different_rates(self):
        """The point of the fix: two approaches, two answers, where the
        pooled figure alone would have given the same wrong one to both."""
        model = _thermal.ThermalModel()
        model.k_sensible = _thermal.Coefficient(value=2.0, variance=0.01, samples=40)
        model.k_sensible_bins[0] = _thermal.Coefficient(
            value=0.3, variance=0.01, samples=40
        )
        model.k_sensible_bins[3] = _thermal.Coefficient(
            value=2.5, variance=0.01, samples=40
        )
        near_setpoint = model.sensible_rate_at(0.2)
        mid_pulldown = model.sensible_rate_at(5.0)
        self.assertNotEqual(near_setpoint, mid_pulldown)
        self.assertAlmostEqual(near_setpoint, 0.3)
        self.assertAlmostEqual(mid_pulldown, 2.5)


_power = importlib.import_module("hvac_core.power")


class TestGridSignConvention(unittest.TestCase):
    """0.8.10, finding 11."""

    def test_positive_means_importing(self):
        self.assertEqual(
            _power.normalise_grid_import_w(500.0, _power.GRID_SIGN_IMPORTING), 500.0
        )

    def test_positive_means_exporting_flips_the_sign(self):
        self.assertEqual(
            _power.normalise_grid_import_w(500.0, _power.GRID_SIGN_EXPORTING), -500.0
        )

    def test_battery_power_is_the_house_balance(self):
        # 3120 W load, 180 W solar, 0 W grid: the battery must supply 2940 W.
        self.assertAlmostEqual(
            _power.derive_battery_w(3120.0, 180.0, 0.0), 2940.0
        )

    def test_unambiguous_evidence_implies_the_convention(self):
        """The worked example from the design: house load 3120, solar 180,
        grid reads -2840. The house must be drawing ~2940 W from somewhere;
        a negative reading under that condition implies positive = export.
        """
        self.assertEqual(
            _power.implied_sign(3120.0, 180.0, -2840.0), _power.GRID_SIGN_EXPORTING
        )

    def test_unambiguous_evidence_the_other_way(self):
        self.assertEqual(
            _power.implied_sign(3120.0, 180.0, 2840.0), _power.GRID_SIGN_IMPORTING
        )

    def test_ambiguous_evidence_offers_no_default(self):
        """Solar covers the load: house_load - solar is near zero, and the
        grid reading alone cannot resolve the convention."""
        self.assertIsNone(_power.implied_sign(500.0, 480.0, -20.0))

    def test_a_reading_of_exactly_zero_offers_no_default(self):
        """0.8.11. The house is on battery overnight, in a no_grid_import
        window: house_load - solar is large (decisive by the old logic),
        but the grid reading itself is exactly zero — no flow occurred, so
        there is no sign to have read wrong. This is the single most common
        reading this feature sees and must never be treated as evidence for
        either convention."""
        self.assertIsNone(_power.implied_sign(3120.0, 180.0, 0.0))

    def test_a_reading_near_zero_also_offers_no_default(self):
        """Float noise around zero is still no flow, not a tiny flow."""
        self.assertIsNone(_power.implied_sign(3120.0, 180.0, 0.001))
        self.assertIsNone(_power.implied_sign(3120.0, 180.0, -0.001))

    def test_a_small_but_real_reading_is_still_evidence(self):
        """Above the no-flow floor, even a modest reading counts — this is
        not the same threshold as AMBIGUOUS_BELOW_W, which governs whether
        house_load - solar is decisive, not whether the grid moved at all."""
        self.assertEqual(
            _power.implied_sign(3120.0, 180.0, 50.0), _power.GRID_SIGN_IMPORTING
        )


class TestAllowableDraw(unittest.TestCase):
    """0.8.10, finding 10."""

    def test_bounded_by_energy_when_no_discharge_ceiling_is_known(self):
        self.assertAlmostEqual(
            _power.allowable_draw_kw(4.0, 2.0, None, 0.0), 2.0
        )

    def test_bounded_by_the_plant_when_that_is_tighter(self):
        # 4 kWh over 2 h wants 2 kW, but the battery can only spare 1 kW
        # once the rest of the house's 2 kW is subtracted from a 3 kW ceiling.
        self.assertAlmostEqual(
            _power.allowable_draw_kw(4.0, 2.0, 3.0, 2.0), 1.0
        )

    def test_never_negative(self):
        self.assertEqual(_power.allowable_draw_kw(-5.0, 2.0, 3.0, 5.0), 0.0)

    def test_zero_hours_is_zero_allowance(self):
        self.assertEqual(_power.allowable_draw_kw(4.0, 0.0, None, 0.0), 0.0)


class TestCeilingBin(unittest.TestCase):
    """0.8.10, finding 10. Bins ordered at_setpoint..pulldown, increasing
    draw; the ceiling reads backwards for the most permissive fit."""

    def test_the_loosest_affordable_bin_is_chosen(self):
        draw = [0.2, 0.5, 0.9, 1.5]
        self.assertEqual(_power.ceiling_bin(1.0, draw), 2)

    def test_everything_fits(self):
        draw = [0.2, 0.5, 0.9, 1.5]
        self.assertEqual(_power.ceiling_bin(2.0, draw), 3)

    def test_nothing_fits(self):
        draw = [0.2, 0.5, 0.9, 1.5]
        self.assertIsNone(_power.ceiling_bin(0.1, draw))


class TestSolarOffset(unittest.TestCase):
    """0.8.11, finding 18. Solar is a direct offset checked first; the
    battery only binds on the shortfall."""

    def test_solar_fully_covers_the_house_with_credit_left_over(self):
        # 3 kW solar, 1 kW rest-of-house: 2 kW left over for this room,
        # nothing left for the battery to cover.
        net_house, credit = _power.solar_offset_kw(3.0, 1.0)
        self.assertAlmostEqual(net_house, 0.0)
        self.assertAlmostEqual(credit, 2.0)

    def test_solar_partially_covers_the_house_with_no_credit(self):
        # 1 kW solar, 3 kW rest-of-house: solar pays down 1 kW of it, 2 kW
        # shortfall remains for the battery, nothing left over for the room.
        net_house, credit = _power.solar_offset_kw(1.0, 3.0)
        self.assertAlmostEqual(net_house, 2.0)
        self.assertAlmostEqual(credit, 0.0)

    def test_no_solar_is_the_pre_0_8_11_behaviour(self):
        net_house, credit = _power.solar_offset_kw(0.0, 2.0)
        self.assertAlmostEqual(net_house, 2.0)
        self.assertAlmostEqual(credit, 0.0)

    def test_solar_exactly_matches_the_house(self):
        net_house, credit = _power.solar_offset_kw(2.0, 2.0)
        self.assertAlmostEqual(net_house, 0.0)
        self.assertAlmostEqual(credit, 0.0)
