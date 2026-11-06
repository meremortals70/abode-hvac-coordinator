"""Another integration's own error (a timeout) is a failed read or a failed
warning, as Home Assistant's are: the cycle and the setup carry on
[D004][D061][D069]."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from .conftest import HOUSE, set_house_states, setup_entry, stored_room
from .test_cycle import TARIFF, _issue, _tick

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")


async def _time_out(call: ServiceCall) -> None:
    raise TimeoutError("the other integration did not answer")


async def test_a_speech_engine_timing_out_does_not_fail_the_cycle(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    set_house_states(hass)
    hass.states.async_set("binary_sensor.presence", "on")
    hass.states.async_set("binary_sensor.window", "off")
    hass.services.async_register("tts", "speak", _time_out)
    entry = await setup_entry(hass, data={**HOUSE, "tts_engine": "tts.speaker"},
                              rooms=[stored_room(presence_sensor="binary_sensor.presence",
                                                 openings=["binary_sensor.window"], announce=True,
                                                 announce_through=["media_player.office"])])
    hass.states.async_set("binary_sensor.window", "on")
    for _ in range(12):
        await _tick(hass, freezer, timedelta(seconds=30))
        assert entry.runtime_data.coordinator.last_update_success, "the cycle failed on a spoken warning"


async def test_a_weather_forecast_timing_out_at_start_up(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]],
) -> None:
    set_house_states(hass)
    hass.states.async_set("weather.home", "sunny", {"temperature_unit": "°C"})
    hass.services.async_register("weather", "get_forecasts", _time_out, supports_response=SupportsResponse.ONLY)
    entry = await setup_entry(hass, data={**HOUSE, "weather": "weather.home"})
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.forecast == []
    assert entry.runtime_data.coordinator.forecast_error


async def test_a_tariff_timing_out_raises_the_repair(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]],
) -> None:
    set_house_states(hass)
    hass.services.async_register("abode_power_tariffs", "get_intervals", _time_out,
                                 supports_response=SupportsResponse.ONLY)
    entry = await setup_entry(hass, data={**HOUSE, "tariff_entry": TARIFF})
    assert entry.state is ConfigEntryState.LOADED
    assert _issue(hass, "tariff_unreadable") is not None
