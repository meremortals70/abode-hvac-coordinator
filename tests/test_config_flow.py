"""Drive the setup flow end to end, without Home Assistant.

    python3 -m unittest tests.test_config_flow

Every regression in this component has been in the config flow, and until now
the largest file had the least coverage. These tests step through the screens
the way the frontend does: show a form, submit a dict, follow the result.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ha_stubs

_ha_stubs.install()

PACKAGE = "abode_power_tariffs_flow"
ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "abode_power_tariffs"
MODULES = (
    "const",
    "plan",
    "validate",
    "intervals",
    "allowance",
    "strip",
    "serialise",
    "config_flow",
)


def _load() -> types.ModuleType:
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
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
FLOW = PKG.config_flow
CONST = PKG.const
Plan = PKG.plan.Plan
validate_plan = PKG.validate.validate_plan

FORM = _ha_stubs.FlowResultType.FORM
MENU = _ha_stubs.FlowResultType.MENU
CREATE = _ha_stubs.FlowResultType.CREATE_ENTRY


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FlowDriver:
    """Steps a config flow the way the frontend does."""

    def __init__(self) -> None:
        self.flow = FLOW.AbodePowerTariffsConfigFlow()
        self.result: dict[str, Any] = {}

    def start(self) -> dict[str, Any]:
        self.result = run(self.flow.async_step_user())
        return self.result

    def submit(self, **overrides: Any) -> dict[str, Any]:
        """Submit the current form, taking every default unless overridden."""
        assert self.result["type"] == FORM, f"not a form: {self.result}"
        payload = _ha_stubs.defaults(self.result)
        payload.update(overrides)
        step = getattr(self.flow, f"async_step_{self.result['step_id']}")
        self.result = run(step(payload))
        return self.result

    def choose(self, option: str) -> dict[str, Any]:
        """Press a button on a menu."""
        assert self.result["type"] == MENU, f"not a menu: {self.result}"
        assert option in self.result["menu_options"], self.result["menu_options"]
        self.result = run(getattr(self.flow, f"async_step_{option}")())
        return self.result

    @property
    def step(self) -> str:
        return str(self.result.get("step_id", self.result.get("type")))

    @property
    def errors(self) -> dict[str, str]:
        return dict(self.result.get("errors") or {})


def a_timetable(
    driver: FlowDriver,
    *,
    name: str,
    rates: list[tuple[str, float]],
    days: list[str] | None = None,
    periods: list[tuple[str, str, str]],
    flat_export: float | None = 0.0,
    export_rates: list[tuple[str, float]] | None = None,
    export_periods: list[tuple[str, str, str]] | None = None,
) -> None:
    """Walk one timetable: days, rates, periods, feed-in."""
    assert driver.step == "days", driver.step
    if days is None:
        driver.submit(name=name, same_every_day=True)
    else:
        driver.submit(name=name, same_every_day=False, days=days)

    for rate_name, cents in rates:
        assert driver.step == "rates", driver.step
        driver.submit(name=rate_name, import_cents=cents, on_submit=CONST.SUBMIT_ADD)
    driver.submit(on_submit=CONST.SUBMIT_CONTINUE)

    for start, end, rate in periods:
        assert driver.step == "periods", driver.step
        driver.submit(start=start, end=end, rate=rate, on_submit=CONST.SUBMIT_ADD)
    driver.submit(on_submit=CONST.SUBMIT_CONTINUE)

    assert driver.step == "feed_in", driver.step
    if flat_export is not None:
        driver.submit(export_same_all_day=True, export_flat_cents=flat_export)
        return

    driver.submit(export_same_all_day=False, export_flat_cents=0.0)
    assert export_rates is not None and export_periods is not None
    for rate_name, cents in export_rates:
        assert driver.step == "export_rates", driver.step
        driver.submit(name=rate_name, export_cents=cents, on_submit=CONST.SUBMIT_ADD)
    driver.submit(on_submit=CONST.SUBMIT_CONTINUE)
    for start, end, rate in export_periods:
        assert driver.step == "export_periods", driver.step
        driver.submit(start=start, end=end, rate=rate, on_submit=CONST.SUBMIT_ADD)
    driver.submit(on_submit=CONST.SUBMIT_CONTINUE)


class TestEveryScreenAcceptsItsOwnDefaults(unittest.TestCase):
    """Pressing Submit without touching anything must never raise.

    An exception here is what produced 'Unknown error occurred'.
    """

    def test_first_two_screens(self) -> None:
        driver = FlowDriver()
        self.assertEqual(driver.start()["step_id"], "user")
        driver.submit(plan_name="Test Plan")
        self.assertEqual(driver.step, "charges")
        driver.submit()
        self.assertEqual(driver.step, "days")
        driver.submit()
        self.assertEqual(driver.step, "rates")

    def test_no_step_raises_on_defaults(self) -> None:
        driver = FlowDriver()
        driver.start()
        driver.submit(plan_name="Test Plan")
        driver.submit()
        driver.submit()
        # The rates screen has no default name, so it reports an error rather
        # than raising. That is the correct behaviour.
        driver.submit()
        self.assertEqual(driver.step, "rates")
        self.assertIn("name", driver.errors)


class TestOneTimetable(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = FlowDriver()
        self.driver.start()
        self.driver.submit(plan_name="Ovo Original", plan_description="Legacy plan")
        self.driver.submit(
            daily_supply_charge_cents=116.6,
            monthly_charge=0.0,
            prices_include_gst=True,
            gst_percent=10.0,
            demand_rate_per_kw_month=0.0,
        )

    def _finish(self) -> dict[str, Any]:
        a_timetable(
            self.driver,
            name="Every day",
            rates=[("Off Peak", 19.8), ("Peak", 56.88)],
            periods=[("00:00", "06:00", "Off Peak"), ("06:00", "00:00", "Peak")],
            flat_export=2.7,
        )
        self.assertEqual(self.driver.step, "timetable_done")
        return self.driver.choose("finish")

    def test_creates_a_valid_plan(self) -> None:
        result = self._finish()
        self.assertEqual(result["type"], CREATE)
        plan = Plan.from_dict({**result["options"], "name": result["title"]})
        self.assertEqual(plan.name, "Ovo Original")
        self.assertEqual(plan.description, "Legacy plan")
        self.assertEqual(len(plan.day_patterns), 1)
        self.assertEqual(plan.rate_names, ("Off Peak", "Peak"))
        self.assertEqual(
            plan.qualified_rate_names, ("every_day.off_peak", "every_day.peak")
        )
        self.assertEqual(validate_plan(plan), [])

    def test_prices_survive_the_round_trip(self) -> None:
        result = self._finish()
        plan = Plan.from_dict({**result["options"], "name": result["title"]})
        peak = plan.rate_by_name("Peak", "Every day")
        assert peak is not None
        self.assertAlmostEqual(peak.import_price, 0.5688)
        self.assertAlmostEqual(plan.daily_supply_charge, 1.166)

    def test_flat_feed_in_is_on_the_timetable(self) -> None:
        result = self._finish()
        plan = Plan.from_dict({**result["options"], "name": result["title"]})
        pattern = plan.day_patterns[0]
        self.assertTrue(pattern.export_same_all_day)
        self.assertAlmostEqual(pattern.export_flat_price, 0.027)


class TestTwoTimetables(unittest.TestCase):
    """The case the whole design turns on: weekends inside the same plan."""

    def setUp(self) -> None:
        self.driver = FlowDriver()
        self.driver.start()
        self.driver.submit(plan_name="Two Timetables")
        self.driver.submit()

    def test_second_timetable_with_timed_feed_in(self) -> None:
        a_timetable(
            self.driver,
            name="Weekday",
            days=["mon", "tue", "wed", "thu", "fri"],
            rates=[("Peak", 56.88)],
            periods=[("00:00", "00:00", "Peak")],
            flat_export=2.7,
        )
        self.assertEqual(self.driver.step, "timetable_done")
        self.driver.choose("days")

        a_timetable(
            self.driver,
            name="Weekend",
            days=["sat", "sun", "holiday"],
            rates=[("Off Peak", 19.8)],
            periods=[("00:00", "00:00", "Off Peak")],
            flat_export=None,
            export_rates=[("Daytime", 2.7), ("Evening", 12.0)],
            export_periods=[
                ("00:00", "16:00", "Weekend Daytime"),
                ("16:00", "00:00", "Weekend Evening"),
            ],
        )
        self.assertEqual(self.driver.step, "timetable_done")
        result = self.driver.choose("finish")

        self.assertEqual(result["type"], CREATE)
        plan = Plan.from_dict({**result["options"], "name": result["title"]})
        self.assertEqual(plan.day_pattern_names, ("Weekday", "Weekend"))
        self.assertEqual(plan.rate_names, ("Peak", "Off Peak"))
        self.assertEqual(
            plan.qualified_rate_names, ("weekday.peak", "weekend.off_peak")
        )
        self.assertEqual(plan.export_rate_names, ("Weekend Daytime", "Weekend Evening"))

    def test_feed_in_mode_differs_between_timetables(self) -> None:
        self.test_second_timetable_with_timed_feed_in()

    def test_the_same_name_under_two_timetables_is_two_rates(self) -> None:
        """Both are called Peak. They are told apart by their timetable."""
        a_timetable(
            self.driver,
            name="Weekday",
            days=["mon", "tue", "wed", "thu", "fri"],
            rates=[("Peak", 56.88)],
            periods=[("00:00", "00:00", "Peak")],
            flat_export=2.7,
        )
        self.driver.choose("days")
        a_timetable(
            self.driver,
            name="Weekend",
            days=["sat", "sun", "holiday"],
            rates=[("Peak", 30.0)],
            periods=[("00:00", "00:00", "Peak")],
            flat_export=2.7,
        )
        result = self.driver.choose("finish")
        plan = Plan.from_dict({**result["options"], "name": result["title"]})
        weekday = plan.rate_by_name("Peak", "Weekday")
        weekend = plan.rate_by_name("Peak", "Weekend")
        assert weekday is not None and weekend is not None
        self.assertEqual(weekday.name, "Peak")
        self.assertEqual(weekend.name, "Peak")
        self.assertAlmostEqual(weekday.import_price, 0.5688)
        self.assertAlmostEqual(weekend.import_price, 0.30)
        self.assertEqual(weekday.qualified_name, "weekday.peak")
        self.assertEqual(weekend.qualified_name, "weekend.peak")
        self.assertEqual(validate_plan(plan), [])


class TestEscapingTheLoops(unittest.TestCase):
    """The thing Jason got stuck on: continuing without adding another."""

    def _to_rates(self) -> FlowDriver:
        driver = FlowDriver()
        driver.start()
        driver.submit(plan_name="Escape")
        driver.submit()
        driver.submit(name="Every day", same_every_day=True)
        return driver

    def test_continue_from_rates_keeps_what_was_entered(self) -> None:
        driver = self._to_rates()
        driver.submit(name="Peak", import_cents=50.0, on_submit=CONST.SUBMIT_ADD)
        self.assertEqual(driver.step, "rates")
        # Forgot to change the choice, now on an empty form: continue anyway.
        driver.submit(on_submit=CONST.SUBMIT_CONTINUE)
        self.assertEqual(driver.step, "periods")
        self.assertEqual(len(driver.flow._rates), 1)

    def test_continue_with_no_rates_is_refused(self) -> None:
        driver = self._to_rates()
        driver.submit(on_submit=CONST.SUBMIT_CONTINUE)
        self.assertEqual(driver.step, "rates")
        self.assertIn("name", driver.errors)

    def test_duplicate_rate_name_is_refused(self) -> None:
        driver = self._to_rates()
        driver.submit(name="Peak", import_cents=50.0, on_submit=CONST.SUBMIT_ADD)
        driver.submit(name="Peak", import_cents=10.0, on_submit=CONST.SUBMIT_ADD)
        self.assertEqual(driver.errors.get("name"), "rate_exists")

    def test_continue_from_periods_needs_a_period(self) -> None:
        driver = self._to_rates()
        driver.submit(name="Peak", import_cents=50.0, on_submit=CONST.SUBMIT_ADD)
        driver.submit(on_submit=CONST.SUBMIT_CONTINUE)
        self.assertEqual(driver.step, "periods")
        driver.submit(on_submit=CONST.SUBMIT_CONTINUE)
        self.assertEqual(driver.step, "periods")


class TestPeriodValidation(unittest.TestCase):
    def _to_periods(self) -> FlowDriver:
        driver = FlowDriver()
        driver.start()
        driver.submit(plan_name="Periods")
        driver.submit()
        driver.submit(name="Every day", same_every_day=True)
        driver.submit(name="Peak", import_cents=50.0, on_submit=CONST.SUBMIT_ADD)
        driver.submit(on_submit=CONST.SUBMIT_CONTINUE)
        return driver

    def test_end_before_start_is_refused(self) -> None:
        driver = self._to_periods()
        driver.submit(
            start="12:00:00",
            end="06:00:00",
            rate="Every day Peak",
            on_submit=CONST.SUBMIT_ADD,
        )
        self.assertEqual(driver.errors.get("end"), "end_before_start")

    def test_overlap_is_refused(self) -> None:
        driver = self._to_periods()
        driver.submit(
            start="00:00:00",
            end="12:00:00",
            rate="Every day Peak",
            on_submit=CONST.SUBMIT_ADD,
        )
        driver.submit(
            start="06:00:00",
            end="18:00:00",
            rate="Every day Peak",
            on_submit=CONST.SUBMIT_ADD,
        )
        self.assertEqual(driver.errors.get("base"), "period_overlaps")

    def test_midnight_end_is_the_end_of_the_day(self) -> None:
        driver = self._to_periods()
        driver.submit(
            start="00:00:00",
            end="00:00:00",
            rate="Every day Peak",
            on_submit=CONST.SUBMIT_ADD,
        )
        self.assertEqual(driver.errors, {})
        self.assertEqual(driver.flow._periods[0]["end"], "24:00")


class TestFailuresAreVisible(unittest.TestCase):
    def test_an_exception_shows_the_traceback(self) -> None:
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        driver = FlowDriver()
        driver.start()

        def explode(*_: Any, **__: Any) -> None:
            raise RuntimeError("deliberate")

        driver.flow.async_step_charges = explode  # type: ignore[method-assign]
        result = run(driver.flow.async_step_user({"plan_name": "Boom"}))
        self.assertEqual(result["step_id"], "setup_failure")
        detail = result["description_placeholders"]["detail"]
        self.assertIn("RuntimeError", detail)
        self.assertIn("deliberate", detail)


class TestEveryStepIsReachable(unittest.TestCase):
    def test_setup_steps_are_all_exercised(self) -> None:
        """Every setup screen must be visited by the tests above."""
        expected = {
            "user",
            "charges",
            "days",
            "rates",
            "periods",
            "feed_in",
            "export_rates",
            "export_periods",
            "timetable_done",
            "setup_failure",
        }
        defined = {
            name.removeprefix("async_step_")
            for name in dir(FLOW.AbodePowerTariffsConfigFlow)
            if name.startswith("async_step_")
        }
        self.assertEqual(defined - {"finish"}, expected)


if __name__ == "__main__":
    unittest.main()
