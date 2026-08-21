"""Entities, setup, the action and diagnostics.

    python3 -m unittest tests.test_platform

These are the modules that were at zero coverage: sensor.py, binary_sensor.py,
__init__.py and diagnostics.py. They run against the same thin stubs as the
other suites.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ha_stubs

_ha_stubs.install()

PACKAGE = "abode_power_tariffs_platform"
ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "abode_power_tariffs"
ORDER = (
    "const",
    "plan",
    "validate",
    "intervals",
    "allowance",
    "strip",
    "serialise",
    "coordinator",
    "entity",
    "__init__",
    "sensor",
    "binary_sensor",
    "diagnostics",
)


def _load() -> types.ModuleType:
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package
    for name in ORDER:
        target = PACKAGE if name == "__init__" else f"{PACKAGE}.{name}"
        spec = importlib.util.spec_from_file_location(target, ROOT / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        if name == "__init__":
            module.__path__ = [str(ROOT)]
            module.__package__ = PACKAGE
        sys.modules[target] = module
        spec.loader.exec_module(module)
        if name != "__init__":
            setattr(package, name, module)
    return sys.modules[PACKAGE]


PKG = _load()
CONST = sys.modules[f"{PACKAGE}.const"]
SENSOR = sys.modules[f"{PACKAGE}.sensor"]
BINARY = sys.modules[f"{PACKAGE}.binary_sensor"]
DIAG = sys.modules[f"{PACKAGE}.diagnostics"]
COORD = sys.modules[f"{PACKAGE}.coordinator"]
Plan = sys.modules[f"{PACKAGE}.plan"].Plan

BRISBANE = ZoneInfo("Australia/Brisbane")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def options(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
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
    }
    base.update(extra)
    return base


def a_coordinator(data: dict[str, Any] | None = None) -> Any:
    hass = _ha_stubs.FakeHass()
    data = data or options()
    plan = Plan.from_dict({**data, CONST.CONF_NAME: "Test"})
    coordinator = COORD.TariffCoordinator(hass, "entry1", plan, data)
    coordinator.async_refresh()
    return coordinator


class Added:
    """Captures what a platform hands to async_add_entities."""

    def __init__(self) -> None:
        self.entities: list[Any] = []

    def __call__(self, entities: Any) -> None:
        self.entities.extend(entities)

    def by_key(self, key: str) -> Any:
        for entity in self.entities:
            if entity._key == key:
                return entity
        raise AssertionError(f"no entity {key} in {[e._key for e in self.entities]}")


class FakeEntry:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.options = data or options()
        self.data = {CONST.CONF_PLAN_NAME: "Test Plan"}
        self.title = "Test Plan"
        self.entry_id = "entry1"
        self.domain = CONST.DOMAIN
        self.runtime_data: Any = None
        self.unloaded: list[Any] = []

    def add_update_listener(self, listener: Any) -> Any:
        return lambda: None

    def async_on_unload(self, func: Any) -> None:
        self.unloaded.append(func)


class PlatformCase(unittest.TestCase):
    def setUp(self) -> None:
        _ha_stubs.SCHEDULED.__init__()  # type: ignore[misc]
        COORD.dt_util.NOW = datetime(2026, 8, 14, 10, 0, tzinfo=BRISBANE)
        self.coordinator = a_coordinator()
        self.entry = FakeEntry()
        self.entry.runtime_data = self.coordinator
        self.added = Added()

    def tearDown(self) -> None:
        COORD.dt_util.NOW = None

    def sensors(self, data: dict[str, Any] | None = None) -> Added:
        if data is not None:
            self.coordinator = a_coordinator(data)
            self.entry.runtime_data = self.coordinator
        run(SENSOR.async_setup_entry(self.coordinator.hass, self.entry, self.added))
        return self.added


class TestSensorPlatform(PlatformCase):
    def test_the_five_always_present_sensors(self) -> None:
        added = self.sensors()
        keys = {entity._key for entity in added.entities}
        self.assertEqual(
            keys,
            {
                "import_price",
                "export_price",
                "rate",
                "next_rate_change",
                "daily_supply_charge",
            },
        )

    def test_import_price_is_dollars_per_kwh(self) -> None:
        sensor = self.sensors().by_key("import_price")
        self.assertAlmostEqual(sensor.native_value, 0.198)
        self.assertEqual(sensor._attr_native_unit_of_measurement, "AUD/kWh")

    def test_export_price_comes_from_the_timetable(self) -> None:
        sensor = self.sensors().by_key("export_price")
        self.assertAlmostEqual(sensor.native_value, 0.027)
        self.assertTrue(sensor.available)

    def test_price_attributes(self) -> None:
        sensor = self.sensors().by_key("import_price")
        attributes = sensor.extra_state_attributes
        self.assertEqual(attributes["rate"], "Every day Off Peak")
        self.assertEqual(attributes["period_start"], "00:00")
        self.assertEqual(attributes["period_end"], "16:00")
        self.assertEqual(attributes["day_pattern"], "Every day")
        self.assertEqual(attributes["constraints"], ["grid_charge_battery"])
        self.assertFalse(attributes["coasting_permitted"])
        self.assertFalse(attributes["plan_expired"])

    def test_the_forecast_attribute_is_evcc_shaped(self) -> None:
        sensor = self.sensors().by_key("import_price")
        forecast = sensor.extra_state_attributes["forecast"]
        self.assertTrue(forecast)
        self.assertEqual(set(forecast[0]), {"start", "end", "value"})
        # Local time with the offset, like everything else this publishes.
        self.assertFalse(forecast[0]["start"].endswith("Z"))
        self.assertTrue(forecast[0]["start"].endswith("+10:00"))

    def test_the_forecast_is_not_written_to_the_database(self) -> None:
        """A history of 24-hour predictions is tens of megabytes a day."""
        for key in ("import_price", "export_price"):
            sensor = self.sensors().by_key(key)
            self.assertIn("forecast", type(sensor)._unrecorded_attributes)

    def test_rate_sensor_is_an_enum_of_the_plan(self) -> None:
        sensor = self.sensors().by_key("rate")
        self.assertEqual(sensor.native_value, "Every day Off Peak")
        self.assertEqual(sensor._attr_options, ["Every day Off Peak", "Every day Peak"])
        self.assertEqual(
            sensor.extra_state_attributes["scheduled_rate"], "Every day Off Peak"
        )

    def test_next_rate_change_is_the_boundary(self) -> None:
        sensor = self.sensors().by_key("next_rate_change")
        assert sensor.native_value is not None
        self.assertEqual(sensor.native_value.astimezone(BRISBANE).hour, 16)

    def test_supply_charge_is_dollars_per_day_and_always_available(self) -> None:
        sensor = self.sensors().by_key("daily_supply_charge")
        self.assertAlmostEqual(sensor.native_value, 1.166)
        self.assertTrue(sensor.available)

    def test_no_export_change_sensor_on_a_flat_feed_in_price(self) -> None:
        keys = {entity._key for entity in self.sensors().entities}
        self.assertNotIn("next_export_change", keys)

    def test_the_export_change_sensor_appears_with_a_feed_in_schedule(self) -> None:
        data = options()
        pattern = data[CONST.CONF_DAY_PATTERNS][0]
        pattern[CONST.CONF_EXPORT_SAME_ALL_DAY] = False
        pattern[CONST.CONF_EXPORT_PERIODS] = [
            {
                CONST.CONF_START: "00:00",
                CONST.CONF_END: "09:00",
                CONST.CONF_RATE: "Night",
            },
            {
                CONST.CONF_START: "09:00",
                CONST.CONF_END: "16:00",
                CONST.CONF_RATE: "Day",
            },
            {
                CONST.CONF_START: "16:00",
                CONST.CONF_END: "24:00",
                CONST.CONF_RATE: "Night",
            },
        ]
        data[CONST.CONF_EXPORT_RATES] = [
            {CONST.CONF_NAME: "Night", CONST.CONF_EXPORT_CENTS: 0.0},
            {CONST.CONF_NAME: "Day", CONST.CONF_EXPORT_CENTS: 5.0},
        ]
        sensor = self.sensors(data).by_key("next_export_change")
        assert sensor.native_value is not None
        self.assertEqual(sensor.native_value.astimezone(BRISBANE).hour, 16)
        self.assertTrue(sensor.available)

    def _capped(self, **extra: Any) -> dict[str, Any]:
        data = options(**extra)
        data[CONST.CONF_RATES][0][CONST.CONF_RATE_ALLOWANCE_KWH] = 24.0
        data[CONST.CONF_RATES][0][CONST.CONF_FALLBACK_RATE] = "Every day Peak"
        return data

    def test_a_cap_alone_does_not_start_counting(self) -> None:
        """Declaring the cap and counting against it are separate things."""
        data = self._capped(**{CONST.CONF_IMPORT_ENERGY_SENSOR: "sensor.grid"})
        added = self.sensors(data)
        self.assertFalse(self.coordinator.counting_allowance)
        self.assertNotIn(
            "allowance_remaining", {entity._key for entity in added.entities}
        )

    def test_with_counting_off_the_price_is_the_scheduled_one(self) -> None:
        """Never the fallback, because nothing here knows the cap is spent."""
        data = self._capped()
        sensor = self.sensors(data).by_key("import_price")
        self.assertAlmostEqual(sensor.native_value, 0.198)
        self.assertFalse(sensor.extra_state_attributes["allowance_counted"])
        self.assertFalse(sensor.extra_state_attributes["allowance_exhausted"])

    def test_with_counting_on_the_price_says_so(self) -> None:
        data = self._capped(
            **{
                CONST.CONF_IMPORT_ENERGY_SENSOR: "sensor.grid",
                CONST.CONF_COUNT_ALLOWANCE: True,
            }
        )
        sensor = self.sensors(data).by_key("import_price")
        self.assertTrue(sensor.extra_state_attributes["allowance_counted"])

    def test_counting_needs_a_meter_as_well_as_the_switch(self) -> None:
        data = self._capped(**{CONST.CONF_COUNT_ALLOWANCE: True})
        self.sensors(data)
        self.assertFalse(self.coordinator.counting_allowance)

    def test_no_allowance_sensor_without_an_energy_entity(self) -> None:
        keys = {entity._key for entity in self.sensors().entities}
        self.assertNotIn("allowance_remaining", keys)

    def test_the_allowance_sensor_appears_when_configured(self) -> None:
        data = options(
            **{
                CONST.CONF_IMPORT_ENERGY_SENSOR: "sensor.grid",
                CONST.CONF_COUNT_ALLOWANCE: True,
            }
        )
        data[CONST.CONF_RATES][0][CONST.CONF_RATE_ALLOWANCE_KWH] = 24.0
        data[CONST.CONF_RATES][0][CONST.CONF_FALLBACK_RATE] = "Every day Peak"
        sensor = self.sensors(data).by_key("allowance_remaining")
        self.assertAlmostEqual(sensor.native_value, 24.0)
        self.assertTrue(sensor.available)

    def test_the_allowance_is_restored_across_a_restart(self) -> None:
        data = options(
            **{
                CONST.CONF_IMPORT_ENERGY_SENSOR: "sensor.grid",
                CONST.CONF_COUNT_ALLOWANCE: True,
            }
        )
        data[CONST.CONF_RATES][0][CONST.CONF_RATE_ALLOWANCE_KWH] = 24.0
        data[CONST.CONF_RATES][0][CONST.CONF_FALLBACK_RATE] = "Every day Peak"
        sensor = self.sensors(data).by_key("allowance_remaining")
        sensor.hass = self.coordinator.hass
        module = _ha_stubs.sys.modules["homeassistant.components.sensor"]
        module.RestoreSensor.RESTORED = types.SimpleNamespace(native_value=9.0)
        # Recorded in the slot the component has come back into.
        module.RestoreSensor.RESTORED_STATE = types.SimpleNamespace(
            attributes={
                CONST.ATTR_ALLOWANCE_SLOT: self.coordinator.state.allowance_slot
            }
        )
        run(sensor.async_added_to_hass())
        self.assertAlmostEqual(self.coordinator.state.allowance_used_kwh, 15.0)
        module.RestoreSensor.RESTORED = None
        module.RestoreSensor.RESTORED_STATE = None

    def test_a_count_from_another_slot_is_not_restored(self) -> None:
        """The allowance is the slot's. A figure from another one says nothing."""
        data = options(
            **{
                CONST.CONF_IMPORT_ENERGY_SENSOR: "sensor.grid",
                CONST.CONF_COUNT_ALLOWANCE: True,
            }
        )
        data[CONST.CONF_RATES][0][CONST.CONF_RATE_ALLOWANCE_KWH] = 24.0
        data[CONST.CONF_RATES][0][CONST.CONF_FALLBACK_RATE] = "Every day Peak"
        sensor = self.sensors(data).by_key("allowance_remaining")
        sensor.hass = self.coordinator.hass
        module = _ha_stubs.sys.modules["homeassistant.components.sensor"]
        module.RestoreSensor.RESTORED = types.SimpleNamespace(native_value=9.0)
        module.RestoreSensor.RESTORED_STATE = types.SimpleNamespace(
            attributes={CONST.ATTR_ALLOWANCE_SLOT: "some.other@0-60/2001-01-01"}
        )
        run(sensor.async_added_to_hass())
        self.assertEqual(self.coordinator.state.allowance_used_kwh, 0.0)
        module.RestoreSensor.RESTORED = None
        module.RestoreSensor.RESTORED_STATE = None

    def test_the_supply_charge_is_declared_and_never_accumulated(self) -> None:
        """The charge is a statement of what it is. A total is the consumer's."""
        keys = {entity._key for entity in self.sensors().entities}
        self.assertIn("daily_supply_charge", keys)
        self.assertNotIn("supply_charge_today", keys)
        self.assertNotIn("supply_charge_energy", keys)


class TestBinarySensorPlatform(PlatformCase):
    def _added(self) -> Added:
        run(BINARY.async_setup_entry(self.coordinator.hass, self.entry, self.added))
        return self.added

    def test_one_per_declared_constraint(self) -> None:
        keys = {entity._key for entity in self._added().entities}
        self.assertEqual(
            keys, {"constraint_grid_charge_battery", "constraint_no_grid_import"}
        )

    def test_on_only_while_its_rate_is_in_force(self) -> None:
        added = self._added()
        charge = added.by_key("constraint_grid_charge_battery")
        peak = added.by_key("constraint_no_grid_import")
        self.assertTrue(charge.is_on)
        self.assertFalse(peak.is_on)

        COORD.dt_util.NOW = datetime(2026, 8, 14, 17, 0, tzinfo=BRISBANE)
        self.coordinator.async_refresh()
        self.assertFalse(charge.is_on)
        self.assertTrue(peak.is_on)

    def test_attributes_name_the_period(self) -> None:
        charge = self._added().by_key("constraint_grid_charge_battery")
        attributes = charge.extra_state_attributes
        self.assertEqual(attributes["constraint"], "grid_charge_battery")
        self.assertEqual(attributes["period_end"], "16:00")

    def test_attributes_are_bare_when_off(self) -> None:
        peak = self._added().by_key("constraint_no_grid_import")
        self.assertEqual(
            peak.extra_state_attributes,
            {"constraint": "no_grid_import", "enforceable": False},
        )

    def test_the_rule_says_whether_it_is_enforceable(self) -> None:
        """A declaration about what the rate means. Nothing is enforced here."""
        data = options()
        data[CONST.CONF_RATES][1][CONST.CONF_ENFORCEABLE_CONSTRAINTS] = [
            "no_grid_import"
        ]
        self.coordinator = a_coordinator(data)
        self.entry.runtime_data = self.coordinator
        added = self._added()
        peak = added.by_key("constraint_no_grid_import")
        charge = added.by_key("constraint_grid_charge_battery")

        COORD.dt_util.NOW = datetime(2026, 8, 14, 17, 0, tzinfo=BRISBANE)
        self.coordinator.async_refresh()
        self.assertTrue(peak.is_on)
        self.assertTrue(peak.extra_state_attributes["enforceable"])
        self.assertFalse(charge.extra_state_attributes["enforceable"])

    def test_the_name_is_readable(self) -> None:
        charge = self._added().by_key("constraint_grid_charge_battery")
        self.assertEqual(charge._attr_name, "Grid charge battery")
        self.assertIsNone(charge._attr_translation_key)

    def test_the_demand_sensor_is_absent_when_no_rate_has_one(self) -> None:
        """A plan with no demand charge gets no entity that is permanently off."""
        keys = {entity._key for entity in self._added().entities}
        self.assertNotIn("demand_period_active", keys)

    def test_the_demand_sensor_appears_when_a_rate_has_one(self) -> None:
        data = options()
        data[CONST.CONF_RATES][1][CONST.CONF_DEMAND_PERIOD] = True
        data[CONST.CONF_RATES][1][CONST.CONF_DEMAND_RATE] = 18.4
        self.coordinator = a_coordinator(data)
        self.entry.runtime_data = self.coordinator
        keys = {entity._key for entity in self._added().entities}
        self.assertIn("demand_period_active", keys)

    def test_the_demand_sensor_is_on_only_while_its_rate_is_in_force(self) -> None:
        data = options()
        data[CONST.CONF_RATES][1][CONST.CONF_DEMAND_PERIOD] = True
        data[CONST.CONF_RATES][1][CONST.CONF_DEMAND_RATE] = 18.4
        self.coordinator = a_coordinator(data)
        self.entry.runtime_data = self.coordinator
        demand = self._added().by_key("demand_period_active")

        # "Every day Off Peak" is in force at setup (COORD.dt_util.NOW default).
        self.assertFalse(demand.is_on)
        self.assertIsNone(demand.extra_state_attributes["demand_rate_per_kw_month"])

        COORD.dt_util.NOW = datetime(2026, 8, 14, 17, 0, tzinfo=BRISBANE)
        self.coordinator.async_refresh()
        self.assertTrue(demand.is_on)
        self.assertAlmostEqual(
            demand.extra_state_attributes["demand_rate_per_kw_month"], 18.4
        )


class TestSetupAndUnload(PlatformCase):
    def setUp(self) -> None:
        super().setUp()
        self.forwarded: list[Any] = []
        issues = sys.modules["homeassistant.helpers.issue_registry"]
        issues.RAISED.clear()
        issues.DELETED.clear()

        class FakeConfigEntries:
            def __init__(self, outer: Any) -> None:
                self.outer = outer

            async def async_forward_entry_setups(
                self, entry: Any, platforms: Any
            ) -> None:
                self.outer.forwarded.append(list(platforms))

            async def async_unload_platforms(self, entry: Any, platforms: Any) -> bool:
                return True

            def async_get_entry(self, entry_id: str) -> Any:
                return self.outer.entry if entry_id == "entry1" else None

            def async_update_entry(self, entry: Any, **kwargs: Any) -> None:
                for key, value in kwargs.items():
                    setattr(entry, key, value)

        self.coordinator.hass.config_entries = FakeConfigEntries(self)

    def test_setup_creates_the_coordinator_and_forwards_platforms(self) -> None:
        entry = FakeEntry()
        hass = self.coordinator.hass
        self.assertTrue(run(PKG.async_setup_entry(hass, entry)))
        self.assertIsNotNone(entry.runtime_data)
        self.assertEqual(self.forwarded, [["binary_sensor", "sensor"]])

    def test_a_valid_plan_clears_the_repair_issue(self) -> None:
        run(PKG.async_setup_entry(self.coordinator.hass, FakeEntry()))
        issues = sys.modules["homeassistant.helpers.issue_registry"]
        self.assertEqual(issues.RAISED, [])
        self.assertEqual(issues.DELETED, ["invalid_plan_entry1"])

    def test_an_invalid_plan_raises_a_repair_issue_and_still_sets_up(self) -> None:
        broken = options()
        broken[CONST.CONF_DAY_PATTERNS][0][CONST.CONF_PERIODS].pop()
        entry = FakeEntry(broken)
        self.assertTrue(run(PKG.async_setup_entry(self.coordinator.hass, entry)))
        issues = sys.modules["homeassistant.helpers.issue_registry"]
        self.assertEqual(len(issues.RAISED), 1)
        self.assertIn("problems", issues.RAISED[0][1]["translation_placeholders"])

    def test_an_unreadable_plan_defers_setup(self) -> None:
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        entry = FakeEntry({CONST.CONF_RATES: [{CONST.CONF_IMPORT_CENTS: 1}]})
        not_ready = sys.modules["homeassistant.exceptions"].ConfigEntryNotReady
        with self.assertRaises(not_ready):
            run(PKG.async_setup_entry(self.coordinator.hass, entry))

    def test_unload_shuts_the_coordinator_down(self) -> None:
        entry = FakeEntry()
        run(PKG.async_setup_entry(self.coordinator.hass, entry))
        self.assertTrue(run(PKG.async_unload_entry(self.coordinator.hass, entry)))


class TestTheAction(PlatformCase):
    def setUp(self) -> None:
        super().setUp()
        self.registered: dict[str, Any] = {}
        outer = self

        class FakeServices(_ha_stubs.FakeServices):
            def async_register(
                self, domain: str, service: str, handler: Any, **kwargs: Any
            ) -> None:
                outer.registered[service] = handler

        self.coordinator.hass.services = FakeServices()

        class FakeConfigEntries:
            def async_get_entry(self, entry_id: str) -> Any:
                return outer.entry if entry_id == "entry1" else None

        self.coordinator.hass.config_entries = FakeConfigEntries()
        run(PKG.async_setup(self.coordinator.hass, {}))

    def _call(self, **data: Any) -> Any:
        payload = {CONST.ATTR_HOURS: 6, CONST.ATTR_RESOLUTION_MINUTES: 30}
        payload.update(data)
        return run(
            self.registered[CONST.SERVICE_GET_INTERVALS](
                types.SimpleNamespace(data=payload)
            )
        )

    def test_the_action_is_registered(self) -> None:
        self.assertIn(CONST.SERVICE_GET_INTERVALS, self.registered)

    def test_it_returns_the_forward_series(self) -> None:
        response = self._call(**{CONST.ATTR_CONFIG_ENTRY_ID: "entry1"})
        self.assertEqual(len(response["intervals"]), 12)
        self.assertEqual(response["intervals"][0]["rate"], "Every day Off Peak")

    def test_an_unknown_entry_is_a_validation_error(self) -> None:
        error = sys.modules["homeassistant.exceptions"].ServiceValidationError
        with self.assertRaises(error):
            self._call(**{CONST.ATTR_CONFIG_ENTRY_ID: "nope"})

    def test_an_unloaded_entry_is_a_validation_error(self) -> None:
        self.entry.runtime_data = None
        error = sys.modules["homeassistant.exceptions"].ServiceValidationError
        with self.assertRaises(error):
            self._call(**{CONST.ATTR_CONFIG_ENTRY_ID: "entry1"})


class TestDiagnostics(PlatformCase):
    def test_everything_needed_to_reason_offline(self) -> None:
        payload = run(
            DIAG.async_get_config_entry_diagnostics(self.coordinator.hass, self.entry)
        )
        for key in (
            "plan",
            "rates_csv",
            "periods_csv",
            "strip",
            "rate_plan_card",
            "problems",
            "current",
            "options",
            "intervals",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["problems"], [])
        self.assertEqual(payload["current"]["rate"], "Every day Off Peak")
        self.assertNotIn("rates", payload["options"])


if __name__ == "__main__":
    unittest.main()
