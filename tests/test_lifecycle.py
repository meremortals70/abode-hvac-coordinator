"""The integration's lifecycle and services: what `__init__.py` owns.

Setup, the two services, migration, removal of the entry and of a room's
device, each from the outside, through Home Assistant.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.abode_hvac_coordinator import (
    async_migrate_entry,
    async_remove_config_entry_device,
    async_remove_entry,
    async_unload_entry,
)
from custom_components.abode_hvac_coordinator.const import (
    ATTR_DEADLINE,
    ATTR_ROOM_ID,
    CONF_ROOMS,
    DOMAIN,
    SERVICE_CLEAR_OVERRIDE,
    SERVICE_HEADING_HOME,
)
from custom_components.abode_hvac_coordinator.models import Mode

_UNIT = {
    "hvac_modes": ["off", "cool", "heat", "dry", "fan_only"],
    "hvac_action": "idle",
    "min_temp": 18.0,
    "max_temp": 30.0,
    "target_temp_step": 1.0,
    "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE.value,
}


async def _loaded(hass: HomeAssistant, entry: MockConfigEntry, *, presence: str = "on"):
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_temperature", "26.0")
    hass.states.async_set("sensor.test_humidity", "55.0")
    hass.states.async_set("binary_sensor.test_presence", presence)
    hass.states.async_set("climate.test", "off", _UNIT)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


async def test_heading_home_brings_an_empty_room_out_of_unoccupied(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _loaded(hass, mock_config_entry, presence="off")
    await coordinator.async_refresh()
    assert coordinator.data["test_room"].mode is Mode.UNOCCUPIED

    await hass.services.async_call(
        DOMAIN,
        SERVICE_HEADING_HOME,
        {ATTR_ROOM_ID: "test_room", ATTR_DEADLINE: "2030-01-01 18:00:00"},
        blocking=True,
    )
    await hass.async_block_till_done()
    trace = coordinator.data["test_room"]
    assert trace.mode is not Mode.UNOCCUPIED
    assert "heading home, overrides presence" in trace.reasons


async def test_clear_override_puts_an_empty_room_back_to_unoccupied(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _loaded(hass, mock_config_entry, presence="off")
    await hass.services.async_call(
        DOMAIN, SERVICE_HEADING_HOME, {ATTR_ROOM_ID: "test_room"}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.data["test_room"].mode is not Mode.UNOCCUPIED

    await hass.services.async_call(
        DOMAIN, SERVICE_CLEAR_OVERRIDE, {ATTR_ROOM_ID: "test_room"}, blocking=True
    )
    # The coordinator's refresh debouncer holds a second request inside its
    # ten-second cooldown; it runs when the cooldown ends.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert coordinator.data["test_room"].mode is Mode.UNOCCUPIED


async def test_heading_home_ends_once_the_room_is_occupied(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.9.6, DR-080. Until 0.9.6 only clear_override ended one, and the room
    stayed in Precondition, overriding presence, until then."""
    coordinator = await _loaded(hass, mock_config_entry, presence="off")
    await hass.services.async_call(
        DOMAIN, SERVICE_HEADING_HOME, {ATTR_ROOM_ID: "test_room"}, blocking=True
    )
    await hass.async_block_till_done()
    assert "test_room" in coordinator._heading_home
    hass.states.async_set("binary_sensor.test_presence", "on")
    start = dt_util.utcnow()
    for minutes in (1, 3, 5):
        with freeze_time(start + timedelta(minutes=minutes)):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
    assert "test_room" not in coordinator._heading_home
    trace = coordinator.data["test_room"]
    assert trace.mode is not Mode.PRECONDITION

    # Leaving again turns the room off, as for any room.
    hass.states.async_set("binary_sensor.test_presence", "off")
    for minutes in range(6, 30):
        with freeze_time(start + timedelta(minutes=minutes)):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
    assert coordinator.data["test_room"].mode is Mode.UNOCCUPIED


async def test_heading_home_ends_an_hour_past_its_deadline(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _loaded(hass, mock_config_entry, presence="off")
    deadline = dt_util.utcnow() + timedelta(minutes=20)
    coordinator.async_request_heading_home("test_room", deadline)
    with freeze_time(deadline + timedelta(minutes=59)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert coordinator.data["test_room"].mode is Mode.PRECONDITION
    with freeze_time(deadline + timedelta(minutes=61)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    trace = coordinator.data["test_room"]
    assert trace.mode is Mode.UNOCCUPIED
    assert any("an hour past its deadline" in r for r in trace.reasons)


@pytest.mark.parametrize("service", [SERVICE_HEADING_HOME, SERVICE_CLEAR_OVERRIDE])
async def test_a_room_that_does_not_exist_is_refused_by_name(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, service: str
) -> None:
    await _loaded(hass, mock_config_entry)
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN, service, {ATTR_ROOM_ID: "no_such_room"}, blocking=True
        )
    assert raised.value.translation_key == "unknown_room"
    assert raised.value.translation_placeholders == {"room_id": "no_such_room"}


async def test_services_refuse_every_room_while_the_entry_is_unloaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _loaded(hass, mock_config_entry)
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_HEADING_HOME, {ATTR_ROOM_ID: "test_room"}, blocking=True
        )


# ---------------------------------------------------------------------------
# Unload and migration
# ---------------------------------------------------------------------------


async def test_a_failed_platform_unload_leaves_the_store_unflushed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _loaded(hass, mock_config_entry)
    flush = AsyncMock()
    with (
        patch.object(coordinator.store, "async_flush", flush),
        patch.object(
            hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
        ),
    ):
        assert not await async_unload_entry(hass, mock_config_entry)
    flush.assert_not_awaited()
    # Unload properly so nothing is left running after the test.
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()


async def test_an_entry_from_a_newer_release_refuses_to_load(
    hass: HomeAssistant, room_config: dict
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, version=3, data={CONF_ROOMS: []}, options={CONF_ROOMS: [room_config]}
    )
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)
    assert entry.version == 3


async def test_a_v1_entry_with_no_rooms_migrates_untouched(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=1, data={CONF_ROOMS: []}, options={})
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.data == {CONF_ROOMS: []}
    assert dict(entry.options) == {}


async def test_a_v1_room_with_no_head_migrates_to_an_empty_list(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={CONF_ROOMS: [{"room_id": "spare", "climate_entity_id": None}]},
        options={},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.data[CONF_ROOMS][0]["climate_entity_ids"] == []


async def test_a_current_entry_is_left_as_it_is(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    before = dict(mock_config_entry.options)
    assert await async_migrate_entry(hass, mock_config_entry)
    assert dict(mock_config_entry.options) == before


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


async def test_removing_the_entry_removes_what_it_learned(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.abode_hvac_coordinator.ModelStore.async_remove",
        AsyncMock(),
    ) as remove:
        await async_remove_entry(hass, mock_config_entry)
    remove.assert_awaited_once()


async def test_removing_the_entry_through_home_assistant_completes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _loaded(hass, mock_config_entry)
    result = await hass.config_entries.async_remove(mock_config_entry.entry_id)
    assert result == {"require_restart": False}
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


def _device(
    hass: HomeAssistant, entry: MockConfigEntry, room_id: str
) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, room_id)}
    )


async def test_a_configured_rooms_device_cannot_be_deleted(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _loaded(hass, mock_config_entry)
    device = _device(hass, mock_config_entry, "test_room")
    assert not await async_remove_config_entry_device(hass, mock_config_entry, device)


async def test_a_removed_rooms_device_can_be_deleted(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await _loaded(hass, mock_config_entry)
    device = _device(hass, mock_config_entry, "old_room")
    assert await async_remove_config_entry_device(hass, mock_config_entry, device)


async def test_device_removal_reads_the_stored_rooms_while_unloaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    configured = _device(hass, mock_config_entry, "test_room")
    removed = _device(hass, mock_config_entry, "old_room")
    assert getattr(mock_config_entry, "runtime_data", None) is None
    assert not await async_remove_config_entry_device(
        hass, mock_config_entry, configured
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, removed)


async def test_device_removal_falls_back_to_entry_data_with_no_options(
    hass: HomeAssistant, room_config: dict
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ROOMS: [room_config]}, options={})
    entry.add_to_hass(hass)
    assert not await async_remove_config_entry_device(
        hass, entry, _device(hass, entry, "test_room")
    )


async def test_a_device_belonging_to_another_integration_is_not_judged(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={("other_domain", "test_room")},
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, device)
