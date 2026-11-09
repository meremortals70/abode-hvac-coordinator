"""The entities through Home Assistant (Section 7): what each shows, and
what each control does."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import (
    CLIMATE_ATTRS,
    DOMAIN,
    ROOM_ID,
    set_house_states,
    setup_entry,
    stored_room,
)
from .harness import (
    async_fire_time_changed,
)

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _state(hass: HomeAssistant, entity_id: str) -> str:
    st = hass.states.get(entity_id)
    assert st is not None, entity_id
    return st.state


async def test_every_entity_and_its_registry_entry(hass: HomeAssistant,
                                                    climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(reg, entry.entry_id)
    room_keys = {e.unique_id.removeprefix(f"{ROOM_ID}_") for e in entries if e.unique_id.startswith(ROOM_ID)}
    assert room_keys == {
        "mode", "comfort_index", "target_temperature", "commanded_setpoint", "dew_point", "settings", "controller",
        "learning", "comfort_in_band", "near_centre", "starts_per_hour", "cost_of_comfort", "automatic_control",
        "automatic_vane_control", "comfort_mode", "unit_mode", "fan_speed", "vertical_vane", "setpoint", "leave",
    }  # no horizontal vane offered; no Precool without a presence sensor
    disabled = {e.unique_id.removeprefix(f"{ROOM_ID}_") for e in entries if e.disabled_by is not None}
    assert disabled == {"settings", "commanded_setpoint", "starts_per_hour"}
    house = {e.unique_id.removeprefix(f"{entry.entry_id}_") for e in entries if e.unique_id.startswith(entry.entry_id)}
    assert house == {"demand_forecast", "tariff_rate", "constraints", "projected_cost", "outdoor_temperature",
                     "outdoor_apparent_temperature", "outdoor_dew_point", "forecast_peak", "stale_feeds",
                     "rooms_configured"}
    index = hass.states.get("sensor.office_comfort_index")
    assert index is not None and index.attributes["position"] in ("below", "in", "above")
    assert "device_class" not in index.attributes  # an index, not an air temperature
    dew = hass.states.get("sensor.office_dew_point")
    assert dew is not None and float(dew.state) == pytest.approx(15.3, abs=0.1)
    assert set(dew.attributes) >= {"outdoor_dew_point", "free_cooling_advised", "condensation_risk"}
    assert _state(hass, "sensor.office_controller") == "plan_pid"
    assert _state(hass, "sensor.abode_hvac_coordinator_rooms_configured") == "1"
    assert _state(hass, "sensor.abode_hvac_coordinator_outdoor_temperature") == "30.0"
    cost = hass.states.get("sensor.office_cost_of_comfort")
    assert cost is not None and cost.attributes["unit_of_measurement"] == hass.config.currency
    assert cost.attributes["state_class"] == "total" and "last_reset" in cost.attributes


async def test_disabled_sensors_once_enabled(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    reg = er.async_get(hass)
    for key in ("settings", "commanded_setpoint", "starts_per_hour"):
        reg.async_get_or_create("sensor", DOMAIN, f"{ROOM_ID}_{key}", suggested_object_id=f"office_{key}",
                                disabled_by=None)
    await setup_entry(hass)
    settings = hass.states.get("sensor.office_settings")
    assert settings is not None and settings.attributes["settings"]["presence_sensor"] == "unset"
    assert settings.attributes["profile"]["fan_modes"] == ["auto", "quiet", "high"]
    commanded = hass.states.get("sensor.office_commanded_setpoint")
    assert commanded is not None and float(commanded.state) == 21.0
    assert commanded.attributes["wanted"] is not None


async def test_automatic_control_off_sends_off_once_then_hands_over(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:  # the component runs the room [D049]
        await hass.services.async_call("select", "select_option",
                                       {"entity_id": "select.office_unit_mode", "option": "heat"}, blocking=True)
    assert err.value.translation_key == "automatic_control_on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.office_automatic_control"},
                                   blocking=True)
    assert [c.data for c in climate_calls["set_hvac_mode"]] == [{"entity_id": "climate.office", "hvac_mode": "off"}]
    assert _state(hass, "sensor.office") == "automatic_off"
    hass.states.async_set("climate.office", "off", CLIMATE_ATTRS)
    await _tick(hass, freezer, timedelta(seconds=30))
    assert len(climate_calls["set_hvac_mode"]) == 1  # sent once [D089]
    # now the room's controls are the user's
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.office_unit_mode", "option": "heat"}, blocking=True)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.office_fan_speed", "option": "quiet"}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": "number.office_setpoint", "value": 22.5},
                                   blocking=True)
    assert climate_calls["set_hvac_mode"][-1].data == {"entity_id": "climate.office", "hvac_mode": "heat"}
    assert climate_calls["set_fan_mode"][-1].data == {"entity_id": "climate.office", "fan_mode": "quiet"}
    assert climate_calls["set_temperature"][-1].data == {"entity_id": "climate.office", "temperature": 22.5}
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call("number", "set_value", {"entity_id": "number.office_setpoint", "value": 31.0},
                                       blocking=True)


async def test_the_vanes_are_the_users_unless_vane_control_is_on(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.office_vertical_vane", "option": "auto"}, blocking=True)
    assert climate_calls["set_swing_mode"][-1].data == {"entity_id": "climate.office", "swing_mode": "auto"}
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.office_automatic_vane_control"},
                                   blocking=True)
    assert _state(hass, "switch.office_automatic_vane_control") == "on"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call("select", "select_option",
                                       {"entity_id": "select.office_vertical_vane", "option": "auto"}, blocking=True)
    assert err.value.translation_key == "automatic_vane_control_on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.office_automatic_vane_control"},
                                   blocking=True)
    assert _state(hass, "switch.office_automatic_vane_control") == "off"


async def test_a_unit_that_refuses_a_command(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    await setup_entry(hass, rooms=[stored_room()])
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.office_automatic_control"},
                                   blocking=True)

    async def refuse(call: ServiceCall) -> None:
        raise HomeAssistantError("the unit is busy")

    hass.services.async_register("climate", "set_fan_mode", refuse)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("select", "select_option",
                                       {"entity_id": "select.office_fan_speed", "option": "high"}, blocking=True)
    assert err.value.translation_key == "command_failed"


async def test_the_unit_controls_follow_the_unit(hass: HomeAssistant,
                                                 climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    assert _state(hass, "select.office_unit_mode") == "cool"
    assert _state(hass, "number.office_setpoint") == "24.0"
    hass.states.async_set("climate.office", "dry", {**CLIMATE_ATTRS, "fan_mode": "high", "temperature": 23.0})
    await hass.async_block_till_done()
    assert _state(hass, "select.office_unit_mode") == "dry"  # without waiting for a cycle [D049]
    assert _state(hass, "select.office_fan_speed") == "high"
    assert _state(hass, "number.office_setpoint") == "23.0"
    attrs = {k: v for k, v in CLIMATE_ATTRS.items() if k != "temperature"}
    hass.states.async_set("climate.office", "cool", {**attrs, "target_temp_low": 22.0, "target_temp_high": 26.0})
    await hass.async_block_till_done()
    assert _state(hass, "number.office_setpoint") == "24.0"  # a range: its middle [D081]
    hass.states.async_set("climate.office", "cool", {**attrs, "target_temp_low": "x", "target_temp_high": 26.0})
    await hass.async_block_till_done()
    assert _state(hass, "number.office_setpoint") == "unknown"
    hass.states.async_set("climate.office", "cool", {**CLIMATE_ATTRS, "temperature": "x"})
    await hass.async_block_till_done()
    assert _state(hass, "number.office_setpoint") == "unknown"
    hass.states.async_set("climate.office", "unavailable")
    await hass.async_block_till_done()
    assert _state(hass, "select.office_unit_mode") == "unavailable"
    assert _state(hass, "number.office_setpoint") == "unavailable"


async def test_comfort_mode_and_leave(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.office_comfort_mode", "option": "sleep"}, blocking=True)
    assert _state(hass, "select.office_comfort_mode") == "sleep"
    assert _state(hass, "sensor.office") == "sleep"  # [D058]
    await hass.services.async_call("button", "press", {"entity_id": "button.office_leave"}, blocking=True)
    assert _state(hass, "sensor.office") == "left"  # off at once [D088]
    assert climate_calls["set_hvac_mode"][-1].data == {"entity_id": "climate.office", "hvac_mode": "off"}
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.office_comfort_mode", "option": "awake"}, blocking=True)
    assert _state(hass, "sensor.office") == "occupied"  # changing Comfort mode undoes Leave [D088]


async def test_an_invalid_comfort_mode_option_is_refused(hass: HomeAssistant,
                                                         climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call("select", "select_option",
                                       {"entity_id": "select.office_comfort_mode", "option": "party"},
                                       blocking=True)
    assert err.value.translation_key == "invalid_comfort_mode"


async def test_the_precool_button(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]],
                                  freezer: FrozenDateTimeFactory) -> None:
    set_house_states(hass)
    hass.states.async_set("binary_sensor.presence", "off")
    await setup_entry(hass, rooms=[stored_room(presence_sensor="binary_sensor.presence")])
    await _tick(hass, freezer, timedelta(minutes=11))
    await hass.services.async_call("button", "press", {"entity_id": "button.office_precool"}, blocking=True)
    assert _state(hass, "sensor.office") == "precool"  # [I8]


async def test_a_room_whose_units_have_not_reported(hass: HomeAssistant,
                                                     climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    hass.states.async_set("climate.office", "unavailable")
    room = stored_room()
    del room["profile"]
    await setup_entry(hass, rooms=[room])
    assert hass.states.get("select.office_unit_mode") is None  # nothing offered yet [D048]
    assert hass.states.get("number.office_setpoint") is None
    assert _state(hass, "select.office_comfort_mode") == "schedule"


async def test_an_edited_room_has_the_entities_its_new_settings_give(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    """An edit makes the room's entities again: a presence sensor added
    brings the Precool button; taken away, the button and its registry
    entry go; a unit changed is the one the controls follow; a sensor the
    user enabled stays enabled [D098]."""
    set_house_states(hass)
    set_house_states(hass, climate="climate.den")
    hass.states.async_set("binary_sensor.presence", "on")
    entry = await setup_entry(hass)
    reg = er.async_get(hass)
    assert hass.states.get("button.office_precool") is None
    settings = reg.async_get("sensor.office_settings")
    assert settings is not None and settings.disabled_by is not None
    reg.async_update_entity("sensor.office_settings", disabled_by=None)  # enabled; added on the next reload or edit
    hass.config_entries.async_update_entry(
        entry, options={"rooms": [stored_room(presence_sensor="binary_sensor.presence")]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("button.office_precool") is not None
    assert hass.states.get("sensor.office_settings") is not None
    assert reg.async_get("sensor.office_settings").disabled_by is None  # the user's choice kept
    hass.config_entries.async_update_entry(entry, options={"rooms": [stored_room(climate="climate.den")]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("button.office_precool") is None
    assert reg.async_get_entity_id("button", DOMAIN, f"{ROOM_ID}_precool") is None
    hass.states.async_set("climate.den", "heat", CLIMATE_ATTRS)
    await hass.async_block_till_done()
    assert _state(hass, "select.office_unit_mode") == "heat"  # follows the new unit
    assert reg.async_get_entity_id("select", DOMAIN, f"{ROOM_ID}_unit_mode") == "select.office_unit_mode"
