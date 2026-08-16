"""Setup and unload. NOT YET RUN."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.abode_hvac_coordinator.const import DOMAIN, SERVICE_HEADING_HOME


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The entry loads and unloads cleanly."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_services_registered(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Services are available once the integration is set up."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_HEADING_HOME)


async def test_unoccupied_room_is_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An unoccupied room does not actuate, however hot it is."""
    hass.states.async_set("sensor.test_temperature", "34.0")
    hass.states.async_set("sensor.test_humidity", "80.0")
    hass.states.async_set("binary_sensor.test_presence", "off")

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.state == "unoccupied"
    assert state.attributes["actuator"] == "none"
