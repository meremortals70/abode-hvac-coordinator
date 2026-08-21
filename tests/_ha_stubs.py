"""Enough of Home Assistant to import and drive the config flow.

The config flow is the largest file in the component and had no test coverage,
which is why every regression landed there. Installing Home Assistant is not
possible here, so the handful of APIs the flow touches are stubbed and the flow
is stepped through exactly as the frontend would step through it.

The stubs are deliberately thin. They implement behaviour the flow relies on -
schema validation, defaults, the shape of a flow result - and nothing else.
"""

from __future__ import annotations

import contextlib
import re
import sys
import types
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import voluptuous as vol


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


class _Selector:
    """A selector that validates nothing and remembers its config."""

    def __init__(self, config: Any = None) -> None:
        self.config = config

    def __call__(self, value: Any) -> Any:
        return value


class _Config:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)


class Section:
    """A collapsible group of fields, as data_entry_flow.section builds one."""

    def __init__(self, schema: Any, options: Any = None) -> None:
        self.schema = schema
        self.options = options or {}

    def __call__(self, value: Any) -> Any:
        return value


class _Mode:
    BOX = "box"
    SLIDER = "slider"
    LIST = "list"
    DROPDOWN = "dropdown"


class FlowResultType:
    FORM = "form"
    MENU = "menu"
    CREATE_ENTRY = "create_entry"
    ABORT = "abort"


class AbortFlow(Exception):
    """Raised by _abort_if_unique_id_configured."""


class _BaseFlow:
    """The parts of FlowHandler the config and options flows actually use."""

    hass: Any = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        kwargs.pop("domain", None)
        super().__init_subclass__(**kwargs)

    def async_show_form(
        self,
        *,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
        description_placeholders: dict[str, str] | None = None,
        last_step: bool | None = None,
    ) -> dict[str, Any]:
        return {
            "type": FlowResultType.FORM,
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
            "description_placeholders": description_placeholders or {},
        }

    def async_show_menu(
        self,
        *,
        step_id: str,
        menu_options: Any,
        description_placeholders: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": FlowResultType.MENU,
            "step_id": step_id,
            "menu_options": list(menu_options),
            "description_placeholders": description_placeholders or {},
        }

    def async_create_entry(
        self,
        *,
        title: str = "",
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": FlowResultType.CREATE_ENTRY,
            "title": title,
            "data": data or {},
            "options": options or {},
        }

    def async_abort(self, *, reason: str) -> dict[str, Any]:
        return {"type": FlowResultType.ABORT, "reason": reason}

    async def async_set_unique_id(self, unique_id: str) -> None:
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self) -> None:
        return None


class FakeState:
    """A state object with the two attributes the coordinator reads."""

    def __init__(self, state: Any, attributes: dict[str, Any] | None = None) -> None:
        self.state = state
        self.attributes = attributes or {}


class FakeStates:
    def __init__(self) -> None:
        self._states: dict[str, FakeState] = {}

    def set(self, entity_id: str, state: Any, **attributes: Any) -> None:
        self._states[entity_id] = FakeState(state, attributes)

    def get(self, entity_id: str) -> FakeState | None:
        return self._states.get(entity_id)


class FakeServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def async_call(
        self, domain: str, service: str, data: dict[str, Any], blocking: bool = False
    ) -> None:
        self.calls.append((domain, service, data))


class FakeConfig:
    currency = "AUD"
    time_zone = "Australia/Brisbane"


class FakeHass:
    """Enough of HomeAssistant for the coordinator and the entities."""

    def __init__(self) -> None:
        self.states = FakeStates()
        self.services = FakeServices()
        self.config = FakeConfig()
        self.tasks: list[Any] = []

    def async_create_task(self, coro: Any) -> None:
        # Run it now; nothing here awaits anything real.
        with contextlib.suppress(StopIteration):
            coro.send(None)
        self.tasks.append(coro)


class Scheduled:
    """Records what was scheduled instead of scheduling it."""

    def __init__(self) -> None:
        self.points: list[tuple[Any, Any]] = []
        self.time_changes: list[dict[str, Any]] = []
        self.state_listeners: list[tuple[list[str], Any]] = []
        self.dispatched: list[str] = []
        self.cancelled = 0

    def cancel(self) -> None:
        self.cancelled += 1


SCHEDULED = Scheduled()


def install() -> None:
    """Put the stubs into sys.modules. Safe to call more than once."""
    if "homeassistant" in sys.modules:
        return

    _module("homeassistant")

    entries = _module("homeassistant.config_entries")
    entries.SOURCE_USER = "user"
    entries.ConfigEntry = type("ConfigEntry", (), {})
    entries.ConfigFlow = type("ConfigFlow", (_BaseFlow,), {})
    entries.OptionsFlow = type("OptionsFlow", (_BaseFlow,), {})
    entries.ConfigFlowResult = dict
    entries.AbortFlow = AbortFlow

    core = _module("homeassistant.core")
    core.callback = lambda func: func
    core.HomeAssistant = type("HomeAssistant", (), {})

    helpers = _module("homeassistant.helpers")
    helpers.__path__ = []  # type: ignore[attr-defined]

    selector = _module("homeassistant.helpers.selector")
    for name in (
        "TextSelector",
        "NumberSelector",
        "BooleanSelector",
        "SelectSelector",
        "TimeSelector",
        "DateSelector",
        "EntitySelector",
    ):
        setattr(selector, name, type(name, (_Selector,), {}))
    for name in (
        "TextSelectorConfig",
        "NumberSelectorConfig",
        "SelectSelectorConfig",
        "EntitySelectorConfig",
    ):
        setattr(selector, name, type(name, (_Config,), {}))
    selector.NumberSelectorMode = _Mode
    selector.SelectSelectorMode = _Mode

    util = _module("homeassistant.util")
    util.__path__ = []  # type: ignore[attr-defined]
    util.slugify = lambda value: re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip(
        "_"
    )

    data_entry_flow = _module("homeassistant.data_entry_flow")
    data_entry_flow.FlowResultType = FlowResultType
    data_entry_flow.section = Section

    const = _module("homeassistant.const")
    const.STATE_ON = "on"
    const.STATE_OFF = "off"
    const.STATE_UNAVAILABLE = "unavailable"
    const.STATE_UNKNOWN = "unknown"

    class _UnitOfEnergy:
        KILO_WATT_HOUR = "kWh"

    const.UnitOfEnergy = _UnitOfEnergy

    class _Platform:
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"

    const.Platform = _Platform

    core.CALLBACK_TYPE = object
    core.Event = dict
    core.EventStateChangedData = dict
    core.ServiceCall = object
    core.ServiceResponse = dict
    core.SupportsResponse = type("SupportsResponse", (), {"ONLY": "only"})

    dispatcher = _module("homeassistant.helpers.dispatcher")

    def _send(hass: Any, signal: str, *args: Any) -> None:
        SCHEDULED.dispatched.append(signal)
        for registered, callback_fn in getattr(hass, "_signals", {}).items():
            if registered == signal:
                for fn in callback_fn:
                    fn()

    def _connect(hass: Any, signal: str, target: Any) -> Any:
        hass._signals = getattr(hass, "_signals", {})
        hass._signals.setdefault(signal, []).append(target)
        return lambda: None

    dispatcher.async_dispatcher_send = _send
    dispatcher.async_dispatcher_connect = _connect

    event = _module("homeassistant.helpers.event")

    def _track_point(hass: Any, action: Any, moment: Any) -> Any:
        SCHEDULED.points.append((moment, action))
        return SCHEDULED.cancel

    def _track_state(hass: Any, entities: Any, action: Any) -> Any:
        SCHEDULED.state_listeners.append((list(entities), action))
        return lambda: None

    def _track_time(hass: Any, action: Any, **kwargs: Any) -> Any:
        SCHEDULED.time_changes.append({"action": action, **kwargs})
        return lambda: None

    event.async_track_point_in_time = _track_point
    event.async_track_state_change_event = _track_state
    event.async_track_time_change = _track_time

    dt_util = _module("homeassistant.util.dt")
    dt_util.DEFAULT_ZONE = ZoneInfo("Australia/Brisbane")
    dt_util.get_default_time_zone = lambda: dt_util.DEFAULT_ZONE
    dt_util.NOW = None

    def _now() -> Any:
        return dt_util.NOW or datetime.now(dt_util.DEFAULT_ZONE)

    dt_util.now = _now
    util.dt = dt_util

    registry = _module("homeassistant.helpers.device_registry")
    registry.DeviceEntryType = type("DeviceEntryType", (), {"SERVICE": "service"})
    registry.DeviceInfo = dict

    entity_mod = _module("homeassistant.helpers.entity")

    class _Entity:
        hass: Any = None
        _attr_should_poll = True

        async def async_added_to_hass(self) -> None:
            return None

        def async_on_remove(self, func: Any) -> None:
            return None

        def async_write_ha_state(self) -> None:
            return None

    entity_mod.Entity = _Entity

    components = _module("homeassistant.components")
    components.__path__ = []  # type: ignore[attr-defined]

    sensor_mod = _module("homeassistant.components.sensor")

    class _SensorEntity(_Entity):
        _attr_native_unit_of_measurement: Any = None
        _attr_native_value: Any = None

    class _RestoreSensor(_SensorEntity):
        RESTORED: Any = None
        RESTORED_STATE: Any = None

        async def async_get_last_sensor_data(self) -> Any:
            return _RestoreSensor.RESTORED

        async def async_get_last_state(self) -> Any:
            return _RestoreSensor.RESTORED_STATE

    sensor_mod.SensorEntity = _SensorEntity
    sensor_mod.RestoreSensor = _RestoreSensor
    sensor_mod.SensorDeviceClass = type(
        "SensorDeviceClass",
        (),
        {
            "ENUM": "enum",
            "TIMESTAMP": "timestamp",
            "MONETARY": "monetary",
            "ENERGY": "energy",
            "ENERGY_STORAGE": "energy_storage",
        },
    )
    sensor_mod.SensorStateClass = type(
        "SensorStateClass",
        (),
        {"MEASUREMENT": "measurement", "TOTAL_INCREASING": "total_increasing"},
    )

    binary_mod = _module("homeassistant.components.binary_sensor")
    binary_mod.BinarySensorEntity = type("BinarySensorEntity", (_Entity,), {})

    platform = _module("homeassistant.helpers.entity_platform")
    platform.AddConfigEntryEntitiesCallback = object

    typing_mod = _module("homeassistant.helpers.typing")
    typing_mod.ConfigType = dict

    exceptions = _module("homeassistant.exceptions")

    class _HomeAssistantError(Exception):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args)
            self.kwargs = kwargs

    exceptions.HomeAssistantError = _HomeAssistantError
    exceptions.ConfigEntryNotReady = type(
        "ConfigEntryNotReady", (_HomeAssistantError,), {}
    )
    exceptions.ServiceValidationError = type(
        "ServiceValidationError", (_HomeAssistantError,), {}
    )

    cv = _module("homeassistant.helpers.config_validation")
    cv.string = str
    cv.config_entry_only_config_schema = lambda domain: {}

    issues = _module("homeassistant.helpers.issue_registry")
    issues.RAISED = []
    issues.DELETED = []
    issues.IssueSeverity = type("IssueSeverity", (), {"ERROR": "error"})
    issues.async_create_issue = lambda hass, domain, issue_id, **kw: (
        issues.RAISED.append((issue_id, kw))
    )
    issues.async_delete_issue = lambda hass, domain, issue_id: issues.DELETED.append(
        issue_id
    )


def defaults(result: dict[str, Any]) -> dict[str, Any]:
    """Return what the frontend would submit if nothing were changed.

    Every field on every screen carries a default, so this is the 'press Submit'
    case and it must always be valid.
    """
    return _defaults_for(result["data_schema"])


def _defaults_for(schema: vol.Schema) -> dict[str, Any]:
    submitted: dict[str, Any] = {}
    for key, value in schema.schema.items():
        name = getattr(key, "schema", key)
        if isinstance(value, Section):
            submitted[name] = _defaults_for(value.schema)
            continue
        default = getattr(key, "default", None)
        if default is not None and default is not vol.UNDEFINED:
            try:
                submitted[name] = default()
            except TypeError:
                submitted[name] = default
    return submitted


def options_for(result: dict[str, Any], field: str) -> list[str]:
    """Return the choices a select field offers on a form, sections included."""
    found = _options_in(result["data_schema"], field)
    if found is None:
        raise AssertionError(f"no field {field!r} on this form")
    return found


def _options_in(schema: vol.Schema, field: str) -> list[str] | None:
    for key, value in schema.schema.items():
        if isinstance(value, Section):
            nested = _options_in(value.schema, field)
            if nested is not None:
                return nested
            continue
        if getattr(key, "schema", key) != field:
            continue
        config = getattr(value, "config", None)
        return [str(option) for option in getattr(config, "options", [])]
    return None


def field_names(form: Any) -> set[str]:
    """Return every field on a form or schema, flattened out of its sections."""
    schema = form["data_schema"] if isinstance(form, dict) else form
    return _field_names_in(schema)


def field_for(form: Any, field: str) -> Any:
    """Return the marker and selector for one field, sections included."""
    schema = form["data_schema"] if isinstance(form, dict) else form
    found = _field_in(schema, field)
    if found is None:
        raise AssertionError(f"no field {field!r} on this form")
    return found


def _field_in(schema: vol.Schema, field: str) -> Any:
    for key, value in schema.schema.items():
        if isinstance(value, Section):
            nested = _field_in(value.schema, field)
            if nested is not None:
                return nested
            continue
        if getattr(key, "schema", key) == field:
            return key, value
    return None


def _field_names_in(schema: vol.Schema) -> set[str]:
    names: set[str] = set()
    for key, value in schema.schema.items():
        if isinstance(value, Section):
            names |= _field_names_in(value.schema)
            continue
        names.add(str(getattr(key, "schema", key)))
    return names
