"""What was declared survives, and what is on screen has a name.

    python3 -m unittest tests.test_declaration

Two guards, both about the component being a source of truth rather than about
any one screen.

`TestAnEditChangesOnlyWhatWasEdited` is the important one. Configure is
menu-driven: the user picks the one thing they want to change, and nothing
else about the plan may be affected. It is asserted as a whole-structure
diff rather than field by field, so a key nobody thought to assert on still
fails the test.

`TestEveryFieldOnEveryFormHasALabel` is the other half. A field with no entry
in strings.json renders as a bare box with no name against it, which raises
nothing and changes no outcome, so every behavioural test in this suite walks
straight past it.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ha_stubs

_ha_stubs.install()

PACKAGE = "abode_power_tariffs_declaration"
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
PLAN = PKG.plan

FORM = _ha_stubs.FlowResultType.FORM
MENU = _ha_stubs.FlowResultType.MENU


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# A plan with every optional field set to something. A guard driven against a
# sparse plan passes by having nothing to lose.
# --------------------------------------------------------------------------


def populated_options() -> dict[str, Any]:
    return {
        CONST.CONF_PLAN_DESCRIPTION: "Everything filled in",
        CONST.CONF_RATES: [
            {
                CONST.CONF_NAME: "Off Peak",
                CONST.CONF_TIMETABLE: "Every day",
                CONST.CONF_IMPORT_CENTS: 19.8,
                # Vestigial, and stored. A rate edit must not quietly drop it.
                CONST.CONF_EXPORT_CENTS: 3.3,
                # Stored sorted: a rate's rules are a set, and the plan reads
                # them back sorted either way.
                CONST.CONF_CONSTRAINTS: ["coasting_permitted", "grid_charge_battery"],
                CONST.CONF_ENFORCEABLE_CONSTRAINTS: ["grid_charge_battery"],
                CONST.CONF_RATE_ALLOWANCE_KWH: None,
                CONST.CONF_FALLBACK_RATE: None,
                CONST.CONF_DEMAND_PERIOD: False,
                CONST.CONF_DEMAND_RATE: 0.0,
                # Stored, never written by a screen, never read at runtime.
                # Exactly the kind of field a rebuild loses silently.
                CONST.CONF_COMPONENTS: {"network": 8.5},
            },
            {
                CONST.CONF_NAME: "Peak",
                CONST.CONF_TIMETABLE: "Every day",
                CONST.CONF_IMPORT_CENTS: 56.88,
                CONST.CONF_EXPORT_CENTS: 0.0,
                CONST.CONF_CONSTRAINTS: ["no_grid_import"],
                CONST.CONF_ENFORCEABLE_CONSTRAINTS: ["no_grid_import"],
                CONST.CONF_RATE_ALLOWANCE_KWH: 12.0,
                CONST.CONF_FALLBACK_RATE: "Off Peak",
                CONST.CONF_DEMAND_PERIOD: True,
                CONST.CONF_DEMAND_RATE: 14.5,
                CONST.CONF_COMPONENTS: {},
            },
        ],
        CONST.CONF_EXPORT_RATES: [
            {
                CONST.CONF_NAME: "Every day Daytime",
                CONST.CONF_EXPORT_CENTS: 5.0,
                CONST.CONF_EXPORT_ALLOWANCE_KWH: 10.0,
                CONST.CONF_EXPORT_FALLBACK_CENTS: 2.0,
            }
        ],
        CONST.CONF_DAY_PATTERNS: [
            {
                CONST.CONF_NAME: "Every day",
                CONST.CONF_DAYS: list(CONST.ALL_DAY_TOKENS),
                CONST.CONF_SEASON_FROM: None,
                CONST.CONF_SEASON_TO: None,
                CONST.CONF_PERIODS: [
                    {
                        CONST.CONF_START: "00:00",
                        CONST.CONF_END: "16:00",
                        CONST.CONF_RATE: "Off Peak",
                    },
                    {
                        CONST.CONF_START: "16:00",
                        CONST.CONF_END: "24:00",
                        CONST.CONF_RATE: "Peak",
                    },
                ],
                CONST.CONF_EXPORT_PERIODS: [],
                CONST.CONF_EXPORT_SAME_ALL_DAY: True,
                CONST.CONF_EXPORT_FLAT_CENTS: 5.0,
                # The two P32 added, and the two a rebuild drops.
                CONST.CONF_EXPORT_ALLOWANCE_KWH: 10.0,
                CONST.CONF_EXPORT_FALLBACK_CENTS: 2.0,
            }
        ],
        CONST.CONF_SUPPLY_CHARGE_CENTS: 116.6,
        CONST.CONF_MONTHLY_CHARGE: 5.5,
        CONST.CONF_BILLING_CYCLE_DAY: 12,
        CONST.CONF_PRICES_INCLUDE_GST: True,
        CONST.CONF_GST_PERCENT: 10.0,
        CONST.CONF_VALID_FROM: None,
        CONST.CONF_VALID_TO: None,
        CONST.CONF_HOLIDAY_SENSOR: None,
        CONST.CONF_COUNT_ALLOWANCE: False,
        CONST.CONF_IMPORT_ENERGY_SENSOR: None,
    }


class FakeEntry:
    """The parts of ConfigEntry the options flow reads."""

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        self.title = "Test Plan"
        self.entry_id = "entry1"


class OptionsDriver:
    """Steps the options flow the way the frontend does."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.flow = FLOW.AbodePowerTariffsOptionsFlow()
        self.flow.config_entry = FakeEntry(options or populated_options())
        self.flow.hass = _ha_stubs.FakeHass()
        self.result: dict[str, Any] = {}

    def start(self) -> dict[str, Any]:
        self.result = run(self.flow.async_step_init())
        return self.result

    def choose(self, option: str) -> dict[str, Any]:
        assert self.result["type"] == MENU, self.result
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


def differences(before: Any, after: Any, path: str = "") -> list[str]:
    """Return every place two stored structures differ, by path.

    A whole-structure comparison on purpose. Asserting on named fields only
    finds what the person writing the test already thought of, and the fault
    this guards against is precisely a field nobody thought of.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        found: list[str] = []
        for key in sorted(set(before) | set(after)):
            here = f"{path}.{key}" if path else str(key)
            if key not in before:
                found.append(f"{here}: added ({after[key]!r})")
            elif key not in after:
                found.append(f"{here}: removed (was {before[key]!r})")
            else:
                found.extend(differences(before[key], after[key], here))
        return found
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [f"{path}: length {len(before)} -> {len(after)}"]
        found = []
        for index, (was, now) in enumerate(zip(before, after, strict=True)):
            found.extend(differences(was, now, f"{path}[{index}]"))
        return found
    if before != after:
        return [f"{path}: {before!r} -> {after!r}"]
    return []


class TestAnEditChangesOnlyWhatWasEdited(unittest.TestCase):
    """The rule the Configure flow is designed around, asserted.

    The user picks one thing to change. Everything else in the stored plan
    must come out the other side untouched.
    """

    def setUp(self) -> None:
        self.before = populated_options()
        self.driver = OptionsDriver(copy.deepcopy(self.before))
        self.driver.start()

    def assert_only(self, expected: list[str]) -> None:
        found = differences(self.before, self.driver.flow.working)
        self.assertEqual(found, expected, "\n".join(found))

    def test_changing_a_timetables_feed_in_price_keeps_its_allowance(self) -> None:
        self.driver.choose("day_patterns_menu")
        self.driver.choose("day_pattern_pick")
        self.driver.submit(name="Every day")
        self.driver.submit(export_flat_cents=6.0)
        self.assert_only(["day_patterns[0].export_flat_cents: 5.0 -> 6.0"])

    def test_changing_a_timetables_days_keeps_everything_else(self) -> None:
        self.driver.choose("day_patterns_menu")
        self.driver.choose("day_pattern_pick")
        self.driver.submit(name="Every day")
        self.driver.submit(same_every_day=False, days=["mon", "tue"])
        self.assert_only(
            [
                "day_patterns[0].days: length 8 -> 2",
            ]
        )

    def test_changing_a_rates_price_keeps_the_rest_of_the_rate(self) -> None:
        self.driver.choose("rates_menu")
        self.driver.choose("rate_pick")
        self.driver.submit(name="every_day.off_peak")
        self.driver.submit(import_cents=21.0)
        self.assert_only(["rates[0].import_cents: 19.8 -> 21.0"])

    def test_changing_an_export_rates_price_keeps_its_allowance(self) -> None:
        self.driver.choose("export_menu")
        self.driver.choose("export_rate_pick")
        self.driver.submit(name="Every day Daytime")
        self.driver.submit(export_cents=6.5)
        self.assert_only(["export_rates[0].export_cents: 5.0 -> 6.5"])

    def test_changing_a_time_period_keeps_the_rest_of_the_timetable(self) -> None:
        self.driver.choose("periods_pick_day_pattern")
        self.driver.choose("period_pick")
        self.driver.submit(period="00:00 to 16:00  Off Peak")
        self.driver.submit(end="15:00:00")
        self.assert_only(["day_patterns[0].periods[0].end: '16:00' -> '15:00'"])

    def test_changing_the_supply_charge_keeps_the_other_charges(self) -> None:
        self.driver.choose("general")
        self.driver.submit(daily_supply_charge_cents=120.0)
        self.assert_only(["daily_supply_charge_cents: 116.6 -> 120.0"])


class TestTheTimetableScreenOffersItsFeedInDeclaration(unittest.TestCase):
    """An all-day feed-in price, its cap, and what is paid past the cap.

    One declaration. Configure has to offer all three or a plan can say
    something at setup that can never be corrected afterwards.
    """

    def setUp(self) -> None:
        self.driver = OptionsDriver()
        self.driver.start()
        self.driver.choose("day_patterns_menu")
        self.driver.choose("day_pattern_pick")
        self.driver.submit(name="Every day")

    def _fields(self) -> list[str]:
        schema = self.driver.result["data_schema"].schema
        return [str(getattr(key, "schema", key)) for key in schema]

    def test_the_cap_and_the_price_past_it_are_on_the_form(self) -> None:
        fields = self._fields()
        self.assertIn(CONST.CONF_EXPORT_ALLOWANCE_KWH, fields)
        self.assertIn(CONST.CONF_EXPORT_FALLBACK_CENTS, fields)

    def test_the_form_opens_on_what_is_stored(self) -> None:
        submitted = _ha_stubs.defaults(self.driver.result)
        self.assertEqual(submitted[CONST.CONF_EXPORT_ALLOWANCE_KWH], 10.0)
        self.assertEqual(submitted[CONST.CONF_EXPORT_FALLBACK_CENTS], 2.0)

    def test_the_cap_can_be_changed(self) -> None:
        self.driver.submit(export_allowance_kwh=15.0, export_fallback_cents=1.5)
        pattern = self.driver.flow.working[CONST.CONF_DAY_PATTERNS][0]
        self.assertEqual(pattern[CONST.CONF_EXPORT_ALLOWANCE_KWH], 15.0)
        self.assertEqual(pattern[CONST.CONF_EXPORT_FALLBACK_CENTS], 1.5)


class TestStoredRecordsKeepTheModelsShape(unittest.TestCase):
    """Every record a screen writes has exactly the keys the model reads.

    The model in plan.py holds each object's shape once. A screen that builds
    its own dict holds a second copy of that knowledge, and the second copy is
    what goes stale.
    """

    def _canonical(self, model: Any, record: dict[str, Any]) -> set[str]:
        return set(model.from_dict(record).as_dict())

    def test_an_edited_timetable_has_every_key(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("day_patterns_menu")
        driver.choose("day_pattern_pick")
        driver.submit(name="Every day")
        driver.submit(export_flat_cents=6.0)
        record = driver.flow.working[CONST.CONF_DAY_PATTERNS][0]
        self.assertEqual(set(record), self._canonical(PLAN.DayPattern, record))

    def test_an_edited_rate_has_every_key(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("rates_menu")
        driver.choose("rate_pick")
        driver.submit(name="every_day.off_peak")
        driver.submit(import_cents=21.0)
        record = driver.flow.working[CONST.CONF_RATES][0]
        self.assertEqual(set(record), self._canonical(PLAN.Rate, record))

    def test_an_edited_export_rate_has_every_key(self) -> None:
        driver = OptionsDriver()
        driver.start()
        driver.choose("export_menu")
        driver.choose("export_rate_pick")
        driver.submit(name="Every day Daytime")
        driver.submit(export_cents=6.5)
        record = driver.flow.working[CONST.CONF_EXPORT_RATES][0]
        self.assertEqual(set(record), self._canonical(PLAN.ExportRate, record))


# --------------------------------------------------------------------------
# Every form, and the words on it.
# --------------------------------------------------------------------------


def _setup_forms() -> list[tuple[str, Any]]:
    """Show every form the setup flow can show, without submitting anything."""
    found: list[tuple[str, Any]] = []

    def record(result: dict[str, Any]) -> None:
        if result["type"] == FORM and result.get("data_schema") is not None:
            found.append((result["step_id"], result["data_schema"]))

    flow = FLOW.AbodePowerTariffsConfigFlow()
    record(run(flow.async_step_user()))
    record(run(flow.async_step_charges()))
    # The single-rate screen grows the feed-in half when export is declared.
    flow._has_export = True
    record(run(flow.async_step_single_rate()))
    record(run(flow.async_step_days()))
    flow._pattern_name = "Every day"
    flow._rates = [
        {
            CONST.CONF_NAME: "Peak",
            CONST.CONF_TIMETABLE: "Every day",
            CONST.CONF_IMPORT_CENTS: 30.0,
        }
    ]
    record(run(flow.async_step_rates()))
    record(run(flow.async_step_periods()))
    record(run(flow.async_step_feed_in()))
    record(run(flow.async_step_export_rates()))
    flow._export_rates = [
        {CONST.CONF_NAME: "Every day Daytime", CONST.CONF_EXPORT_CENTS: 5.0}
    ]
    record(run(flow.async_step_export_periods()))
    record(run(flow.async_step_setup_failure()))
    return found


def _options_forms() -> list[tuple[str, Any]]:
    """Show every form the options flow can show, without submitting anything."""
    found: list[tuple[str, Any]] = []

    def record(result: dict[str, Any]) -> None:
        if result["type"] == FORM and result.get("data_schema") is not None:
            found.append((result["step_id"], result["data_schema"]))

    steps = (
        "failure",
        "rate_plan_card",
        "rate_pick",
        "rate_add",
        "rate_remove",
        "day_pattern_pick",
        "day_pattern_add",
        "day_pattern_duplicate",
        "day_pattern_remove",
        "period_pick",
        "period_add",
        "period_remove",
        "general",
        "export_rate_pick",
        "export_rate_add",
        "export_rate_remove",
        "meter_create",
        "meter_link",
    )
    for step in steps:
        driver = OptionsDriver()
        driver.start()
        record(run(getattr(driver.flow, f"async_step_{step}")()))

    # Two timetables, or the picker skips itself and never renders.
    options = populated_options()
    second = copy.deepcopy(options[CONST.CONF_DAY_PATTERNS][0])
    second[CONST.CONF_NAME] = "Weekend"
    options[CONST.CONF_DAY_PATTERNS].append(second)
    driver = OptionsDriver(options)
    driver.start()
    record(run(driver.flow.async_step_periods_pick_day_pattern()))
    return found


def _fields_of(schema: Any) -> list[tuple[str, str | None]]:
    """Return (field, section) for every box on a form."""
    fields: list[tuple[str, str | None]] = []
    for key, value in schema.schema.items():
        name = str(getattr(key, "schema", key))
        if isinstance(value, _ha_stubs.Section):
            fields.extend((inner, name) for inner, _ in _fields_of(value.schema))
            continue
        fields.append((name, None))
    return fields


class TestEveryFieldOnEveryFormHasALabel(unittest.TestCase):
    """A box with no label renders with no name against it.

    Nothing raises, no outcome changes, and every other test in this suite
    walks straight past it. This is the only thing that looks.
    """

    def setUp(self) -> None:
        self.strings = json.loads((ROOT / "strings.json").read_text())

    def _labelled(self, section: str, step: str, field: str, group: str | None) -> bool:
        block = self.strings[section]["step"].get(step, {})
        if group is not None:
            block = block.get("sections", {}).get(group, {})
        return field in (block.get("data") or {})

    def _missing(self, section: str, forms: list[tuple[str, Any]]) -> list[str]:
        return [
            f"{section}.{step}.{group + '.' if group else ''}{field}"
            for step, schema in forms
            for field, group in _fields_of(schema)
            if not self._labelled(section, step, field, group)
        ]

    def test_every_setup_field_is_named(self) -> None:
        missing = self._missing("config", _setup_forms())
        self.assertEqual(missing, [], "unlabelled: " + ", ".join(missing))

    def test_every_configure_field_is_named(self) -> None:
        missing = self._missing("options", _options_forms())
        self.assertEqual(missing, [], "unlabelled: " + ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
