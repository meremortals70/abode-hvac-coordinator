"""Setup and unload. NOT YET RUN."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.abode_hvac_coordinator.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_RESERVE_MARGIN_KWH,
    CONF_SOLAR_POWER_ENTITY,
    DOMAIN,
    SERVICE_HEADING_HOME,
)
from custom_components.abode_hvac_coordinator.tariff import TariffSeries


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


def _no_grid_import_series(now) -> TariffSeries:
    """A single interval, in force now, that forbids grid import."""
    return TariffSeries.from_response(
        {
            "intervals": [
                {
                    "start_time": (now - timedelta(hours=1)).isoformat(),
                    "end_time": (now + timedelta(hours=6)).isoformat(),
                    "rate": "peak",
                    "per_kwh": 0.50,
                    "export_per_kwh": 0.05,
                    "constraints": ["no_grid_import"],
                }
            ]
        },
        now,
    )


async def test_power_unavailable_holds_the_compressor_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """No grid import, insufficient battery: the room holds rather than run.

    Exercises the coordinator's own `_compute_power_context` /
    `_power_available`, not just select_actuator's handling of an
    already-resolved flag (that is covered in test_core.py).
    """
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            **mock_config_entry.options,
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_BATTERY_CAPACITY_KWH: 10.0,
            CONF_SOLAR_POWER_ENTITY: "sensor.solar_power",
            CONF_HOUSE_LOAD_ENTITY: "sensor.house_load",
            CONF_RESERVE_MARGIN_KWH: 8.0,
        },
    )
    hass.states.async_set("sensor.test_temperature", "30.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set("sensor.battery_soc", "20.0")
    hass.states.async_set("sensor.solar_power", "0")
    hass.states.async_set("sensor.house_load", "500")

    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    coordinator.tariff = _no_grid_import_series(dt_util.utcnow())
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # A freshly set-up room has no converged thermal model and no solved
    # target yet, so `_power_available` cannot project a need against the
    # battery and holds rather than run on an unknown — the same outcome a
    # converged model would also reach here, since 20% of 10 kWh is 2 kWh,
    # entirely inside the 8 kWh reserve margin.
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "none"
