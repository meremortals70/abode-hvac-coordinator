"""0.9.7: the root-and-branch review of 15 October, pure decisions.

Each class names its decision record. The Home Assistant side of the same
records is in `test_comfort_control_ha.py`.

Run from the repository root:   python3 -m unittest tests.test_comfort_control
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "custom_components" / "abode_hvac_coordinator"
if "hvac_core" not in sys.modules:
    _pkg = types.ModuleType("hvac_core")
    _pkg.__path__ = [str(_SRC)]
    sys.modules["hvac_core"] = _pkg

_hci = importlib.import_module("hvac_core.hci")
_models = importlib.import_module("hvac_core.models")
_modes = importlib.import_module("hvac_core.modes")
_regulate = importlib.import_module("hvac_core.regulate")

ComfortBand = _hci.ComfortBand
ActuatorStep = _models.ActuatorStep
ComfortMode = _models.ComfortMode
DecisionTrace = _models.DecisionTrace
Mode = _models.Mode
RoomConfig = _models.RoomConfig
RoomInputs = _models.RoomInputs
evaluate_room = _modes.evaluate_room

NOW = datetime(2026, 10, 15, 19, 0, tzinfo=UTC)
RH = 60.0
BANDS = {
    Mode.SLEEP: ComfortBand(21.0, 24.0),
    Mode.OCCUPIED: ComfortBand(24.0, 27.0),
}


def room() -> RoomConfig:
    return RoomConfig(
        room_id="living",
        name="Living",
        climate_entity_ids=("climate.living",),
        bands=BANDS,
    )


def temperature_for(index: float, rh: float = RH) -> float:
    """The dry bulb at which a still room reads `index`."""
    return _hci.dry_bulb_for_index(index, rh, still_air=True)


def inputs(index: float, **overrides: object) -> RoomInputs:
    values: dict[str, object] = {
        "now": NOW,
        "temperature_c": temperature_for(index),
        "relative_humidity": RH,
        "presence": True,
    }
    values.update(overrides)
    return RoomInputs(**values)  # type: ignore[arg-type]


#: A room whose model has learned enough to compare drying and cooling.
LEARNED = {
    "k_sensible_c_per_hour": 2.0,
    "k_latent_rh_per_hour": 6.0,
    "k_rh_cooling_per_hour": 2.0,
}


class TestCoastOnlyAtTarget(unittest.TestCase):
    """DR-098. A room the unit is working on coasts only at its target."""

    def test_a_cooling_room_inside_its_band_but_short_of_target_keeps_working(self):
        trace = evaluate_room(
            room(), inputs(26.2, predicted_to_hold=True, unit_mode="cool")
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertTrue(trace.hold_setpoint)
        self.assertTrue(
            any("coast: not yet" in note for note in trace.rejected), trace.rejected
        )

    def test_a_cooling_room_at_its_target_coasts(self):
        trace = evaluate_room(
            room(), inputs(25.6, predicted_to_hold=True, unit_mode="cool")
        )
        self.assertIs(trace.mode, Mode.COAST)
        self.assertIs(trace.actuator, ActuatorStep.FAN)

    def test_the_margin_is_measured_from_the_middle_of_the_band(self):
        middle = BANDS[Mode.OCCUPIED].midpoint
        just_inside = middle + _modes.COAST_AT_TARGET_HCI - 0.05
        just_outside = middle + _modes.COAST_AT_TARGET_HCI + 0.05
        held = evaluate_room(
            room(), inputs(just_inside, predicted_to_hold=True, unit_mode="cool")
        )
        kept = evaluate_room(
            room(), inputs(just_outside, predicted_to_hold=True, unit_mode="cool")
        )
        self.assertIs(held.mode, Mode.COAST)
        self.assertIs(kept.mode, Mode.OCCUPIED)

    def test_a_room_whose_unit_is_not_working_coasts_anywhere_in_band(self):
        trace = evaluate_room(
            room(), inputs(26.2, predicted_to_hold=True, unit_mode="fan_only")
        )
        self.assertIs(trace.mode, Mode.COAST)

    def test_a_heating_room_short_of_its_target_keeps_heating(self):
        trace = evaluate_room(
            room(), inputs(24.6, predicted_to_hold=True, unit_mode="heat")
        )
        self.assertIs(trace.mode, Mode.OCCUPIED)

    def test_sleep_keeps_its_own_band(self):
        trace = evaluate_room(
            room(),
            inputs(
                23.2,
                predicted_to_hold=True,
                unit_mode="cool",
                comfort_mode=ComfortMode.SLEEP,
            ),
        )
        self.assertIs(trace.mode, Mode.SLEEP)
        self.assertEqual(trace.band_low, 21.0)


class TestRouteHeld(unittest.TestCase):
    """DR-090. Drying, cooling or the fan, once chosen, is kept 20 minutes."""

    def _above_band(self, **overrides):
        return evaluate_room(room(), inputs(28.0, **{**LEARNED, **overrides}))

    def test_a_drying_unit_keeps_drying_inside_the_hold(self):
        trace = self._above_band(unit_mode="dry", unit_mode_minutes=5.0)
        self.assertIs(trace.actuator, ActuatorStep.DRY)
        self.assertTrue(any("dry: kept" in note for note in trace.reasons))

    def test_a_cooling_unit_keeps_cooling_inside_the_hold(self):
        # The learned rates say dry; the hold says cool.
        trace = self._above_band(
            unit_mode="cool",
            unit_mode_minutes=5.0,
            k_sensible_c_per_hour=0.2,
        )
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertTrue(
            any("cooling was chosen" in note for note in trace.rejected)
        )

    def test_after_the_hold_the_rates_decide_again(self):
        trace = self._above_band(
            unit_mode="cool",
            unit_mode_minutes=_modes.ROUTE_HOLD.total_seconds() / 60.0,
            k_sensible_c_per_hour=0.2,
        )
        self.assertIs(trace.actuator, ActuatorStep.DRY)

    def test_an_unknown_time_in_mode_holds_nothing(self):
        trace = self._above_band(
            unit_mode="cool", unit_mode_minutes=None, k_sensible_c_per_hour=0.2
        )
        self.assertIs(trace.actuator, ActuatorStep.DRY)

    def test_a_coast_keeps_the_fan_inside_the_hold(self):
        trace = DecisionTrace(room_id="living", at=NOW, mode=Mode.COAST)
        step = _modes.coast_step(
            inputs(25.6, relative_humidity=80.0, unit_mode="fan_only", unit_mode_minutes=1.0),
            trace,
            BANDS[Mode.OCCUPIED],
            25.6,
        )
        self.assertIs(step, ActuatorStep.FAN)

    def test_a_coast_keeps_drying_inside_the_hold(self):
        trace = DecisionTrace(room_id="living", at=NOW, mode=Mode.COAST)
        step = _modes.coast_step(
            inputs(25.6, relative_humidity=40.0, unit_mode="dry", unit_mode_minutes=1.0),
            trace,
            BANDS[Mode.OCCUPIED],
            25.6,
        )
        self.assertIs(step, ActuatorStep.DRY)


class TestInBandModeFollowsItsReason(unittest.TestCase):
    """DR-091. Inside its band, a unit does not stay in a mode whose reason
    has passed."""

    def test_a_heating_unit_at_or_above_the_middle_goes_to_its_fan(self):
        trace = evaluate_room(room(), inputs(25.6, unit_mode="heat"))
        self.assertIs(trace.actuator, ActuatorStep.FAN)

    def test_a_heating_unit_below_the_middle_keeps_heating(self):
        trace = evaluate_room(room(), inputs(24.6, unit_mode="heat"))
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertTrue(trace.hold_setpoint)

    def test_a_heating_unit_with_no_fan_mode_is_turned_off(self):
        trace = evaluate_room(
            room(), inputs(25.6, unit_mode="heat", can_fan_only=False)
        )
        self.assertIs(trace.actuator, ActuatorStep.OFF)

    def test_a_drying_unit_whose_reason_has_passed_goes_back_to_cooling(self):
        trace = evaluate_room(
            room(), inputs(25.6, relative_humidity=51.0, unit_mode="dry", unit_mode_minutes=60.0)
        )
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)
        self.assertIsNone(trace.demand)

    def test_a_drying_unit_inside_the_hold_keeps_drying(self):
        trace = evaluate_room(
            room(), inputs(25.6, relative_humidity=51.0, unit_mode="dry", unit_mode_minutes=5.0)
        )
        self.assertIs(trace.actuator, ActuatorStep.NONE)

    def test_a_drying_unit_still_called_for_keeps_drying(self):
        trace = evaluate_room(
            room(), inputs(25.6, relative_humidity=75.0, unit_mode="dry", unit_mode_minutes=60.0)
        )
        self.assertIs(trace.actuator, ActuatorStep.NONE)

    def test_a_cooling_unit_is_left_cooling(self):
        trace = evaluate_room(room(), inputs(25.6, unit_mode="cool"))
        self.assertIs(trace.actuator, ActuatorStep.NONE)
        self.assertTrue(trace.hold_setpoint)

    def test_a_waking_room_below_its_band_is_not_left_cooling(self):
        trace = evaluate_room(room(), inputs(23.0, waking=True, unit_mode="cool"))
        self.assertIs(trace.actuator, ActuatorStep.FAN)
        self.assertTrue(
            any("not heating a room on its way out of Sleep" in n for n in trace.rejected)
        )

    def test_a_waking_room_whose_unit_is_off_is_left_off(self):
        trace = evaluate_room(room(), inputs(23.0, waking=True, unit_mode="off"))
        self.assertIs(trace.actuator, ActuatorStep.NONE)


class TestNoDryAfterHeat(unittest.TestCase):
    """DR-093. A coast after heating does not dry: the coil is dry."""

    def test_a_humid_room_coasting_after_heat_goes_to_its_fan(self):
        trace = DecisionTrace(room_id="living", at=NOW, mode=Mode.COAST)
        step = _modes.coast_step(
            inputs(25.6, relative_humidity=80.0, unit_mode="heat"),
            trace,
            BANDS[Mode.OCCUPIED],
            25.6,
        )
        self.assertIs(step, ActuatorStep.FAN)
        self.assertIn("dry: the unit has been heating, its coil is dry", trace.rejected)

    def test_a_humid_room_coasting_after_cooling_still_dries(self):
        trace = DecisionTrace(room_id="living", at=NOW, mode=Mode.COAST)
        step = _modes.coast_step(
            inputs(25.6, relative_humidity=80.0, unit_mode="cool"),
            trace,
            BANDS[Mode.OCCUPIED],
            25.6,
        )
        self.assertIs(step, ActuatorStep.DRY)


class TestFallbackDirection(unittest.TestCase):
    """DR-094. The fallback loop changes direction only well past target."""

    def test_the_first_direction_is_the_room_against_its_target(self):
        state = _regulate.RegulatorState()
        self.assertEqual(_regulate.loop_direction(state, 22.0, 23.0), "cool")
        state = _regulate.RegulatorState()
        self.assertEqual(_regulate.loop_direction(state, 22.0, 21.0), "heat")
        state = _regulate.RegulatorState()
        self.assertIsNone(_regulate.loop_direction(state, 22.0, 22.0))

    def test_cooling_holds_until_the_room_is_well_below_target(self):
        state = _regulate.RegulatorState(loop_direction="cool")
        margin = _regulate.FALLBACK_DIRECTION_MARGIN_C
        self.assertEqual(
            _regulate.loop_direction(state, 22.0, 22.0 - margin + 0.05), "cool"
        )
        self.assertEqual(
            _regulate.loop_direction(state, 22.0, 22.0 - margin - 0.05), "heat"
        )

    def test_heating_holds_until_the_room_is_well_above_target(self):
        state = _regulate.RegulatorState(loop_direction="heat")
        margin = _regulate.FALLBACK_DIRECTION_MARGIN_C
        self.assertEqual(
            _regulate.loop_direction(state, 22.0, 22.0 + margin - 0.05), "heat"
        )
        self.assertEqual(
            _regulate.loop_direction(state, 22.0, 22.0 + margin + 0.05), "cool"
        )

    def test_no_reading_keeps_the_last_direction(self):
        state = _regulate.RegulatorState(loop_direction="cool")
        self.assertEqual(_regulate.loop_direction(state, None, 22.0), "cool")

    def test_a_room_crossing_its_target_keeps_one_setpoint(self):
        """Simulated night 1: 19 and 21 C forty times. Now one setpoint."""
        state = _regulate.RegulatorState(trim_c=-1.3)
        setpoints = set()
        for room_c in (20.62, 20.58, 20.63, 20.57, 20.61, 20.59):
            direction = _regulate.loop_direction(state, 20.6, room_c) or "cool"
            setpoints.add(_regulate.commanded_setpoint(state, 20.6, direction))
        self.assertEqual(setpoints, {19.3})


class TestSensorOffset(unittest.TestCase):
    """DR-095. The unit's own sensor against the room's, measured."""

    def test_the_first_reading_is_taken_as_it_stands(self):
        state = _regulate.RegulatorState()
        _regulate.track_sensor_offset(state, unit_c=20.0, room_c=23.0, now=NOW)
        self.assertEqual(state.sensor_offset_c, -3.0)

    def test_later_readings_are_filtered(self):
        state = _regulate.RegulatorState()
        _regulate.track_sensor_offset(state, unit_c=20.0, room_c=23.0, now=NOW)
        _regulate.track_sensor_offset(
            state, unit_c=21.0, room_c=23.0, now=NOW + timedelta(minutes=3)
        )
        self.assertAlmostEqual(state.sensor_offset_c, -2.9, places=2)

    def test_a_reading_after_the_filter_time_is_taken_in_full(self):
        state = _regulate.RegulatorState()
        _regulate.track_sensor_offset(state, unit_c=20.0, room_c=23.0, now=NOW)
        _regulate.track_sensor_offset(
            state, unit_c=22.0, room_c=23.0, now=NOW + timedelta(hours=1)
        )
        self.assertEqual(state.sensor_offset_c, -1.0)

    def test_an_implausible_difference_is_ignored(self):
        state = _regulate.RegulatorState()
        _regulate.track_sensor_offset(state, unit_c=15.0, room_c=23.0, now=NOW)
        self.assertIsNone(state.sensor_offset_c)

    def test_no_unit_reading_pauses_the_filter(self):
        state = _regulate.RegulatorState()
        _regulate.track_sensor_offset(state, unit_c=20.0, room_c=23.0, now=NOW)
        _regulate.track_sensor_offset(state, unit_c=None, room_c=23.0, now=NOW)
        self.assertIsNone(state.sensor_offset_at)
        self.assertEqual(state.sensor_offset_c, -3.0)
        # The next reading after a pause restarts the interval, not a jump.
        _regulate.track_sensor_offset(
            state, unit_c=22.0, room_c=23.0, now=NOW + timedelta(hours=5)
        )
        self.assertEqual(state.sensor_offset_c, -3.0)

    def test_the_fallback_setpoint_allows_for_it(self):
        state = _regulate.RegulatorState(trim_c=-0.5, sensor_offset_c=-3.0)
        self.assertEqual(_regulate.commanded_setpoint(state, 23.0), 19.5)

    def test_no_measurement_changes_nothing(self):
        state = _regulate.RegulatorState(trim_c=-0.5)
        self.assertEqual(_regulate.commanded_setpoint(state, 23.0), 22.5)


class TestBandRampsAcrossTheHour(unittest.TestCase):
    """DR-084. The band ramps from the one the room changed from, all hour."""

    def _at(self, minutes: float):
        return evaluate_room(
            room(),
            inputs(
                23.0,
                now=NOW + timedelta(minutes=minutes),
                previous_mode=Mode.SLEEP,
                mode_changed_at=NOW,
            ),
        )

    def test_the_band_starts_at_the_old_band(self):
        trace = self._at(0)
        self.assertAlmostEqual(trace.band_low, 21.0)

    def test_half_way_it_is_half_way(self):
        trace = self._at(30)
        self.assertAlmostEqual(trace.band_low, 22.5)
        self.assertTrue(any(r.startswith("band ramping") for r in trace.reasons))

    def test_near_the_end_it_is_still_ramping(self):
        trace = self._at(55)
        self.assertLess(trace.band_low, 24.0)

    def test_after_the_hour_it_is_the_new_band(self):
        trace = self._at(61)
        self.assertEqual(trace.band_low, 24.0)

    def test_a_coast_ramps_on_the_band_it_displaced(self):
        trace = evaluate_room(
            room(),
            inputs(
                22.5,
                now=NOW + timedelta(minutes=30),
                previous_mode=Mode.SLEEP,
                mode_changed_at=NOW,
                predicted_to_hold=True,
            ),
        )
        self.assertIs(trace.mode, Mode.COAST)
        self.assertAlmostEqual(trace.band_low, 22.5)


    def test_a_coast_is_not_a_band_change(self):
        """A coast keeps the band of the mode it displaced: no ramp."""
        trace = evaluate_room(
            room(),
            inputs(
                25.6,
                now=NOW + timedelta(minutes=10),
                previous_mode=Mode.OCCUPIED,
                mode_changed_at=NOW,
                predicted_to_hold=True,
            ),
        )
        self.assertIs(trace.mode, Mode.COAST)
        self.assertFalse(
            any(r.startswith("band ramping") for r in trace.reasons), trace.reasons
        )


class TestNoHeatOnWaking(unittest.TestCase):
    """DR-085. A room coming out of Sleep is not heated up to its day band."""

    def test_a_waking_room_below_its_band_is_not_heated(self):
        trace = evaluate_room(room(), inputs(22.0, waking=True, unit_mode="off"))
        self.assertEqual(trace.demand, "heat")
        self.assertIsNot(trace.actuator, ActuatorStep.COMPRESSOR)

    def test_a_room_not_waking_is_heated(self):
        trace = evaluate_room(room(), inputs(22.0, unit_mode="off"))
        self.assertIs(trace.actuator, ActuatorStep.COMPRESSOR)


class TestRateFromTheTrend(unittest.TestCase):
    """DR-086. The derivative is the trend over 15 minutes, not a step."""

    def test_no_rate_until_five_minutes_are_held(self):
        state = _regulate.RegulatorState()
        for second in range(0, 240, 30):
            _regulate.track_room_rate(state, 24.0 + second / 3600.0, NOW + timedelta(seconds=second))
        self.assertEqual(state.room_rate_c_per_hour, 0.0)

    def test_a_steady_fall_reads_as_its_slope(self):
        state = _regulate.RegulatorState()
        for second in range(0, 900, 30):
            _regulate.track_room_rate(
                state, 24.0 - second / 3600.0, NOW + timedelta(seconds=second)
            )
        self.assertAlmostEqual(state.room_rate_c_per_hour, -1.0, places=3)

    def test_a_sensor_reporting_in_steps_gives_the_trend_not_a_spike(self):
        """An Eve Room reports 0.2 C steps. The trend is what the room does."""
        state = _regulate.RegulatorState()
        rates = []
        for second in range(0, 1800, 30):
            true = 24.0 - 0.6 * second / 3600.0
            stepped = round(true / 0.2) * 0.2
            _regulate.track_room_rate(state, stepped, NOW + timedelta(seconds=second))
            if second >= 600:
                rates.append(state.room_rate_c_per_hour)
        # A one-interval difference of a 0.2 C step is 24 C per hour. The
        # trend stays within a factor of two of the true 0.6, never past it
        # in the wrong direction.
        self.assertTrue(all(-1.2 <= rate <= 0.0 for rate in rates), rates)

    def test_old_readings_leave_the_window(self):
        state = _regulate.RegulatorState()
        for second in range(0, 3600, 30):
            _regulate.track_room_rate(state, 24.0, NOW + timedelta(seconds=second))
        self.assertLessEqual(
            state.samples[-1][0] - state.samples[0][0], _regulate.RATE_WINDOW
        )

    def test_the_rate_is_capped(self):
        state = _regulate.RegulatorState()
        for second in range(0, 900, 30):
            _regulate.track_room_rate(
                state, 24.0 - 40.0 * second / 3600.0, NOW + timedelta(seconds=second)
            )
        self.assertEqual(
            state.room_rate_c_per_hour, -_regulate.RATE_LIMIT_C_PER_HOUR
        )


if __name__ == "__main__":
    unittest.main()
