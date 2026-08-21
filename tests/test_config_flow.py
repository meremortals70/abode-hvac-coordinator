"""Config and options flow. NOT YET RUN."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.abode_hvac_coordinator.const import (
    CONF_ALLOW_COVER_CONTROL,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CLIMATE_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_RESERVE_MARGIN_KWH,
    CONF_ROOMS,
    CONF_SOLAR_POWER_ENTITY,
    CONF_TARIFF_ENTRY_ID,
    DOMAIN,
    STARTUP_FETCH_DELAY,
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
    """Tariff, outdoor feeds, forecast and power. The window editing steps are gone."""
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
    assert set(result["menu_options"]) == {"tariff", "outdoor", "forecast", "power"}


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


async def test_the_room_step_offers_the_cover_control_override(
    hass: HomeAssistant, mock_setup_entry: None
) -> None:
    """The tick that keeps a semi-transparent blind still, defaulted on."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert set(result["data_schema"].schema).issuperset(
        {CONF_ALLOW_COVER_CONTROL}
    )
    default_field = next(
        key
        for key in result["data_schema"].schema
        if str(key) == CONF_ALLOW_COVER_CONTROL
    )
    assert default_field.default() is True


async def test_disabling_cover_control_is_stored_on_the_room(
    hass: HomeAssistant, mock_setup_entry: None
) -> None:
    """Ticking it off for one room reaches the stored RoomConfig."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Office",
            CONF_CLIMATE_ENTITY: "climate.office",
            CONF_ALLOW_COVER_CONTROL: False,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"occupied_low": 24.0, "occupied_high": 27.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ROOMS][0][CONF_ALLOW_COVER_CONTROL] is False


async def test_the_power_step_stores_all_five_fields(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Vendor-neutral entity pickers plus the two numeric settings."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "global"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "power"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "power"
    fields = {str(key) for key in result["data_schema"].schema}
    assert fields == {
        CONF_BATTERY_SOC_ENTITY,
        CONF_BATTERY_CAPACITY_KWH,
        CONF_SOLAR_POWER_ENTITY,
        CONF_HOUSE_LOAD_ENTITY,
        CONF_RESERVE_MARGIN_KWH,
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_BATTERY_CAPACITY_KWH: 13.5,
            CONF_SOLAR_POWER_ENTITY: "sensor.solar_power",
            CONF_HOUSE_LOAD_ENTITY: "sensor.house_load",
            CONF_RESERVE_MARGIN_KWH: 2.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BATTERY_SOC_ENTITY] == "sensor.battery_soc"
    assert result["data"][CONF_BATTERY_CAPACITY_KWH] == 13.5
    assert result["data"][CONF_SOLAR_POWER_ENTITY] == "sensor.solar_power"
    assert result["data"][CONF_HOUSE_LOAD_ENTITY] == "sensor.house_load"
    assert result["data"][CONF_RESERVE_MARGIN_KWH] == 2.0


async def test_the_power_step_leaves_the_feature_unconfigured_by_default(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Nothing submitted is nothing engaged — comfort alone still runs."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "global"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "power"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BATTERY_SOC_ENTITY] is None
    assert result["data"][CONF_RESERVE_MARGIN_KWH] is None


async def test_a_naive_forecast_does_not_take_the_evaluation_loop_down(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The 0.8.0 crash, reproduced end to end.

    A weather integration returning forecast timestamps without an offset
    raised `TypeError` out of `peak_between`, through `_async_update_data`, and
    took every room with it. The forecast is allowed to fail; the controller is
    not.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, "weather_entity_id": "weather.home"},
    )

    naive = {
        "weather.home": {
            "forecast": [
                {"datetime": f"2026-08-08T{h:02d}:00:00", "temperature": 24.0 + h}
                for h in range(8, 20)
            ]
        }
    }

    async def _forecasts(call):
        return naive

    hass.services.async_register(
        "weather", "get_forecasts", _forecasts, supports_response="only"
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # The forecast is not fetched inline at setup. Since 0.8.4 it is a
    # background task on `async_call_later`, so the clock has to advance past
    # the first startup attempt before there is a trajectory to inspect. This
    # test asserted against the pre-0.8.4 inline fetch and had never been run
    # to find out; corrected in 0.8.6.
    async_fire_time_changed(hass, dt_util.utcnow() + STARTUP_FETCH_DELAY * 2)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    assert coordinator.last_update_success
    assert coordinator.trajectory is not None
    for point in coordinator.trajectory.points:
        assert point.at.tzinfo is not None


async def test_the_model_accumulates_samples_at_the_real_evaluation_interval(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Diagnostics from a live install showed `samples: 0` on every coefficient.

    The learning anchor was replaced on every evaluation, so the measured
    interval was always the 30-second evaluation period — below the 60-second
    minimum an observation needs to carry information over sensor
    quantisation. Every observation was discarded, the model never converged,
    and coast, the dry-versus-cool split, precool sizing and the heading-home
    estimate were all permanently unavailable with nothing reporting a fault.

    **This test must tick at 30 seconds, not at a convenient larger number.**
    A first version advanced two minutes per cycle and passed against the
    broken code, because at two minutes the interval clears the minimum even
    when the anchor is reset every time. It proved nothing.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            **mock_config_entry.options,
            "outdoor_temperature_entity_id": "sensor.outdoor",
        },
    )
    hass.states.async_set("sensor.outdoor", "32.0")
    hass.states.async_set("sensor.test_temperature", "25.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    room_id = next(iter(coordinator.rooms))

    start = dt_util.utcnow()
    for step in range(1, 11):
        hass.states.async_set("sensor.test_temperature", f"{25.0 + step * 0.05:.2f}")
        hass.states.async_set("sensor.test_humidity", f"{60.0 + step * 0.1:.2f}")
        with patch(
            "homeassistant.util.dt.utcnow",
            return_value=start + timedelta(seconds=30 * step),
        ):
            await coordinator.async_refresh()
            await hass.async_block_till_done()

    assert coordinator.model_for(room_id).k_loss.samples > 0, (
        "five minutes of 30-second evaluations produced no observation; the "
        "learning anchor is being reset every cycle"
    )
