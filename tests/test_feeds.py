"""The three outside feeds: the tariff, the weather forecast and the solar forecast.

Each is fetched through a real Home Assistant service or platform call, at
startup with retries and then on a timer. A feed that cannot be read is
reported once the retries are spent, and the last good reading is held
(DR-004, DR-044, DR-061).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.abode_hvac_coordinator.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_RESERVE_MARGIN_KWH,
    CONF_ROOMS,
    CONF_SOLAR_FORECAST_ENTRIES,
    CONF_SOLAR_POWER_ENTITY,
    CONF_TARIFF_ENTRY_ID,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    ISSUE_FORECAST_UNAVAILABLE,
    ISSUE_SOLAR_FORECAST_UNAVAILABLE,
    ISSUE_TARIFF_UNAVAILABLE,
    SOLAR_FORECAST_REFRESH_INTERVAL,
    STARTUP_FETCH_ATTEMPTS,
    STARTUP_FETCH_DELAY,
    TARIFF_DOMAIN,
    TARIFF_SERVICE_GET_INTERVALS,
    WEATHER_DOMAIN,
    WEATHER_SERVICE_GET_FORECASTS,
)

_PLATFORMS = "homeassistant.components.energy.websocket_api.async_get_energy_platforms"


def _intervals(now) -> dict[str, Any]:
    start = now.replace(minute=0, second=0, microsecond=0)
    return {
        "intervals": [
            {
                "start_time": (start + timedelta(minutes=30 * i)).isoformat(),
                "end_time": (start + timedelta(minutes=30 * (i + 1))).isoformat(),
                "duration": 30,
                "per_kwh": 0.3,
                "export_per_kwh": 0.05,
                "rate": "shoulder",
                "constraints": [],
                "coasting_permitted": False,
                "allowance_kwh": None,
                "day_pattern": "Every day",
                "forecast": False,
            }
            for i in range(48)
        ]
    }


def _hourly(now) -> dict[str, Any]:
    return {
        "weather.home": {
            "forecast": [
                {
                    "datetime": (now + timedelta(hours=i)).isoformat(),
                    "temperature": 25.0 + i,
                }
                for i in range(12)
            ]
        }
    }


def _register(hass: HomeAssistant, domain: str, service: str, handler) -> None:
    hass.services.async_register(
        domain, service, handler, supports_response=SupportsResponse.ONLY
    )


async def _setup(hass: HomeAssistant, room_config: dict, **options: Any) -> Any:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ROOMS: []},
        options={CONF_ROOMS: [room_config], **options},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_temperature", "25.0")
    hass.states.async_set("sensor.test_humidity", "55.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set("climate.test", "off", {"hvac_modes": ["off", "cool"]})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def _attempts(hass: HomeAssistant, freezer, count: int) -> None:
    for _ in range(count):
        freezer.tick(STARTUP_FETCH_DELAY + timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _issue(hass: HomeAssistant, issue_id: str):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


# ---------------------------------------------------------------------------
# Tariff
# ---------------------------------------------------------------------------


async def test_the_tariff_is_read_at_startup(
    hass: HomeAssistant, room_config: dict, freezer
) -> None:
    async def get_intervals(call: ServiceCall) -> dict[str, Any]:
        assert call.data["config_entry_id"] == "tariff_entry"
        return _intervals(dt_util.utcnow())

    _register(hass, TARIFF_DOMAIN, TARIFF_SERVICE_GET_INTERVALS, get_intervals)
    coordinator = await _setup(hass, room_config, **{CONF_TARIFF_ENTRY_ID: "tariff_entry"})
    assert coordinator.tariff is None
    await _attempts(hass, freezer, 1)
    assert coordinator.tariff is not None
    assert len(coordinator.tariff.intervals) == 48
    assert _issue(hass, ISSUE_TARIFF_UNAVAILABLE) is None


async def test_a_missing_tariff_integration_is_reported_only_after_the_retries(
    hass: HomeAssistant, room_config: dict, freezer
) -> None:
    coordinator = await _setup(hass, room_config, **{CONF_TARIFF_ENTRY_ID: "tariff_entry"})
    await _attempts(hass, freezer, STARTUP_FETCH_ATTEMPTS - 1)
    assert _issue(hass, ISSUE_TARIFF_UNAVAILABLE) is None
    await _attempts(hass, freezer, 1)
    issue = _issue(hass, ISSUE_TARIFF_UNAVAILABLE)
    assert issue is not None
    assert "not installed" in issue.translation_placeholders["reason"]
    assert coordinator.tariff is None
    # No further attempts are booked once the last one has failed.
    await _attempts(hass, freezer, 2)


async def test_a_tariff_that_arrives_late_in_startup_is_still_read(
    hass: HomeAssistant, room_config: dict, freezer
) -> None:
    coordinator = await _setup(hass, room_config, **{CONF_TARIFF_ENTRY_ID: "tariff_entry"})
    await _attempts(hass, freezer, 1)
    _register(
        hass,
        TARIFF_DOMAIN,
        TARIFF_SERVICE_GET_INTERVALS,
        lambda call: _intervals(dt_util.utcnow()),
    )
    await _attempts(hass, freezer, 1)
    assert coordinator.tariff is not None


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (
            "raise",
            "tariff integration says no",
        ),
        ("garbage", "the interval series could not be read"),
    ],
)
async def test_a_tariff_that_cannot_be_read_holds_the_last_series(
    hass: HomeAssistant, room_config: dict, freezer, handler: str, reason: str
) -> None:
    state = {"mode": "good"}

    async def get_intervals(call: ServiceCall) -> dict[str, Any]:
        if state["mode"] == "raise":
            raise HomeAssistantError("tariff integration says no")
        if state["mode"] == "garbage":
            return {"intervals": "none"}
        return _intervals(dt_util.utcnow())

    _register(hass, TARIFF_DOMAIN, TARIFF_SERVICE_GET_INTERVALS, get_intervals)
    coordinator = await _setup(hass, room_config, **{CONF_TARIFF_ENTRY_ID: "tariff_entry"})
    await _attempts(hass, freezer, 1)
    held = coordinator.tariff
    assert held is not None

    state["mode"] = handler
    await coordinator._async_tariff_tick(dt_util.utcnow())
    await hass.async_block_till_done()
    assert coordinator.tariff is held
    assert reason in _issue(hass, ISSUE_TARIFF_UNAVAILABLE).translation_placeholders["reason"]

    state["mode"] = "good"
    await coordinator._async_tariff_tick(dt_util.utcnow())
    assert _issue(hass, ISSUE_TARIFF_UNAVAILABLE) is None


async def test_with_no_tariff_chosen_there_is_none(
    hass: HomeAssistant, room_config: dict
) -> None:
    coordinator = await _setup(hass, room_config)
    assert await coordinator._async_refresh_tariff()
    assert coordinator.tariff is None


# ---------------------------------------------------------------------------
# Weather forecast
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        ("raise", "weather says no"),
        ("garbage", "the forecast could not be read"),
    ],
)
async def test_a_forecast_that_cannot_be_read_holds_the_last_one(
    hass: HomeAssistant, room_config: dict, freezer, handler: str, reason: str
) -> None:
    state = {"mode": "good"}

    async def get_forecasts(call: ServiceCall) -> dict[str, Any]:
        if state["mode"] == "raise":
            raise HomeAssistantError("weather says no")
        if state["mode"] == "garbage":
            return {"weather.home": {"forecast": []}}
        return _hourly(dt_util.utcnow())

    _register(hass, WEATHER_DOMAIN, WEATHER_SERVICE_GET_FORECASTS, get_forecasts)
    coordinator = await _setup(hass, room_config, **{CONF_WEATHER_ENTITY: "weather.home"})
    await _attempts(hass, freezer, 1)
    held = coordinator.trajectory
    assert held is not None

    state["mode"] = handler
    await coordinator._async_forecast_tick(dt_util.utcnow())
    await hass.async_block_till_done()
    assert coordinator.trajectory is held
    issue = _issue(hass, ISSUE_FORECAST_UNAVAILABLE)
    assert reason in issue.translation_placeholders["reason"]

    state["mode"] = "good"
    await coordinator._async_forecast_tick(dt_util.utcnow())
    assert _issue(hass, ISSUE_FORECAST_UNAVAILABLE) is None


async def test_a_forecast_still_missing_after_the_retries_is_reported(
    hass: HomeAssistant, room_config: dict, freezer
) -> None:
    await _setup(hass, room_config, **{CONF_WEATHER_ENTITY: "weather.home"})
    await _attempts(hass, freezer, STARTUP_FETCH_ATTEMPTS)
    assert _issue(hass, ISSUE_FORECAST_UNAVAILABLE) is not None


async def test_with_no_weather_entity_there_is_no_forecast(
    hass: HomeAssistant, room_config: dict
) -> None:
    coordinator = await _setup(hass, room_config)
    assert await coordinator._async_refresh_forecast()
    assert coordinator.trajectory is None


# ---------------------------------------------------------------------------
# Solar forecast (DR-061)
# ---------------------------------------------------------------------------

_POWER = {
    CONF_BATTERY_SOC_ENTITY: "sensor.soc",
    CONF_BATTERY_CAPACITY_KWH: 13.5,
    CONF_SOLAR_POWER_ENTITY: "sensor.solar",
    CONF_HOUSE_LOAD_ENTITY: "sensor.load",
    CONF_RESERVE_MARGIN_KWH: 2.0,
}


def _forecast(now) -> dict[str, Any]:
    start = now.replace(minute=0, second=0, microsecond=0)
    return {
        "wh_hours": {
            (start + timedelta(hours=i)).isoformat(): 1000.0 for i in range(6)
        }
    }


async def test_a_solar_forecast_that_cannot_be_read_at_startup_is_reported_last(
    hass: HomeAssistant, room_config: dict, freezer
) -> None:
    source = MockConfigEntry(domain="forecast_solar", title="North")
    source.add_to_hass(hass)
    platform = AsyncMock(side_effect=HomeAssistantError("down"))
    with patch(_PLATFORMS, AsyncMock(return_value={"forecast_solar": platform})):
        await _setup(
            hass,
            room_config,
            **_POWER,
            **{CONF_SOLAR_FORECAST_ENTRIES: [source.entry_id]},
        )
        await _attempts(hass, freezer, STARTUP_FETCH_ATTEMPTS - 1)
        assert _issue(hass, ISSUE_SOLAR_FORECAST_UNAVAILABLE) is None
        await _attempts(hass, freezer, 1)
    issue = _issue(hass, ISSUE_SOLAR_FORECAST_UNAVAILABLE)
    assert "North" in issue.translation_placeholders["reason"]


async def test_the_energy_platforms_failing_is_reported(
    hass: HomeAssistant, room_config: dict
) -> None:
    with patch(_PLATFORMS, AsyncMock(side_effect=HomeAssistantError("no energy"))):
        coordinator = await _setup(
            hass, room_config, **_POWER, **{CONF_SOLAR_FORECAST_ENTRIES: ["x"]}
        )
        assert not await coordinator._async_refresh_solar_forecast()
    issue = _issue(hass, ISSUE_SOLAR_FORECAST_UNAVAILABLE)
    assert "energy platforms could not be read" in issue.translation_placeholders["reason"]


async def test_a_chosen_forecast_whose_entry_has_gone_is_named_by_id(
    hass: HomeAssistant, room_config: dict
) -> None:
    with patch(_PLATFORMS, AsyncMock(return_value={})):
        coordinator = await _setup(
            hass, room_config, **_POWER, **{CONF_SOLAR_FORECAST_ENTRIES: ["gone_entry"]}
        )
        await coordinator._async_refresh_solar_forecast()
    issue = _issue(hass, ISSUE_SOLAR_FORECAST_UNAVAILABLE)
    assert "gone_entry" in issue.translation_placeholders["reason"]


async def test_with_no_forecast_chosen_or_on_the_dashboard_there_is_none(
    hass: HomeAssistant, room_config: dict
) -> None:
    coordinator = await _setup(
        hass, room_config, **_POWER, **{CONF_SOLAR_FORECAST_ENTRIES: []}
    )
    assert await coordinator._async_refresh_solar_forecast()
    assert coordinator._solar_points is None
    assert coordinator._solar_forecast_slices(dt_util.utcnow(), 2.0) is None


async def test_an_unreadable_energy_dashboard_means_no_default(
    hass: HomeAssistant, room_config: dict
) -> None:
    with patch(
        "homeassistant.components.energy.data.async_get_manager",
        AsyncMock(side_effect=HomeAssistantError("energy not set up")),
    ):
        coordinator = await _setup(hass, room_config, **_POWER)
        assert await coordinator._async_solar_forecast_entries() == []


async def test_the_solar_forecast_is_read_again_on_its_timer(
    hass: HomeAssistant, room_config: dict, freezer
) -> None:
    source = MockConfigEntry(domain="forecast_solar", title="North")
    source.add_to_hass(hass)
    platform = AsyncMock(side_effect=lambda hass, entry_id: _forecast(dt_util.utcnow()))
    with patch(_PLATFORMS, AsyncMock(return_value={"forecast_solar": platform})):
        coordinator = await _setup(
            hass,
            room_config,
            **_POWER,
            **{CONF_SOLAR_FORECAST_ENTRIES: [source.entry_id]},
        )
        await _attempts(hass, freezer, 1)
        reads = platform.await_count
        assert reads >= 1
        freezer.tick(SOLAR_FORECAST_REFRESH_INTERVAL)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert platform.await_count > reads
    assert coordinator._solar_points


async def test_a_forecast_that_no_longer_reaches_now_is_not_used(
    hass: HomeAssistant, room_config: dict
) -> None:
    coordinator = await _setup(hass, room_config, **_POWER)
    now = dt_util.utcnow()
    coordinator._solar_points = [(now - timedelta(hours=5), 1000.0)]
    assert coordinator._solar_forecast_slices(now, 2.0) is None
