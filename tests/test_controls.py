"""0.9.0: the room's controls, its stored profile, the vane switch, the command
log, setpoint rounding, the opening grace and the weather coast, against a real
Home Assistant.

Each test says which decision record it belongs to. The pure decisions are in
`test_core.py`; this file is what only a running Home Assistant can show.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun import freeze_time
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.abode_hvac_coordinator.actuator import COMMAND_LOG_SIZE
from custom_components.abode_hvac_coordinator.const import (
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_TARGETS,
    CONF_CLIMATE_ENTITIES,
    CONF_OPENING_GRACE,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_ROOMS,
)
from custom_components.abode_hvac_coordinator.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.abode_hvac_coordinator.thermal import Coefficient

_FEATURES = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.SWING_MODE
)

_MODE = "select.test_room_mode"
_FAN = "select.test_room_fan_speed"
_VANE = "select.test_room_vertical_vane"
_HORIZONTAL = "select.test_room_horizontal_vane"
_SETPOINT = "number.test_room_setpoint"
_AUTOMATIC = "switch.test_room_automatic_control"
_VANES = "switch.test_room_automatic_vane_control"


def _unit(state: str = "cool", **overrides: object) -> dict[str, object]:
    """What a unit that offers fan speeds and a vertical vane reports."""
    return {
        "hvac_modes": ["off", "cool", "heat", "dry", "fan_only"],
        "hvac_action": "cooling" if state == "cool" else "idle",
        "fan_modes": ["auto", "low", "medium", "high"],
        "swing_modes": ["off", "auto", "up", "down"],
        "min_temp": 18.0,
        "max_temp": 30.0,
        "target_temp_step": 1.0,
        "supported_features": _FEATURES.value,
        **overrides,
    }


def _publish(hass: HomeAssistant, entity_id: str = "climate.test", state: str = "cool", **extra: object) -> None:
    hass.states.async_set(entity_id, state, _unit(state, **extra))


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    temperature: str = "32.0",
    humidity: str = "60.0",
    room_extra: dict | None = None,
    options_extra: dict | None = None,
    unit: bool = True,
) -> object:
    """Set a room up and let occupancy settle, so it is actually evaluated."""
    room = {**entry.options[CONF_ROOMS][0], **(room_extra or {})}
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_ROOMS: [room], **(options_extra or {})},
    )
    hass.states.async_set("sensor.test_temperature", temperature)
    hass.states.async_set("sensor.test_humidity", humidity)
    hass.states.async_set("binary_sensor.test_presence", "on")
    for entity_id in room[CONF_CLIMATE_ENTITIES]:
        if unit:
            _publish(hass, entity_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await _settle(hass, coordinator)
    return coordinator


async def _settle(hass: HomeAssistant, coordinator: object, minutes: int = 5) -> None:
    with freeze_time(dt_util.utcnow() + timedelta(minutes=minutes)):
        await coordinator.async_refresh()  # type: ignore[attr-defined]
        await hass.async_block_till_done()


async def _flip(hass: HomeAssistant, entity_id: str, service: str) -> None:
    domain = entity_id.split(".")[0]
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def _select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: entity_id, "option": option},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: entity_id, "value": value},
        blocking=True,
    )
    await hass.async_block_till_done()


def _two_heads(entry: MockConfigEntry) -> dict:
    return {CONF_CLIMATE_ENTITIES: ["climate.test", "climate.test_two"]}


# ---------------------------------------------------------------------------
# DR-051 - the commanded setpoint is rounded to what the unit can hold
# ---------------------------------------------------------------------------


async def test_a_setpoint_is_sent_in_whole_degrees_to_a_unit_that_holds_whole_degrees(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The Office Aircon advertises a step of 1.0. The component sent 22.8,
    22.9 and 22.2 and the unit kept 22.0."""
    coordinator = await _setup(hass, mock_config_entry)
    temperature_calls = async_mock_service(hass, "climate", "set_temperature")
    _publish(hass, state="off")
    with freeze_time(dt_util.utcnow() + timedelta(minutes=20)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    sent = [call.data["temperature"] for call in temperature_calls]
    assert sent, "a hot room was sent no setpoint at all"
    assert all(value == round(value) for value in sent), sent
    state = hass.states.get("sensor.test_room_commanded_setpoint")
    assert state is not None
    assert float(state.state) == round(float(state.state))


async def test_a_setpoint_the_unit_holds_is_not_sent_again(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The loop that was the incident: sent, reverted, noticed, sent again.
    Once what is sent is what the unit can hold, it stays sent."""
    coordinator = await _setup(hass, mock_config_entry)
    commanded = hass.states.get("sensor.test_room_commanded_setpoint")
    assert commanded is not None
    held = float(commanded.state)
    assert held == round(held)

    _publish(hass, temperature=held)
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    temperature_calls = async_mock_service(hass, "climate", "set_temperature")
    for seconds in (30, 60, 90):
        with freeze_time(dt_util.utcnow() + timedelta(seconds=seconds)):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
    assert not mode_calls and not temperature_calls, (
        [c.data for c in mode_calls],
        [c.data for c in temperature_calls],
    )


async def test_the_trace_says_what_it_rounded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["setpoint_step_c"] == 1.0


async def test_a_unit_that_advertises_no_step_is_rounded_to_tenths(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    attributes = _unit()
    del attributes["target_temp_step"]
    hass.states.async_set("climate.test", "cool", attributes)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await _settle(hass, mock_config_entry.runtime_data)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["setpoint_step_c"] is None
    commanded = state.attributes["commanded_dry_bulb_c"]
    assert commanded == round(commanded, 1)


# ---------------------------------------------------------------------------
# DR-052 - every command sent is recorded
# ---------------------------------------------------------------------------


async def test_a_command_sent_is_recorded_with_what_it_was_decided_from(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    _publish(hass, state="off")
    with freeze_time(dt_util.utcnow() + timedelta(minutes=20)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    log = list(coordinator.actuator.command_log)
    assert log, "nothing recorded"
    entry = log[-1]
    assert entry["room"] == "test_room"
    assert entry["entity"] == "climate.test"
    assert entry["source"] == "coordinator"
    assert entry["demand"] == "cool"
    assert entry["room_c"] == 32.0
    assert entry["target_c"] is not None
    assert entry["at"]


async def test_a_command_is_written_to_the_home_assistant_log(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    caplog.clear()
    _publish(hass, state="off")
    with freeze_time(dt_util.utcnow() + timedelta(minutes=20)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert any(
        record.levelname == "INFO" and "Sent test_room to climate.test" in record.message
        for record in caplog.records
    )


async def test_an_evaluation_that_sends_nothing_records_nothing(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(
        hass, mock_config_entry, temperature="25.5", humidity="45.0"
    )
    before = len(coordinator.actuator.command_log)
    await _settle(hass, coordinator, minutes=1)
    assert len(coordinator.actuator.command_log) == before


async def test_the_log_is_bounded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    room = coordinator.rooms["test_room"]
    for index in range(COMMAND_LOG_SIZE + 50):
        coordinator.actuator._record_command(room, "climate.test", "cool", 23.0, None)
    assert len(coordinator.actuator.command_log) == COMMAND_LOG_SIZE


async def test_the_log_is_in_the_diagnostics_download(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    coordinator.actuator._record_command(
        coordinator.rooms["test_room"], "climate.test", "cool", 23.0, None
    )
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diagnostics["commands"], diagnostics["commands"]
    assert diagnostics["commands"][-1]["source"] == "user"
    room = diagnostics["rooms"]["test_room"]
    assert room["automatic_control"] is True
    assert room["automatic_vane_control"] is True
    assert room["capabilities"]["hvac_modes"] == ["off", "cool", "heat", "dry", "fan_only"]
    assert room["opening_grace_minutes"] == 5.0


# ---------------------------------------------------------------------------
# DR-048 - what the room's units can do
# ---------------------------------------------------------------------------


async def test_a_room_configured_before_0_9_is_read_on_first_load_and_kept(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The fixture room carries no profile, as a pre-0.9 room does."""
    assert "capabilities" not in mock_config_entry.options[CONF_ROOMS][0]
    coordinator = await _setup(hass, mock_config_entry)
    profile = coordinator.rooms["test_room"].capabilities
    assert profile is not None
    assert profile.fan_modes == ("auto", "low", "medium", "high")
    assert profile.target_temp_step == 1.0
    assert coordinator.store.profile("test_room") is not None


async def test_a_kept_profile_does_not_depend_on_the_unit_being_up(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """A restart with the unit down must not lose what was read."""
    hass_storage["abode_hvac_coordinator.model"] = {
        "version": 1,
        "minor_version": 1,
        "key": "abode_hvac_coordinator.model",
        "data": {
            "rooms": {},
            "groups": {},
            "switched_off": [],
            "manual_vanes": [],
            "profiles": {
                "test_room": {
                    "hvac_modes": ["off", "cool"],
                    "fan_modes": [],
                    "swing_modes": [],
                    "swing_horizontal_modes": [],
                    "min_temp": 18.0,
                    "max_temp": 30.0,
                    "target_temp_step": 1.0,
                    "single_target": True,
                    "range_target": False,
                }
            },
        },
    }
    coordinator = await _setup(hass, mock_config_entry, unit=False)
    assert coordinator.rooms["test_room"].capabilities is not None
    assert coordinator.rooms["test_room"].capabilities.hvac_modes == ("off", "cool")


async def test_a_room_whose_unit_has_not_reported_is_sent_nothing_and_says_why(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set("climate.test", "unavailable")
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data

    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    temperature_calls = async_mock_service(hass, "climate", "set_temperature")
    for minutes in (1, 10, 40):
        await _settle(hass, coordinator, minutes=minutes)

    assert not mode_calls and not temperature_calls
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert "waiting for the air conditioner" in " ".join(state.attributes["reasons"])
    assert coordinator.rooms["test_room"].capabilities is None
    # Named once, not every cycle.
    assert sum("has not reported what it can do" in r.message for r in caplog.records) == 1


async def test_a_room_starts_working_as_soon_as_its_unit_reports(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set("climate.test", "unavailable")
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data
    await _settle(hass, coordinator, minutes=5)
    assert coordinator.rooms["test_room"].capabilities is None

    _publish(hass, state="off")
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    await _settle(hass, coordinator, minutes=10)
    assert coordinator.rooms["test_room"].capabilities is not None
    assert mode_calls, "the room never started once its unit reported"


async def test_a_modes_list_comes_from_the_profile_not_from_a_live_lookup(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The unit later stops advertising cool for a moment. The stored profile
    still says it can, so the decision does not flap with the entity."""
    coordinator = await _setup(hass, mock_config_entry)
    assert coordinator.rooms["test_room"].capabilities is not None
    _publish(hass, state="cool", hvac_modes=["off"])
    await _settle(hass, coordinator, minutes=1)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "compressor", state.attributes


# ---------------------------------------------------------------------------
# DR-049 - the room's controls
# ---------------------------------------------------------------------------


async def test_the_controls_are_built_from_what_the_unit_offers(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    assert hass.states.get(_MODE) is not None
    assert hass.states.get(_FAN) is not None
    assert hass.states.get(_VANE) is not None
    assert hass.states.get(_SETPOINT) is not None
    # This unit offers no horizontal vane, so there is no control for one.
    assert hass.states.get(_HORIZONTAL) is None
    assert hass.states.get(_FAN).attributes["options"] == ["auto", "low", "medium", "high"]  # type: ignore[union-attr]
    assert hass.states.get(_SETPOINT).attributes["min"] == 18.0  # type: ignore[union-attr]
    assert hass.states.get(_SETPOINT).attributes["max"] == 30.0  # type: ignore[union-attr]
    assert hass.states.get(_SETPOINT).attributes["step"] == 1.0  # type: ignore[union-attr]


async def test_a_unit_with_a_horizontal_vane_gets_a_control_for_it(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set(
        "climate.test",
        "cool",
        _unit(
            swing_horizontal_modes=["off", "left", "right"],
            supported_features=(_FEATURES | ClimateEntityFeature.SWING_HORIZONTAL_MODE).value,
        ),
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(_HORIZONTAL)
    assert state is not None
    assert state.attributes["options"] == ["off", "left", "right"]


async def test_the_controls_show_the_units_live_state(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    _publish(hass, state="cool", fan_mode="medium", swing_mode="up", temperature=23.0)
    await hass.async_block_till_done()
    assert hass.states.get(_MODE).state == "cool"  # type: ignore[union-attr]
    assert hass.states.get(_FAN).state == "medium"  # type: ignore[union-attr]
    assert hass.states.get(_VANE).state == "up"  # type: ignore[union-attr]
    assert float(hass.states.get(_SETPOINT).state) == 23.0  # type: ignore[union-attr]
    # A change at the wall is shown, not hidden.
    _publish(hass, state="cool", fan_mode="low", swing_mode="down", temperature=21.0)
    await hass.async_block_till_done()
    assert hass.states.get(_FAN).state == "low"  # type: ignore[union-attr]
    assert hass.states.get(_VANE).state == "down"  # type: ignore[union-attr]
    assert float(hass.states.get(_SETPOINT).state) == 21.0  # type: ignore[union-attr]


async def test_the_controls_are_unavailable_while_the_unit_is_not_reporting(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    hass.states.async_set("climate.test", "unavailable")
    await hass.async_block_till_done()
    for entity_id in (_MODE, _FAN, _VANE, _SETPOINT):
        assert hass.states.get(entity_id).state == "unavailable", entity_id  # type: ignore[union-attr]


async def test_mode_fan_and_setpoint_refuse_changes_while_automatic_control_is_on(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    calls = []
    for service in ("set_hvac_mode", "set_fan_mode", "set_temperature", "set_swing_mode"):
        calls += async_mock_service(hass, "climate", service)
    with pytest.raises(ServiceValidationError) as error:
        await _select(hass, _MODE, "fan_only")
    assert error.value.translation_key == "control_not_writable"
    with pytest.raises(ServiceValidationError):
        await _select(hass, _FAN, "low")
    with pytest.raises(ServiceValidationError):
        await _number(hass, _SETPOINT, 21.0)
    assert not calls, [c.data for c in calls]


async def test_the_vanes_refuse_changes_while_automatic_vane_control_is_on(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    calls = async_mock_service(hass, "climate", "set_swing_mode")
    with pytest.raises(ServiceValidationError) as error:
        await _select(hass, _VANE, "up")
    assert error.value.translation_key == "vane_not_writable"
    assert not calls


async def test_everything_is_writable_once_automatic_control_is_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    await _flip(hass, _AUTOMATIC, "turn_off")
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    fan_calls = async_mock_service(hass, "climate", "set_fan_mode")
    swing_calls = async_mock_service(hass, "climate", "set_swing_mode")
    temperature_calls = async_mock_service(hass, "climate", "set_temperature")

    await _select(hass, _MODE, "fan_only")
    await _select(hass, _FAN, "low")
    await _select(hass, _VANE, "up")
    await _number(hass, _SETPOINT, 21.0)

    assert [c.data["hvac_mode"] for c in mode_calls] == ["fan_only"]
    assert [c.data["fan_mode"] for c in fan_calls] == ["low"]
    assert [c.data["swing_mode"] for c in swing_calls] == ["up"]
    assert [c.data["temperature"] for c in temperature_calls] == [21.0]


async def test_a_change_from_the_controls_goes_to_every_head_in_the_room(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(
        hass,
        mock_config_entry,
        room_extra=_two_heads(mock_config_entry),
        unit=False,
    )
    # Setup needs both heads reporting before the room can be read; publish
    # them and reload so the room is read with both.
    _publish(hass, "climate.test")
    _publish(hass, "climate.test_two")
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await _flip(hass, _AUTOMATIC, "turn_off")
    calls = async_mock_service(hass, "climate", "set_fan_mode")
    await _select(hass, _FAN, "high")
    assert sorted(c.data[ATTR_ENTITY_ID] for c in calls) == [
        "climate.test",
        "climate.test_two",
    ]


async def test_a_value_the_unit_never_offered_is_refused_with_a_reason(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    calls = async_mock_service(hass, "climate", "set_fan_mode")
    with pytest.raises(ServiceValidationError) as error:
        await coordinator.actuator.async_user_command(
            coordinator.rooms["test_room"], fan_mode="turbo"
        )
    assert error.value.translation_key == "control_value_not_offered"
    assert not calls


async def test_a_room_with_no_profile_refuses_its_controls_with_a_reason(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    bare = coordinator.rooms["test_room"].__class__(
        room_id="x", name="X", climate_entity_ids=("climate.x",), bands={}
    )
    with pytest.raises(ServiceValidationError) as error:
        await coordinator.actuator.async_user_command(bare, fan_mode="low")
    assert error.value.translation_key == "control_profile_not_read"


async def test_a_command_from_the_controls_is_recorded_as_the_users(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    await _flip(hass, _AUTOMATIC, "turn_off")
    async_mock_service(hass, "climate", "set_hvac_mode")
    await _select(hass, _MODE, "fan_only")
    entry = coordinator.actuator.command_log[-1]
    assert entry["source"] == "user"
    assert entry["hvac_mode"] == "fan_only"


async def test_the_controls_are_in_the_entity_registry_with_their_own_ids(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    registry = er.async_get(hass)
    for unique_id, domain in (
        ("test_room_unit_mode", "select"),
        ("test_room_fan_speed", "select"),
        ("test_room_vertical_vane", "select"),
        ("test_room_setpoint", "number"),
        ("test_room_automatic_vane_control", "switch"),
    ):
        assert registry.async_get_entity_id(domain, "abode_hvac_coordinator", unique_id), unique_id


# ---------------------------------------------------------------------------
# DR-049 - Automatic vane control
# ---------------------------------------------------------------------------


async def test_the_coordinator_positions_the_vane_while_automatic_vane_control_is_on(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    assert hass.states.get(_VANES).state == "on"  # type: ignore[union-attr]
    swing_calls = async_mock_service(hass, "climate", "set_swing_mode")
    _publish(hass, state="off")
    with freeze_time(dt_util.utcnow() + timedelta(minutes=20)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert swing_calls, "the coordinator never positioned the vane"


async def test_the_coordinator_never_moves_a_vane_once_automatic_vane_control_is_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    await _flip(hass, _VANES, "turn_off")
    swing_calls = async_mock_service(hass, "climate", "set_swing_mode")
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    _publish(hass, state="off")
    for minutes in (20, 40):
        with freeze_time(dt_util.utcnow() + timedelta(minutes=minutes)):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
    assert mode_calls, "the room itself stopped being run"
    assert not swing_calls, [c.data for c in swing_calls]


async def test_the_vane_is_the_users_while_the_rest_stays_automatic(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Whether people like air flowing over them is not the controller's call,
    and turning that over does not hand over the mode or the setpoint."""
    await _setup(hass, mock_config_entry)
    await _flip(hass, _VANES, "turn_off")
    swing_calls = async_mock_service(hass, "climate", "set_swing_mode")
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    assert hass.states.get(_AUTOMATIC).state == "on"  # type: ignore[union-attr]

    await _select(hass, _VANE, "down")
    assert [c.data["swing_mode"] for c in swing_calls] == ["down"]
    with pytest.raises(ServiceValidationError):
        await _select(hass, _MODE, "fan_only")
    assert not mode_calls


async def test_handing_the_vane_back_lets_the_coordinator_position_it_again(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    await _flip(hass, _VANES, "turn_off")
    await _flip(hass, _VANES, "turn_on")
    swing_calls = async_mock_service(hass, "climate", "set_swing_mode")
    _publish(hass, state="off")
    with freeze_time(dt_util.utcnow() + timedelta(minutes=20)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert swing_calls
    with pytest.raises(ServiceValidationError):
        await _select(hass, _VANE, "up")


async def test_the_vane_choice_is_written_to_the_store(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    await _flip(hass, _VANES, "turn_off")
    assert coordinator.store.manual_vanes() == {"test_room"}
    await _flip(hass, _VANES, "turn_on")
    assert coordinator.store.manual_vanes() == set()


async def test_the_vane_choice_survives_a_restart(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """The first evaluation after start-up must already know, or it would move
    a vane the user set by hand."""
    hass_storage["abode_hvac_coordinator.model"] = {
        "version": 1,
        "minor_version": 1,
        "key": "abode_hvac_coordinator.model",
        "data": {
            "rooms": {},
            "groups": {},
            "switched_off": [],
            "manual_vanes": ["test_room"],
        },
    }
    coordinator = await _setup(hass, mock_config_entry)
    assert coordinator.is_room_vanes_manual("test_room")
    assert hass.states.get(_VANES).state == "off"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# DR-049 - Automatic control off leaves the unit alone
# ---------------------------------------------------------------------------


async def test_turning_automatic_control_off_does_not_turn_the_unit_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    temperature_calls = async_mock_service(hass, "climate", "set_temperature")
    await _flip(hass, _AUTOMATIC, "turn_off")
    # The switch asks for a refresh, which is debounced; make sure at least one
    # evaluation of the switched-off room has actually run, and then several.
    for minutes in (1, 15, 45):
        await _settle(hass, coordinator, minutes=minutes)
    assert not mode_calls, [c.data for c in mode_calls]
    assert not temperature_calls, [c.data for c in temperature_calls]
    assert hass.states.get("climate.test").state == "cool"  # type: ignore[union-attr]
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.state == "lockout"


# ---------------------------------------------------------------------------
# DR-050 - an open window or door
# ---------------------------------------------------------------------------


def _with_window(**extra: object) -> dict:
    return {"opening_entity_ids": ["binary_sensor.test_window"], **extra}


async def _open_window_for(hass, coordinator, minutes: float) -> None:
    """Open the window, then look at the room `minutes` later.

    The window is opened twelve minutes in, past the compressor's ten-minute
    minimum run, so that what is under test is the opening's own grace and not
    the short-cycle guard refusing a stop the grace had allowed.
    """
    opened = dt_util.utcnow() + timedelta(minutes=12)
    with freeze_time(opened):
        hass.states.async_set("binary_sensor.test_window", "on")
        await hass.async_block_till_done()
    with freeze_time(opened + timedelta(minutes=minutes)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()


async def test_a_window_open_for_four_minutes_is_still_inside_the_default_grace(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("binary_sensor.test_window", "off")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_with_window())
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    await _open_window_for(hass, coordinator, 4)
    assert not [c for c in mode_calls if c.data["hvac_mode"] == "off"], mode_calls
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "none"


async def test_a_window_open_for_six_minutes_stops_the_unit(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("binary_sensor.test_window", "off")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_with_window())
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    await _open_window_for(hass, coordinator, 6)
    assert [c.data["hvac_mode"] for c in mode_calls] and {
        c.data["hvac_mode"] for c in mode_calls
    } == {"off"}


async def test_a_rooms_own_grace_is_what_stops_it(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("binary_sensor.test_window", "off")
    coordinator = await _setup(
        hass, mock_config_entry, room_extra=_with_window(**{CONF_OPENING_GRACE: 2})
    )
    assert coordinator.rooms["test_room"].opening_grace_minutes == 2.0
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    await _open_window_for(hass, coordinator, 3)
    assert {c.data["hvac_mode"] for c in mode_calls} == {"off"}


async def test_the_warnings_are_spoken_once_each_before_the_unit_stops(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("binary_sensor.test_window", "off")
    hass.states.async_set("media_player.kitchen", "idle")
    coordinator = await _setup(
        hass,
        mock_config_entry,
        room_extra=_with_window(
            **{CONF_ANNOUNCE: True, CONF_ANNOUNCE_TARGETS: ["media_player.kitchen"]}
        ),
    )
    spoken = async_mock_service(hass, "tts", "speak")
    async_mock_service(hass, "climate", "set_hvac_mode")
    hass.states.async_set("binary_sensor.test_window", "on")
    start = dt_util.utcnow()
    for minutes in (1, 2, 3, 4, 5, 6, 7):
        with freeze_time(start + timedelta(minutes=minutes)):
            await coordinator.async_refresh()
            await hass.async_block_till_done()

    messages = [call.data["message"] for call in spoken]
    assert len(messages) == 2, messages
    assert "will turn off in about 3 minutes" in messages[0]
    assert "Turning the Test Room air conditioning off" in messages[1]


async def test_a_room_with_announcements_off_is_silent_about_a_window(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    hass.states.async_set("binary_sensor.test_window", "off")
    coordinator = await _setup(hass, mock_config_entry, room_extra=_with_window())
    spoken = async_mock_service(hass, "tts", "speak")
    await _open_window_for(hass, coordinator, 7)
    assert not spoken


# ---------------------------------------------------------------------------
# DR-053 - a room coasts when its own physics will bring it back
# ---------------------------------------------------------------------------


def _outdoor(hass: HomeAssistant, temperature: str, humidity: str = "60.0") -> dict:
    hass.states.async_set("sensor.outdoor_temperature", temperature)
    hass.states.async_set("sensor.outdoor_humidity", humidity)
    return {
        CONF_OUTDOOR_TEMPERATURE_ENTITY: "sensor.outdoor_temperature",
        CONF_OUTDOOR_HUMIDITY_ENTITY: "sensor.outdoor_humidity",
    }


def _learn(coordinator) -> None:
    model = coordinator.model_for("test_room")
    model.k_sensible = Coefficient(value=2.0, variance=0.01, samples=40)
    model.k_loss = Coefficient(value=0.15, variance=0.01, samples=40)
    model.k_solar = Coefficient(value=1.0, variance=0.01, samples=40)
    model.k_sensible_bins = [
        Coefficient(value=value, variance=0.01, samples=40)
        for value in (0.5, 2.0, 4.5, 8.0)
    ]


async def test_a_cold_room_on_a_hot_day_coasts_instead_of_heating(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The Office was sent Heat 0.12 below its floor with 25 C outside."""
    coordinator = await _setup(
        hass,
        mock_config_entry,
        temperature="23.5",
        humidity="50.0",
        options_extra=_outdoor(hass, "33.0"),
    )
    _learn(coordinator)
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    await _settle(hass, coordinator, minutes=1)

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.state == "coast", state.attributes
    shown = {k: state.attributes.get(k) for k in ("mode","base_mode","demand","actuator","hci","band_low","band_high","unaided_return_minutes","reasons","rejected")}
    assert state.attributes["demand"] == "heat", shown
    assert state.attributes["actuator"] == "off"
    assert state.attributes["unaided_return_minutes"] is not None
    assert any("weather brings the room back" in r for r in state.attributes["reasons"])
    assert not [c for c in mode_calls if c.data["hvac_mode"] == "heat"], mode_calls


async def test_the_same_cold_room_on_a_cold_day_is_heated(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(
        hass,
        mock_config_entry,
        temperature="23.5",
        humidity="50.0",
        options_extra=_outdoor(hass, "8.0", "60.0"),
    )
    _learn(coordinator)
    await _settle(hass, coordinator, minutes=1)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.state != "coast", state.attributes
    assert state.attributes["actuator"] == "compressor"
    assert state.attributes["demand"] == "heat"


async def test_without_a_learned_model_a_cold_room_on_a_hot_day_still_does_not_heat(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A fresh install has no model. The plain outdoor comparison is the floor."""
    coordinator = await _setup(
        hass,
        mock_config_entry,
        temperature="23.5",
        humidity="50.0",
        options_extra=_outdoor(hass, "33.0"),
    )
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.state == "coast", state.attributes
    assert state.attributes["unaided_return_minutes"] is None
    assert coordinator.model_for("test_room").k_loss.converged is False


# ---------------------------------------------------------------------------
# DR-054 - the room loop
# ---------------------------------------------------------------------------


async def test_a_learned_room_is_regulated_by_the_loop_and_says_how(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(
        hass,
        mock_config_entry,
        temperature="29.0",
        humidity="60.0",
        options_extra=_outdoor(hass, "30.0"),
    )
    _learn(coordinator)
    await _settle(hass, coordinator, minutes=1)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["approach_c"] is not None, state.attributes
    assert state.attributes["wanted_rate_c_per_hour"] < 0
    assert any("asking" in r and "below the room" in r for r in state.attributes["reasons"])
    commanded = state.attributes["commanded_dry_bulb_c"]
    assert commanded == round(commanded)


async def test_an_unlearned_room_falls_back_to_the_target_plus_the_trim(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["approach_c"] is None
    assert state.attributes["commanded_dry_bulb_c"] is not None


async def test_a_cooling_trim_is_not_carried_into_heating(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The Office had a target of 23.2 C, a heat demand and a commanded
    setpoint of 22.8 C, because a trim learned while cooling was applied to
    heating. Here the cooling trim is a degree and a half, so carrying it over
    would move the rounded setpoint a whole degree."""
    from custom_components.abode_hvac_coordinator.regulate import RegulatorState

    coordinator = await _setup(
        hass,
        mock_config_entry,
        temperature="21.0",
        humidity="50.0",
        options_extra=_outdoor(hass, "8.0", "60.0"),
    )
    coordinator._regulators["test_room"] = RegulatorState(trim_c=-1.4)
    await _settle(hass, coordinator, minutes=1)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["demand"] == "heat", state.attributes
    target = state.attributes["target_dry_bulb_c"]
    commanded = state.attributes["commanded_dry_bulb_c"]
    assert commanded == round(target), (target, commanded)
    assert state.attributes["regulation_trim_c"] == 0.0
