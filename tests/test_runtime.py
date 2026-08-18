"""The runtime half: coordinator, entities, and the options flow.

    python3 -m unittest tests.test_runtime

Same approach as the setup-flow tests: thin stubs for the Home Assistant APIs
these modules touch, then drive them. What is being tested is the component's
own logic — when it schedules, what it publishes, what it writes — not the
stubs.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
import unittest
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ha_stubs

_ha_stubs.install()

PACKAGE = "abode_power_tariffs_runtime"
ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "abode_power_tariffs"
MODULES = (
    "const",
    "plan",
    "validate",
    "intervals",
    "allowance",
    "strip",
    "serialise",
    "coordinator",
    "entity",
    "config_flow",
)


def _load() -> types.ModuleType:
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package
    for name in MODULES:
        spec = importlib.util.spec_from_file_location(
            f"{PACKAGE}.{name}", ROOT / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{PACKAGE}.{name}"] = module
        spec.loader.exec_module(module)
        setattr(package, name, module)
    return package


PKG = _load()
CONST = PKG.const
Plan = PKG.plan.Plan
Rate = PKG.plan.Rate
ExportRate = PKG.plan.ExportRate
DayPattern = PKG.plan.DayPattern
Period = PKG.plan.Period
TariffCoordinator = PKG.coordinator.TariffCoordinator
OptionsFlow = PKG.config_flow.AbodePowerTariffsOptionsFlow

BRISBANE = ZoneInfo("Australia/Brisbane")
ALL_DAYS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun", "holiday"})
FORM = _ha_stubs.FlowResultType.FORM
MENU = _ha_stubs.FlowResultType.MENU
CREATE = _ha_stubs.FlowResultType.CREATE_ENTRY


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def at(hour: int, minute: int = 0, day: int = 14) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=BRISBANE)


def sample_options() -> dict[str, Any]:
    return {
        CONST.CONF_RATES: [
            {
                CONST.CONF_NAME: "Every day Off Peak",
                CONST.CONF_IMPORT_CENTS: 19.8,
                CONST.CONF_CONSTRAINTS: ["grid_charge_battery"],
                CONST.CONF_COASTING_PERMITTED: False,
            },
            {
                CONST.CONF_NAME: "Every day Peak",
                CONST.CONF_IMPORT_CENTS: 56.88,
                CONST.CONF_CONSTRAINTS: ["no_grid_import"],
            },
        ],
        CONST.CONF_DAY_PATTERNS: [
            {
                CONST.CONF_NAME: "Every day",
                CONST.CONF_DAYS: list(CONST.ALL_DAY_TOKENS),
                CONST.CONF_PERIODS: [
                    {
                        CONST.CONF_START: "00:00",
                        CONST.CONF_END: "16:00",
                        CONST.CONF_RATE: "Every day Off Peak",
                    },
                    {
                        CONST.CONF_START: "16:00",
                        CONST.CONF_END: "24:00",
                        CONST.CONF_RATE: "Every day Peak",
                    },
                ],
                CONST.CONF_EXPORT_SAME_ALL_DAY: True,
                CONST.CONF_EXPORT_FLAT_CENTS: 2.7,
            }
        ],
        CONST.CONF_SUPPLY_CHARGE_CENTS: 116.6,
        CONST.CONF_PRICES_INCLUDE_GST: True,
        CONST.CONF_GST_PERCENT: 10.0,
    }


def a_coordinator(options: dict[str, Any] | None = None) -> Any:
    hass = _ha_stubs.FakeHass()
    data = options or sample_options()
    plan = Plan.from_dict({**data, CONST.CONF_NAME: "Test"})
    return TariffCoordinator(hass, "entry1", plan, data)


class CoordinatorCase(unittest.TestCase):
    def setUp(self) -> None:
        _ha_stubs.SCHEDULED.__init__()  # type: ignore[misc]
        PKG.coordinator.dt_util.NOW = at(10)

    def tearDown(self) -> None:
        PKG.coordinator.dt_util.NOW = None


class TestResolution(CoordinatorCase):
    def test_the_rate_in_force(self) -> None:
        coordinator = a_coordinator()
        coordinator.async_refresh()
        assert coordinator.state.effective_rate is not None
        self.assertEqual(coordinator.state.effective_rate.name, "Every day Off Peak")

    def test_the_rate_changes_with_the_clock(self) -> None:
        coordinator = a_coordinator()
        PKG.coordinator.dt_util.NOW = at(17)
        coordinator.async_refresh()
        assert coordinator.state.effective_rate is not None
        self.assertEqual(coordinator.state.effective_rate.name, "Every day Peak")

    def test_export_price_comes_from_the_timetable(self) -> None:
        coordinator = a_coordinator()
        coordinator.async_refresh()
        self.assertAlmostEqual(coordinator.export_price_now(), 0.027)

    def test_a_trace_is_recorded(self) -> None:
        coordinator = a_coordinator()
        coordinator.async_refresh()
        self.assertTrue(coordinator.state.trace)


class TestScheduling(CoordinatorCase):
    def test_the_next_boundary_is_scheduled(self) -> None:
        coordinator = a_coordinator()
        coordinator.async_refresh()
        self.assertEqual(len(_ha_stubs.SCHEDULED.points), 1)
        moment, _ = _ha_stubs.SCHEDULED.points[0]
        self.assertEqual(moment.astimezone(BRISBANE).hour, 16)

    def test_the_previous_schedule_is_cancelled_before_the_next(self) -> None:
        coordinator = a_coordinator()
        coordinator.async_refresh()
        coordinator.async_refresh()
        self.assertEqual(_ha_stubs.SCHEDULED.cancelled, 1)
        self.assertEqual(len(_ha_stubs.SCHEDULED.points), 2)

    def test_midnight_reset_is_registered_on_start(self) -> None:
        coordinator = a_coordinator()
        run(coordinator.async_start())
        registered = [t for t in _ha_stubs.SCHEDULED.time_changes if t.get("hour") == 0]
        self.assertEqual(len(registered), 1)

    def test_midnight_clears_the_allowance(self) -> None:
        coordinator = a_coordinator()
        coordinator.state.allowance_used_kwh = 12.0
        coordinator._handle_midnight(at(0, 0, day=15))
        self.assertEqual(coordinator.state.allowance_used_kwh, 0.0)

    def test_entities_are_told(self) -> None:
        coordinator = a_coordinator()
        coordinator.async_refresh()
        self.assertIn(f"{CONST.SIGNAL_UPDATE}_entry1", _ha_stubs.SCHEDULED.dispatched)

    def test_shutdown_releases_everything(self) -> None:
        coordinator = a_coordinator()
        run(coordinator.async_start())
        coordinator.async_shutdown()
        self.assertIsNone(coordinator._boundary_unsubscribe)


class TestExportScheduling(CoordinatorCase):
    """The import rate and the feed-in price are scheduled and reported apart."""

    EXPORT_PERIODS = [
        {CONST.CONF_START: "00:00", CONST.CONF_END: "09:00", CONST.CONF_RATE: "Night"},
        {CONST.CONF_START: "09:00", CONST.CONF_END: "24:00", CONST.CONF_RATE: "Day"},
    ]
    EXPORT_RATES = [
        {CONST.CONF_NAME: "Night", CONST.CONF_EXPORT_CENTS: 0.0},
        {CONST.CONF_NAME: "Day", CONST.CONF_EXPORT_CENTS: 5.0},
    ]

    def _with_timed_export(self, *, flat_import: bool) -> Any:
        data = sample_options()
        pattern = data[CONST.CONF_DAY_PATTERNS][0]
        if flat_import:
            pattern[CONST.CONF_PERIODS] = [
                {
                    CONST.CONF_START: "00:00",
                    CONST.CONF_END: "24:00",
                    CONST.CONF_RATE: "Every day Off Peak",
                },
            ]
        pattern[CONST.CONF_EXPORT_SAME_ALL_DAY] = False
        pattern[CONST.CONF_EXPORT_PERIODS] = list(self.EXPORT_PERIODS)
        data[CONST.CONF_EXPORT_RATES] = list(self.EXPORT_RATES)
        return a_coordinator(data)

    def test_it_wakes_at_the_feed_in_change_not_at_midnight(self) -> None:
        """The bug: a flat import rate meant nothing was scheduled all day."""
        PKG.coordinator.dt_util.NOW = at(8)
        coordinator = self._with_timed_export(flat_import=True)
        coordinator.async_refresh()
        self.assertEqual(len(_ha_stubs.SCHEDULED.points), 1)
        moment, _ = _ha_stubs.SCHEDULED.points[0]
        self.assertEqual(moment.astimezone(BRISBANE).hour, 9)

    def test_the_feed_in_change_is_reported(self) -> None:
        PKG.coordinator.dt_util.NOW = at(8)
        coordinator = self._with_timed_export(flat_import=True)
        coordinator.async_refresh()
        nxt = coordinator.state.next_export_change
        assert nxt is not None
        self.assertEqual(nxt.astimezone(BRISBANE).hour, 9)

    def test_the_import_change_is_reported_separately(self) -> None:
        PKG.coordinator.dt_util.NOW = at(8)
        coordinator = self._with_timed_export(flat_import=True)
        coordinator.async_refresh()
        nxt = coordinator.state.next_change
        assert nxt is not None
        self.assertEqual(nxt.astimezone(BRISBANE).date(), date(2026, 8, 15))

    def test_the_earlier_of_the_two_is_what_is_scheduled(self) -> None:
        """Import changes at 16:00, feed-in at 09:00. Wake at 09:00."""
        PKG.coordinator.dt_util.NOW = at(8)
        coordinator = self._with_timed_export(flat_import=False)
        coordinator.async_refresh()
        assert coordinator.state.next_change is not None
        assert coordinator.state.next_export_change is not None
        self.assertEqual(coordinator.state.next_change.astimezone(BRISBANE).hour, 16)
        self.assertEqual(
            coordinator.state.next_export_change.astimezone(BRISBANE).hour, 9
        )
        moment, _ = _ha_stubs.SCHEDULED.points[0]
        self.assertEqual(moment.astimezone(BRISBANE).hour, 9)

    def test_a_flat_feed_in_price_reports_nothing_and_changes_nothing(self) -> None:
        coordinator = a_coordinator()
        coordinator.async_refresh()
        self.assertIsNone(coordinator.state.next_export_change)
        moment, _ = _ha_stubs.SCHEDULED.points[0]
        self.assertEqual(moment.astimezone(BRISBANE).hour, 16)


class TestHolidays(CoordinatorCase):
    def _with_sensor(self) -> Any:
        options = sample_options()
        options[CONST.CONF_HOLIDAY_SENSOR] = "binary_sensor.workday"
        return a_coordinator(options)

    def test_no_sensor_means_no_holiday(self) -> None:
        self.assertFalse(a_coordinator().is_holiday(date(2026, 8, 14)))

    def test_workday_off_means_holiday(self) -> None:
        coordinator = self._with_sensor()
        coordinator.hass.states.set("binary_sensor.workday", "off")
        self.assertTrue(coordinator.is_holiday(PKG.coordinator.dt_util.NOW.date()))

    def test_workday_on_means_ordinary(self) -> None:
        coordinator = self._with_sensor()
        coordinator.hass.states.set("binary_sensor.workday", "on")
        self.assertFalse(coordinator.is_holiday(PKG.coordinator.dt_util.NOW.date()))

    def test_an_unavailable_sensor_falls_back_quietly(self) -> None:
        coordinator = self._with_sensor()
        coordinator.hass.states.set("binary_sensor.workday", "unavailable")
        self.assertFalse(coordinator.is_holiday(PKG.coordinator.dt_util.NOW.date()))
        self.assertTrue(coordinator._holiday_warned)
        # Second read must not warn again.
        coordinator.is_holiday(PKG.coordinator.dt_util.NOW.date())
        self.assertTrue(coordinator._holiday_warned)

    def test_only_today_can_be_answered(self) -> None:
        coordinator = self._with_sensor()
        coordinator.hass.states.set("binary_sensor.workday", "off")
        self.assertFalse(coordinator.is_holiday(date(2030, 1, 1)))


class TestAllowanceAccounting(CoordinatorCase):
    def _capped(self) -> Any:
        options = sample_options()
        options[CONST.CONF_RATES][0][CONST.CONF_DAILY_ALLOWANCE_KWH] = 24.0
        options[CONST.CONF_RATES][0][CONST.CONF_FALLBACK_RATE] = "Every day Peak"
        options[CONST.CONF_IMPORT_ENERGY_SENSOR] = "sensor.grid_import"
        options[CONST.CONF_COUNT_ALLOWANCE] = True
        coordinator = a_coordinator(options)
        coordinator.hass.states.set("sensor.grid_import", "100.0")
        coordinator._seed_energy_total()
        return coordinator

    def test_consumption_counts_against_the_allowance(self) -> None:
        coordinator = self._capped()
        coordinator.async_refresh()
        coordinator.hass.states.set("sensor.grid_import", "110.0")
        coordinator._accumulate_energy("sensor.grid_import")
        self.assertAlmostEqual(coordinator.state.allowance_used_kwh, 10.0)

    def test_past_the_allowance_the_fallback_rate_applies(self) -> None:
        coordinator = self._capped()
        coordinator.async_refresh()
        coordinator.hass.states.set("sensor.grid_import", "130.0")
        coordinator._accumulate_energy("sensor.grid_import")
        coordinator.async_refresh()
        assert coordinator.state.effective_rate is not None
        self.assertEqual(coordinator.state.effective_rate.name, "Every day Peak")
        self.assertTrue(coordinator.state.allowance_exhausted)

    def test_a_meter_reset_does_not_count(self) -> None:
        coordinator = self._capped()
        coordinator.async_refresh()
        coordinator.hass.states.set("sensor.grid_import", "5.0")
        coordinator._accumulate_energy("sensor.grid_import")
        self.assertEqual(coordinator.state.allowance_used_kwh, 0.0)

    def test_an_unavailable_meter_warns_once(self) -> None:
        coordinator = self._capped()
        coordinator.hass.states.set("sensor.grid_import", "unavailable")
        coordinator._accumulate_energy("sensor.grid_import")
        self.assertTrue(coordinator._energy_warned)


class TestForwardSeriesIsHeld(CoordinatorCase):
    """The price sensors ask for the forecast every time they write state.

    With an energy meter attached that is several times a minute, and the
    series only actually changes on the resolution grid.
    """

    def test_the_same_slot_reuses_the_series(self) -> None:
        coordinator = a_coordinator()
        self.assertIs(
            coordinator.forward_intervals(24, 30), coordinator.forward_intervals(24, 30)
        )

    def test_a_new_slot_rebuilds_it(self) -> None:
        coordinator = a_coordinator()
        first = coordinator.forward_intervals(24, 30)
        PKG.coordinator.dt_util.NOW = at(10, 30)
        self.assertIsNot(first, coordinator.forward_intervals(24, 30))

    def test_a_moment_later_in_the_same_slot_does_not(self) -> None:
        coordinator = a_coordinator()
        first = coordinator.forward_intervals(24, 30)
        PKG.coordinator.dt_util.NOW = at(10, 29)
        self.assertIs(first, coordinator.forward_intervals(24, 30))

    def test_a_different_request_is_not_confused_with_it(self) -> None:
        coordinator = a_coordinator()
        day = coordinator.forward_intervals(24, 30)
        hour = coordinator.forward_intervals(1, 60)
        self.assertIsNot(day, hour)
        self.assertEqual(len(hour), 1)

    def test_a_new_plan_rebuilds_it(self) -> None:
        coordinator = a_coordinator()
        first = coordinator.forward_intervals(24, 30)
        coordinator.apply_plan(coordinator.plan, coordinator.options)
        self.assertIsNot(first, coordinator.forward_intervals(24, 30))


class TestTheTraceHoldsStill(CoordinatorCase):
    """The trace is published as a sensor attribute.

    A live figure in it rewrites the attribute, and the entity state with it,
    on every meter reading. How much allowance is left has its own sensor.
    """

    def _capped(self) -> Any:
        data = sample_options()
        data[CONST.CONF_RATES][0][CONST.CONF_DAILY_ALLOWANCE_KWH] = 24.0
        data[CONST.CONF_RATES][0][CONST.CONF_FALLBACK_RATE] = "Every day Peak"
        data[CONST.CONF_IMPORT_ENERGY_SENSOR] = "sensor.grid_import"
        data[CONST.CONF_COUNT_ALLOWANCE] = True
        coordinator = a_coordinator(data)
        coordinator.hass.states.set("sensor.grid_import", "100.0")
        coordinator._seed_energy_total()
        return coordinator

    def _consume(self, coordinator: Any, total: str) -> None:
        coordinator.hass.states.set("sensor.grid_import", total)
        coordinator._accumulate_energy("sensor.grid_import")
        coordinator.async_refresh()

    def test_it_does_not_move_as_the_meter_ticks(self) -> None:
        coordinator = self._capped()
        coordinator.async_refresh()
        before = coordinator.state.trace
        self._consume(coordinator, "105.0")
        self.assertEqual(coordinator.state.trace, before)
        self._consume(coordinator, "110.0")
        self.assertEqual(coordinator.state.trace, before)

    def test_within_the_allowance_it_says_so_and_no_more(self) -> None:
        coordinator = self._capped()
        coordinator.async_refresh()
        self.assertEqual(
            coordinator.state.trace, ("day set Every day", "within the allowance")
        )

    def test_it_still_reports_the_allowance_being_spent(self) -> None:
        coordinator = self._capped()
        coordinator.async_refresh()
        self._consume(coordinator, "130.0")
        self.assertEqual(
            coordinator.state.trace,
            ("day set Every day", "allowance spent", "priced at Every day Peak"),
        )

    def test_the_remaining_figure_is_still_published_by_its_own_sensor(self) -> None:
        coordinator = self._capped()
        coordinator.async_refresh()
        self._consume(coordinator, "110.0")
        self.assertAlmostEqual(coordinator.state.allowance_remaining_kwh, 14.0)


class TestTheOneWrite(CoordinatorCase):
    def _linked(self, options_extra: dict[str, Any] | None = None) -> Any:
        options = sample_options()
        options[CONST.CONF_TARIFF_SELECTS] = ["select.meter_tariff"]
        options.update(options_extra or {})
        return a_coordinator(options)

    def test_nothing_is_written_when_no_select_is_nominated(self) -> None:
        coordinator = a_coordinator()
        coordinator.async_refresh()
        self.assertEqual(coordinator.hass.services.calls, [])

    def test_the_select_is_set_to_the_rate_in_force(self) -> None:
        coordinator = self._linked()
        coordinator.hass.states.set(
            "select.meter_tariff",
            "Every day Peak",
            options=["Every day Off Peak", "Every day Peak"],
        )
        coordinator.async_refresh()
        self.assertEqual(
            coordinator.hass.services.calls,
            [
                (
                    "select",
                    "select_option",
                    {
                        "entity_id": "select.meter_tariff",
                        "option": "Every day Off Peak",
                    },
                )
            ],
        )

    def test_it_is_not_written_twice_for_the_same_rate(self) -> None:
        coordinator = self._linked()
        coordinator.hass.states.set(
            "select.meter_tariff",
            "Every day Peak",
            options=["Every day Off Peak", "Every day Peak"],
        )
        coordinator.async_refresh()
        coordinator.async_refresh()
        self.assertEqual(len(coordinator.hass.services.calls), 1)

    def test_a_missing_option_is_refused_not_forced(self) -> None:
        coordinator = self._linked()
        coordinator.hass.states.set(
            "select.meter_tariff", "peak", options=["peak", "offpeak"]
        )
        coordinator.async_refresh()
        self.assertEqual(coordinator.hass.services.calls, [])

    def test_a_missing_select_is_survived(self) -> None:
        coordinator = self._linked()
        coordinator.async_refresh()
        self.assertEqual(coordinator.hass.services.calls, [])


class TestExpiredPlan(CoordinatorCase):
    def test_an_expired_plan_is_held_not_dropped(self) -> None:
        options = sample_options()
        options[CONST.CONF_VALID_TO] = "2026-01-01"
        coordinator = a_coordinator(options)
        coordinator.async_refresh()
        self.assertTrue(coordinator.state.plan_expired)
        self.assertIsNotNone(coordinator.state.effective_rate)


class TestForwardIntervals(CoordinatorCase):
    def test_the_series_covers_the_horizon(self) -> None:
        coordinator = a_coordinator()
        series = coordinator.forward_intervals(6, 30)
        self.assertEqual(len(series), 12)
        self.assertEqual(series[0].rate, "Every day Off Peak")

    def test_the_service_shape(self) -> None:
        coordinator = a_coordinator()
        payload = coordinator.forward_intervals(1, 60)[0].as_dict()
        self.assertEqual(payload["duration"], 60)
        self.assertIn("per_kwh", payload)
        self.assertFalse(payload["forecast"])


class TestEntityBase(CoordinatorCase):
    def test_available_only_when_a_period_resolves(self) -> None:
        coordinator = a_coordinator()
        entity = PKG.entity.TariffEntity(coordinator, "import_price")
        self.assertFalse(entity.available)
        coordinator.async_refresh()
        self.assertTrue(entity.available)

    def test_unique_id_and_device(self) -> None:
        coordinator = a_coordinator()
        entity = PKG.entity.TariffEntity(coordinator, "import_price")
        self.assertEqual(entity._attr_unique_id, "entry1_import_price")
        self.assertEqual(coordinator.device_identifier, (CONST.DOMAIN, "entry1"))

    def test_names_come_from_translations(self) -> None:
        coordinator = a_coordinator()
        entity = PKG.entity.TariffEntity(coordinator, "import_price")
        self.assertTrue(entity._attr_has_entity_name)
        self.assertEqual(entity._attr_translation_key, "import_price")


class FakeEntry:
    """The parts of ConfigEntry the options flow reads."""

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        self.title = "Test Plan"
        self.entry_id = "entry1"


class OptionsDriver:
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.flow = OptionsFlow()
        self.flow.config_entry = FakeEntry(options or sample_options())
        self.flow.hass = _ha_stubs.FakeHass()
        self.result: dict[str, Any] = {}

    def start(self) -> dict[str, Any]:
        self.result = run(self.flow.async_step_init())
        return self.result

    def choose(self, option: str) -> dict[str, Any]:
        assert self.result["type"] == MENU, self.result
        assert option in self.result["menu_options"], self.result["menu_options"]
        self.result = run(getattr(self.flow, f"async_step_{option}")())
        return self.result

    def submit(self, **overrides: Any) -> dict[str, Any]:
        assert self.result["type"] == FORM, self.result
        payload = _ha_stubs.defaults(self.result)
        payload.update(overrides)
        step = getattr(self.flow, f"async_step_{self.result['step_id']}")
        self.result = run(step(payload))
        return self.result

    @property
    def step(self) -> str:
        return str(self.result.get("step_id", self.result.get("type")))


class TestOptionsFlow(unittest.TestCase):
    def test_the_menu_shows_the_plan(self) -> None:
        driver = OptionsDriver()
        result = driver.start()
        self.assertEqual(result["type"], MENU)
        plan_text = result["description_placeholders"]["plan"]
        self.assertIn("Every day", plan_text)
        self.assertIn("Coverage: complete", plan_text)

    def test_every_menu_entry_opens(self) -> None:
        for entry in PKG.config_flow.MENU:
            if entry == "save":
                continue
            driver = OptionsDriver()
            driver.start()
            result = driver.choose(entry)
            self.assertIn(result["type"], (FORM, MENU), entry)

    def test_save_writes_a_valid_plan(self) -> None:
        driver = OptionsDriver()
        driver.start()
        result = driver.choose("save")
        self.assertEqual(result["type"], CREATE)
        plan = Plan.from_dict({**result["data"], CONST.CONF_NAME: "Test Plan"})
        self.assertEqual(PKG.validate.validate_plan(plan), [])

    def test_save_is_refused_while_the_plan_is_invalid(self) -> None:
        options = sample_options()
        options[CONST.CONF_DAY_PATTERNS][0][CONST.CONF_PERIODS].pop()
        driver = OptionsDriver(options)
        driver.start()
        result = driver.choose("save")
        self.assertEqual(result["type"], MENU)
        self.assertIn("Not ready to save", result["description_placeholders"]["plan"])

    def test_adding_a_rate(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("rates_menu")
        driver.choose("rate_add")
        driver.submit(name="Shoulder", import_cents=32.1)
        self.assertEqual(driver.step, "rates_menu")
        self.assertIn("Shoulder", driver.result["description_placeholders"]["rates"])

    def test_renaming_a_rate_follows_into_its_periods(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("rates_menu")
        driver.choose("rate_pick")
        # An older rate carries no timetable. Editing it keeps it that way
        # rather than renaming its entity out from under the user.
        driver.submit(name="Every day Peak")
        driver.submit(
            name="Every day Evening",
            import_cents=56.88,
            timetable=PKG.config_flow.UNSCOPED_TIMETABLE,
        )
        periods = driver.flow.working[CONST.CONF_DAY_PATTERNS][0][CONST.CONF_PERIODS]
        self.assertEqual(periods[1][CONST.CONF_RATE], "Every day Evening")

    def test_a_rate_in_use_cannot_be_removed(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("rates_menu")
        driver.choose("rate_remove")
        driver.submit(name="Every day Peak")
        self.assertEqual(driver.result["errors"].get("name"), "rate_in_use")

    def test_duplicating_a_timetable_copies_its_periods(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("day_patterns_menu")
        driver.choose("day_pattern_duplicate")
        driver.submit(source="Every day", name="Weekend")
        patterns = driver.flow.working[CONST.CONF_DAY_PATTERNS]
        self.assertEqual(len(patterns), 2)
        self.assertEqual(len(patterns[1][CONST.CONF_PERIODS]), 2)

    def test_editing_a_period(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("periods_pick_day_pattern")
        self.assertEqual(driver.step, "periods_menu")
        driver.choose("period_pick")
        driver.submit(period="00:00 to 16:00  Every day Off Peak")
        driver.submit(start="00:00:00", end="15:00:00", rate="Every day Off Peak")
        self.assertEqual(driver.step, "periods_menu")
        periods = driver.flow.working[CONST.CONF_DAY_PATTERNS][0][CONST.CONF_PERIODS]
        self.assertEqual(periods[0][CONST.CONF_END], "15:00")

    def test_the_feed_in_section_opens(self) -> None:
        driver = OptionsDriver()
        driver.start()
        result = driver.choose("export_menu")
        self.assertEqual(result["type"], MENU)

    def test_the_rate_plan_card_renders(self) -> None:
        driver = OptionsDriver()
        driver.start()
        result = driver.choose("rate_plan_card")
        card = result["description_placeholders"]["card"]
        self.assertIn("buy", card)
        self.assertIn("Every day Peak", card)

    def test_a_failing_step_shows_the_traceback(self) -> None:
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        driver = OptionsDriver()
        driver.flow._working = None
        driver.flow.config_entry.options = {"rates": [{"import_cents": 1}]}
        result = run(driver.flow.async_step_rate_plan_card())
        self.assertIn(result["step_id"], ("rate_plan_card", "failure"))


if __name__ == "__main__":
    unittest.main()


class TestOptionsFlowBranches(unittest.TestCase):
    """The paths a first pass misses: empty lists, removals, redirects."""

    def _empty(self) -> OptionsDriver:
        return OptionsDriver({CONST.CONF_RATES: [], CONST.CONF_DAY_PATTERNS: []})

    def test_editing_a_rate_with_none_defined_opens_the_add_form(self) -> None:
        driver = self._empty()
        driver.start()
        driver.choose("rates_menu")
        self.assertEqual(driver.choose("rate_pick")["step_id"], "rate_add")

    def test_removing_a_rate_with_none_defined_returns_to_the_menu(self) -> None:
        driver = self._empty()
        driver.start()
        driver.choose("rates_menu")
        self.assertEqual(driver.choose("rate_remove")["step_id"], "rates_menu")

    def test_removing_an_unused_rate(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("rates_menu")
        driver.choose("rate_add")
        driver.submit(name="Spare", import_cents=1.0)
        driver.choose("rate_remove")
        driver.submit(name="every_day.spare")
        self.assertEqual(driver.step, "rates_menu")
        self.assertNotIn("Spare", driver.result["description_placeholders"]["rates"])

    def test_timetables_with_none_defined_open_the_add_form(self) -> None:
        driver = self._empty()
        driver.start()
        driver.choose("day_patterns_menu")
        self.assertEqual(
            driver.choose("day_pattern_pick")["step_id"], "day_pattern_add"
        )
        driver.start()
        driver.choose("day_patterns_menu")
        self.assertEqual(
            driver.choose("day_pattern_duplicate")["step_id"], "day_pattern_add"
        )

    def test_adding_and_removing_a_timetable(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("day_patterns_menu")
        driver.choose("day_pattern_add")
        driver.submit(name="Weekend", same_every_day=False, days=["sat", "sun"])
        self.assertEqual(driver.step, "day_patterns_menu")
        driver.choose("day_pattern_remove")
        driver.submit(name="Weekend")
        self.assertEqual(len(driver.flow.working[CONST.CONF_DAY_PATTERNS]), 1)

    def test_a_timetable_needs_a_name_and_days(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("day_patterns_menu")
        driver.choose("day_pattern_add")
        driver.submit(name="", same_every_day=True)
        self.assertEqual(driver.result["errors"].get("name"), "name_required")
        driver.submit(name="Weekend", same_every_day=False, days=[])
        self.assertEqual(driver.result["errors"].get("days"), "days_required")

    def test_periods_with_several_timetables_asks_which(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("day_patterns_menu")
        driver.choose("day_pattern_duplicate")
        driver.submit(source="Every day", name="Weekend")
        driver.result = run(driver.flow.async_step_init())
        result = driver.choose("periods_pick_day_pattern")
        self.assertEqual(result["step_id"], "periods_pick_day_pattern")
        driver.submit(name="Weekend")
        self.assertEqual(driver.step, "periods_menu")

    def test_adding_and_removing_a_period(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("periods_pick_day_pattern")
        driver.choose("period_remove")
        driver.submit(period="16:00 to 24:00  Every day Peak")
        self.assertEqual(driver.step, "periods_menu")
        driver.choose("period_add")
        driver.submit(start="16:00:00", end="00:00:00", rate="Every day Peak")
        periods = driver.flow.working[CONST.CONF_DAY_PATTERNS][0][CONST.CONF_PERIODS]
        self.assertEqual(len(periods), 2)

    def test_a_period_ending_before_it_starts_is_refused(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("periods_pick_day_pattern")
        driver.choose("period_add")
        driver.submit(start="12:00:00", end="06:00:00", rate="Every day Peak")
        self.assertEqual(driver.result["errors"].get("end"), "end_before_start")

    def test_feed_in_rates_can_be_added_edited_and_removed(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("export_menu")
        driver.choose("export_rate_add")
        driver.submit(name="Evening", export_cents=12.0)
        self.assertEqual(driver.step, "export_menu")
        driver.choose("export_rate_pick")
        driver.submit(name="Evening")
        driver.submit(name="Evening peak", export_cents=14.0)
        self.assertEqual(
            driver.flow.working[CONST.CONF_EXPORT_RATES][0][CONST.CONF_NAME],
            "Evening peak",
        )
        driver.choose("export_rate_remove")
        driver.submit(name="Evening peak")
        self.assertEqual(driver.flow.working[CONST.CONF_EXPORT_RATES], [])

    def test_a_duplicate_feed_in_rate_is_refused(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("export_menu")
        driver.choose("export_rate_add")
        driver.submit(name="Evening", export_cents=12.0)
        driver.choose("export_rate_add")
        driver.submit(name="Evening", export_cents=9.0)
        self.assertEqual(driver.result["errors"].get("name"), "rate_exists")

    def test_feed_in_periods_reuse_the_period_screens(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("export_menu")
        driver.choose("export_rate_add")
        driver.submit(name="All day", export_cents=2.7)
        driver.choose("export_periods_pick")
        self.assertTrue(driver.flow._editing_export)
        self.assertEqual(driver.step, "periods_menu")
        driver.choose("period_add")
        driver.submit(start="00:00:00", end="00:00:00", rate="All day")
        pattern = driver.flow.working[CONST.CONF_DAY_PATTERNS][0]
        self.assertEqual(len(pattern[CONST.CONF_EXPORT_PERIODS]), 1)

    def test_general_settings_are_stored(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("general")
        driver.submit(
            daily_supply_charge_cents=99.0,
            demand_rate_per_kw_month=12.5,
            monthly_charge=19.0,
        )
        self.assertEqual(driver.step, "init")
        self.assertEqual(driver.flow.working[CONST.CONF_DEMAND_RATE], 12.5)
        self.assertEqual(driver.flow.working[CONST.CONF_MONTHLY_CHARGE], 19.0)

    def test_backwards_validity_is_refused(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("general")
        driver.submit(valid_from="2026-07-01", valid_to="2026-01-01")
        self.assertEqual(driver.result["errors"].get("valid_to"), "validity_backwards")

    def test_usage_tracking_links_a_select(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("usage_tracking")
        driver.choose("meter_link")
        driver.submit(tariff_selects=["select.meter"], supply_charge_entities=True)
        self.assertEqual(
            driver.flow.working[CONST.CONF_TARIFF_SELECTS], ["select.meter"]
        )
        self.assertTrue(driver.flow.working[CONST.CONF_SUPPLY_CHARGE_ENTITIES])

    def test_creating_a_meter_needs_rates(self) -> None:
        driver = self._empty()
        driver.start()
        driver.choose("usage_tracking")
        self.assertEqual(driver.choose("meter_create")["step_id"], "rate_add")


class TestRuleLists(unittest.TestCase):
    """Two multi-selects, both seeded, both accepting a typed value.

    Each rule is typed once into whichever list it belongs to. What is stored
    is the union plus the enforceable subset, so anything reading the flat
    ``constraints`` list sees exactly what it always saw.
    """

    def _field(self, driver: OptionsDriver, key: str) -> Any:
        for marker in driver.result["data_schema"].schema:
            if getattr(marker, "schema", marker) == key:
                return marker, driver.result["data_schema"].schema[marker]
        raise AssertionError(f"no {key} field on {driver.step}")

    def _at_rate_form(self) -> OptionsDriver:
        driver = OptionsDriver()
        driver.start()
        driver.choose("rates_menu")
        driver.choose("rate_add")
        return driver

    def _last_rate(self, driver: OptionsDriver) -> dict[str, Any]:
        rates: list[dict[str, Any]] = driver.flow.working[CONST.CONF_RATES]
        return rates[-1]

    def test_both_lists_are_on_the_form_and_seeded(self) -> None:
        driver = self._at_rate_form()
        for key in (
            CONST.CONF_INFORMATION_CONSTRAINTS,
            CONST.CONF_ENFORCEABLE_CONSTRAINTS,
        ):
            _, field = self._field(driver, key)
            self.assertEqual(
                sorted(field.config.options), sorted(CONST.KNOWN_CONSTRAINTS)
            )
            self.assertTrue(field.config.multiple)
            self.assertTrue(field.config.custom_value)

    def test_an_information_rule_is_stored_but_not_enforceable(self) -> None:
        driver = self._at_rate_form()
        driver.submit(
            name="Night",
            import_cents=39.6,
            information_constraints=["precool_opportunity"],
        )
        rate = self._last_rate(driver)
        self.assertEqual(rate[CONST.CONF_CONSTRAINTS], ["precool_opportunity"])
        self.assertEqual(rate[CONST.CONF_ENFORCEABLE_CONSTRAINTS], [])

    def test_an_enforceable_rule_lands_in_both_the_union_and_the_subset(self) -> None:
        driver = self._at_rate_form()
        driver.submit(
            name="Night", import_cents=39.6, enforceable_constraints=["no_grid_import"]
        )
        rate = self._last_rate(driver)
        self.assertEqual(rate[CONST.CONF_CONSTRAINTS], ["no_grid_import"])
        self.assertEqual(rate[CONST.CONF_ENFORCEABLE_CONSTRAINTS], ["no_grid_import"])

    def test_a_mixture_keeps_the_roles_apart(self) -> None:
        driver = self._at_rate_form()
        driver.submit(
            name="Peak",
            import_cents=56.88,
            information_constraints=["precool_opportunity"],
            enforceable_constraints=["no_grid_import"],
        )
        rate = self._last_rate(driver)
        self.assertEqual(
            rate[CONST.CONF_CONSTRAINTS], ["precool_opportunity", "no_grid_import"]
        )
        self.assertEqual(rate[CONST.CONF_ENFORCEABLE_CONSTRAINTS], ["no_grid_import"])

    def test_a_rule_in_both_lists_is_refused(self) -> None:
        driver = self._at_rate_form()
        driver.submit(
            name="Peak",
            import_cents=56.88,
            information_constraints=["no_grid_import"],
            enforceable_constraints=["no_grid_import"],
        )
        self.assertEqual(
            driver.result["errors"].get(CONST.CONF_ENFORCEABLE_CONSTRAINTS),
            "rule_in_both_lists",
        )

    def test_a_typed_rule_joins_the_list_for_the_next_rate(self) -> None:
        driver = self._at_rate_form()
        driver.submit(
            name="Night", import_cents=39.6, enforceable_constraints=["hold_battery"]
        )
        driver.choose("rate_add")
        for key in (
            CONST.CONF_INFORMATION_CONSTRAINTS,
            CONST.CONF_ENFORCEABLE_CONSTRAINTS,
        ):
            _, field = self._field(driver, key)
            self.assertIn("hold_battery", field.config.options)

    def test_duplicates_within_one_list_collapse(self) -> None:
        driver = self._at_rate_form()
        driver.submit(
            name="Night",
            import_cents=39.6,
            information_constraints=["no_grid_import", "no_grid_import"],
        )
        self.assertEqual(
            self._last_rate(driver)[CONST.CONF_CONSTRAINTS], ["no_grid_import"]
        )

    def test_editing_a_rate_puts_each_rule_back_in_its_own_list(self) -> None:
        driver = self._at_rate_form()
        driver.submit(
            name="Night",
            import_cents=39.6,
            information_constraints=["precool_opportunity"],
            enforceable_constraints=["no_grid_import"],
        )
        driver.choose("rate_pick")
        driver.submit(name="every_day.night")
        info_key, _ = self._field(driver, CONST.CONF_INFORMATION_CONSTRAINTS)
        enf_key, _ = self._field(driver, CONST.CONF_ENFORCEABLE_CONSTRAINTS)
        self.assertEqual(info_key.default(), ["precool_opportunity"])
        self.assertEqual(enf_key.default(), ["no_grid_import"])

    def test_a_plan_written_before_the_distinction_loads_as_information(self) -> None:
        """Existing rules keep the meaning they had rather than being promoted."""
        data = sample_options()
        data[CONST.CONF_RATES][0].pop(CONST.CONF_ENFORCEABLE_CONSTRAINTS, None)
        driver = OptionsDriver(data)
        driver.start()
        driver.choose("rates_menu")
        driver.choose("rate_pick")
        driver.submit(name="Every day Off Peak")
        info_key, _ = self._field(driver, CONST.CONF_INFORMATION_CONSTRAINTS)
        enf_key, _ = self._field(driver, CONST.CONF_ENFORCEABLE_CONSTRAINTS)
        self.assertEqual(info_key.default(), ["grid_charge_battery"])
        self.assertEqual(enf_key.default(), [])


class TestTheSetupFormCanSetRules(unittest.TestCase):
    """The bug: the setup form never asked, and wrote an empty list anyway."""

    def test_the_setup_rate_form_offers_both_lists(self) -> None:
        fields = [
            getattr(key, "schema", key)
            for key in PKG.config_flow._rate_schema(
                {}, [], fields=PKG.config_flow.SETUP_RATE_FIELDS
            ).schema
        ]
        self.assertEqual(
            fields,
            [
                CONST.CONF_NAME,
                CONST.CONF_IMPORT_CENTS,
                CONST.CONF_INFORMATION_CONSTRAINTS,
                CONST.CONF_ENFORCEABLE_CONSTRAINTS,
            ],
        )

    def test_the_edit_form_is_the_same_form_unfiltered(self) -> None:
        """One definition. The setup form asking for less is the only difference."""
        setup = {
            getattr(key, "schema", key)
            for key in PKG.config_flow._rate_schema(
                {}, [], fields=PKG.config_flow.SETUP_RATE_FIELDS
            ).schema
        }
        full = {
            getattr(key, "schema", key)
            for key in PKG.config_flow._rate_schema({}, ["Other"]).schema
        }
        self.assertTrue(setup < full)
        self.assertEqual(
            full - setup,
            {
                CONST.CONF_COASTING_PERMITTED,
                CONST.CONF_DEMAND_PERIOD,
                CONST.CONF_DAILY_ALLOWANCE_KWH,
                CONST.CONF_EXPORT_ALLOWANCE_KWH,
                CONST.CONF_FALLBACK_RATE,
            },
        )

    def test_rules_entered_at_setup_reach_the_stored_rate(self) -> None:
        record = PKG.config_flow._rate_record(
            {
                CONST.CONF_NAME: "Peak",
                CONST.CONF_IMPORT_CENTS: 56.88,
                CONST.CONF_INFORMATION_CONSTRAINTS: ["precool_opportunity"],
                CONST.CONF_ENFORCEABLE_CONSTRAINTS: ["no_grid_import"],
            }
        )
        self.assertEqual(
            record[CONST.CONF_CONSTRAINTS], ["precool_opportunity", "no_grid_import"]
        )
        self.assertEqual(record[CONST.CONF_ENFORCEABLE_CONSTRAINTS], ["no_grid_import"])
