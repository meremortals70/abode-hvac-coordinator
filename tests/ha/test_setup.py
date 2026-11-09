"""Setting up and taking down, and the actions, through Home Assistant
(Section 8, __init__.py)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.abode_hvac_coordinator import async_unload_entry

from .conftest import DOMAIN, HOUSE, ROOM_ID, set_house_states, setup_entry, stored_room
from .harness import (
    MockConfigEntry,
    WebSocketGenerator,
    async_fire_time_changed,
)

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")

# the room device's software version is the manifest's, whatever the release
_MANIFEST_VERSION = json.loads(
    (Path(__file__).parents[2] / "custom_components" / "abode_hvac_coordinator" / "manifest.json").read_text()
)["version"]


async def test_setup_runs_the_first_cycle_before_any_entity(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED
    mode = hass.states.get("sensor.office")
    assert mode is not None and mode.state == "occupied"  # no presence sensor: occupied [Set]
    assert mode.attributes["room_id"] == ROOM_ID
    assert mode.attributes["action"] == "cool"
    # the first cycle planned and sent the setpoint [D002]
    assert [c.data for c in climate_calls["set_temperature"]] == [{"entity_id": "climate.office", "temperature": 21.0}]


async def test_devices_room_reached_through_the_house(hass: HomeAssistant,
                                                      climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    reg = dr.async_get(hass)
    house = reg.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    room = reg.async_get_device_by_identifier((DOMAIN, ROOM_ID), entry.entry_id)
    assert house is not None and room is not None
    assert house.entry_type is dr.DeviceEntryType.SERVICE
    assert room.via_device_id == house.id
    assert (room.name, room.manufacturer, room.model, room.sw_version) == ("Office", "Abode", "Room coordinator",
                                                                           _MANIFEST_VERSION)


async def test_unload_writes_memory_and_remove_deletes_it(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], hass_storage: dict[str, Any]
) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert ROOM_ID in hass_storage[DOMAIN]["data"]["rooms"]  # [D064]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN not in hass_storage


async def test_a_newer_stored_file_retries_setup(hass: HomeAssistant, hass_storage: dict[str, Any],
                                                 climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    hass_storage[DOMAIN] = {"version": 9, "minor_version": 1, "key": DOMAIN, "data": {}}
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY  # ConfigEntryNotReady [R18]


async def test_learning_restored_from_memory(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]],
                                             hass_storage: dict[str, Any]) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    switch = "switch.office_automatic_vane_control"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(switch)
    assert state is not None and state.state == "on"  # a choice stands across a restart [D049]


async def test_precool_actions(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]],
                               freezer: FrozenDateTimeFactory) -> None:
    set_house_states(hass)
    hass.states.async_set("binary_sensor.office_presence", "off")
    await setup_entry(hass, rooms=[stored_room(presence_sensor="binary_sensor.office_presence")])
    freezer.tick(timedelta(minutes=11))  # empty for longer than the empty grace
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    mode = hass.states.get("sensor.office")
    assert mode is not None and mode.state == "unoccupied"
    ready = dt_util.utcnow() + timedelta(hours=2)
    await hass.services.async_call(DOMAIN, "precool", {"room_id": ROOM_ID, "ready_by": ready.isoformat()},
                                   blocking=True)
    await hass.async_block_till_done()
    mode = hass.states.get("sensor.office")
    assert mode is not None and mode.state == "precool"
    await hass.services.async_call(DOMAIN, "cancel_precool", {"room_id": ROOM_ID}, blocking=True)
    await hass.async_block_till_done()
    mode = hass.states.get("sensor.office")
    assert mode is not None and mode.state != "precool"


async def test_precool_refused_without_a_presence_sensor(hass: HomeAssistant,
                                                         climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "precool", {"room_id": ROOM_ID}, blocking=True)
    assert err.value.translation_key == "precool_needs_presence"


async def test_actions_refused_for_an_unknown_room_or_none_loaded(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "cancel_precool", {"room_id": "nope"}, blocking=True)
    assert err.value.translation_key == "unknown_room"
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "precool", {"room_id": ROOM_ID}, blocking=True)
    assert err.value.translation_key == "not_loaded"


async def test_only_a_leftover_room_device_can_be_deleted(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], hass_ws_client: WebSocketGenerator
) -> None:
    assert await async_setup_component(hass, "config", {})
    set_house_states(hass)
    entry = await setup_entry(hass)
    reg = dr.async_get(hass)
    room = reg.async_get_device_by_identifier((DOMAIN, ROOM_ID), entry.entry_id)
    house = reg.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    stale = reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "gone")})
    other = reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("other", "x")})
    assert room is not None and house is not None
    client = await hass_ws_client(hass)
    results = {}
    for name, device in (("room", room), ("house", house), ("stale", stale), ("other", other)):
        await client.send_json_auto_id({"type": "config/device_registry/remove", "device_id": device.id})
        results[name] = (await client.receive_json())["success"]
    assert results == {"room": False, "house": False, "stale": True, "other": True}  # [R18]


async def test_an_older_configuration_is_converted(hass: HomeAssistant,
                                                   climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=HOUSE, options={"rooms": [stored_room()]}, version=1,
                            minor_version=0)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (1, 1)


async def test_a_choice_is_saved_within_five_seconds(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    hass_storage.pop(DOMAIN, None)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.office_comfort_mode", "option": "sleep"}, blocking=True)
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass_storage[DOMAIN]["data"]["rooms"][ROOM_ID]["choices"]["comfort_mode"] == "sleep"  # [D064]


async def test_unload_before_runtime_data_is_set_does_not_crash(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    """If setup fails before runtime_data is set, unload still returns cleanly."""
    set_house_states(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=HOUSE, options={"rooms": [stored_room()]})
    entry.add_to_hass(hass)
    assert await async_unload_entry(hass, entry) is False
