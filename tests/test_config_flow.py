"""Config and options flow. NOT YET RUN."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.abode_hvac_coordinator.const import (
    CONF_CLIMATE_ENTITY,
    CONF_ROOMS,
    CONF_TARIFF_ENTRY_ID,
    DOMAIN,
)


async def test_user_flow_collects_the_first_room(
    hass: HomeAssistant, mock_setup_entry: None
) -> None:
    """Setup produces a working room rather than an empty hub."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "room"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "First Room", CONF_CLIMATE_ENTITY: "climate.first"},
    )
    assert result["step_id"] == "bands"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"occupied_low": 24.0, "occupied_high": 27.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(result["data"][CONF_ROOMS]) == 1
    assert result["data"][CONF_ROOMS][0]["room_id"] == "first_room"


async def test_user_flow_rejects_an_inverted_band(
    hass: HomeAssistant, mock_setup_entry: None
) -> None:
    """The same validation applies during initial setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "First Room", CONF_CLIMATE_ENTITY: "climate.first"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"occupied_low": 27.0, "occupied_high": 24.0}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "band_inverted"}


async def test_single_instance_only(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A second entry is refused."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_adds_a_room(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A room is added across the room and bands steps."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "rooms"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "room"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "room"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Second Room", CONF_CLIMATE_ENTITY: "climate.second"},
    )
    assert result["step_id"] == "bands"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"occupied_low": 25.0, "occupied_high": 28.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(result["data"][CONF_ROOMS]) == 2


async def test_inverted_band_is_rejected(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A band whose low is above its high is refused."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "rooms"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "room"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Third Room", CONF_CLIMATE_ENTITY: "climate.third"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"occupied_low": 28.0, "occupied_high": 25.0}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "band_inverted"}


async def test_setup_stores_no_tariff_of_its_own(
    hass: HomeAssistant, mock_setup_entry: None
) -> None:
    """The plan belongs to Abode Power Tariffs. Setup must not seed one here."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "First Room", CONF_CLIMATE_ENTITY: "climate.first"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"occupied_low": 24.0, "occupied_high": 27.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "tariff_windows" not in result["data"]
    assert "export_windows" not in result["data"]
    assert CONF_TARIFF_ENTRY_ID not in result["data"]


async def test_the_house_menu_offers_the_three_house_wide_settings(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Tariff, outdoor feeds and forecast. The window editing steps are gone."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "global"}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"tariff", "outdoor", "forecast"}


async def test_the_outdoor_step_collects_both_feeds(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Humidity is what makes free-cooling advice possible at all."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "global"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "outdoor"}
    )
    assert result["type"] is FlowResultType.FORM
    fields = {str(key) for key in result["data_schema"].schema}
    assert "outdoor_temperature_entity_id" in fields
    assert "outdoor_humidity_entity_id" in fields


async def test_the_tariff_step_stores_only_the_entry_id(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Nothing about the plan itself is copied into this integration."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "global"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "tariff"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "tariff"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TARIFF_ENTRY_ID] is None


async def test_wind_speed_is_converted_from_the_entity_unit(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Most Australian weather feeds publish km/h; Steadman wants m/s.

    Assuming the unit would make the apparent temperature wrong by a factor of
    3.6, in the direction that advises opening the windows on an evening you
    should not.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            **mock_config_entry.options,
            "outdoor_wind_entity_id": "sensor.outdoor_wind",
        },
    )
    hass.states.async_set(
        "sensor.outdoor_wind",
        "36.0",
        {"unit_of_measurement": "km/h", "device_class": "wind_speed"},
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    assert coordinator.outdoor_wind_ms() == pytest.approx(10.0)


async def test_wind_speed_already_in_metres_per_second_is_untouched(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The conversion must not be applied twice."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            **mock_config_entry.options,
            "outdoor_wind_entity_id": "sensor.outdoor_wind",
        },
    )
    hass.states.async_set(
        "sensor.outdoor_wind",
        "10.0",
        {"unit_of_measurement": "m/s", "device_class": "wind_speed"},
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.runtime_data.outdoor_wind_ms() == pytest.approx(10.0)


async def test_an_unconvertible_wind_unit_falls_back_to_still_air(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A unit nothing can convert must not take the integration down."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            **mock_config_entry.options,
            "outdoor_wind_entity_id": "sensor.outdoor_wind",
        },
    )
    hass.states.async_set(
        "sensor.outdoor_wind", "5.0", {"unit_of_measurement": "furlongs/fortnight"}
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.runtime_data.outdoor_wind_ms() is None


async def test_the_forecast_step_stores_only_the_weather_entity(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Precool needs the forecast; nothing about the weather is copied here."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "global"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "forecast"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "forecast"
    assert set(result["data_schema"].schema) == {"weather_entity_id"}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["weather_entity_id"] is None
