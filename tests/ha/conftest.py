"""Fixtures for the tests through Home Assistant: a house with one room.

Each test sets up a config entry as a user would and checks states and
actions; none calls the component's functions directly [R18].
"""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component

import custom_components  # noqa: F401 - imported first, so Home Assistant finds this repository's integration

from .harness import (
    MockConfigEntry,
    async_mock_service,
)

DOMAIN = "abode_hvac_coordinator"
ROOM_ID = "0123456789abcdef0123456789abcdef"

CLIMATE_ATTRS: dict[str, Any] = {
    "hvac_modes": ["off", "cool", "heat", "dry", "fan_only"],
    "fan_modes": ["auto", "quiet", "high"],
    "swing_modes": ["auto", "fixed"],
    "min_temp": 16.0,
    "max_temp": 30.0,
    "target_temp_step": 0.5,
    "supported_features": 1 | 8 | 32,
    "temperature": 24.0,
    "current_temperature": 25.0,
    "fan_mode": "auto",
    "swing_mode": "fixed",
}

HOUSE: dict[str, Any] = {"outdoor_temperature": "sensor.outdoor_t", "outdoor_humidity": "sensor.outdoor_rh"}


def profile_dict() -> dict[str, Any]:
    return {"hvac_modes": ["off", "cool", "heat", "dry", "fan_only"], "fan_modes": ["auto", "quiet", "high"],
            "vertical_vanes": ["auto", "fixed"], "horizontal_vanes": [], "min_setpoint": 16.0, "max_setpoint": 30.0,
            "setpoint_step": 0.5, "range_only": False, "step_reason": None}


def stored_room(room_id: str = ROOM_ID, name: str = "Office", climate: str = "climate.office",
                **extra: Any) -> dict[str, Any]:
    room: dict[str, Any] = {
        "room_id": room_id, "name": name, "heads": [{"climate": climate}],
        "temperature_sensor": "sensor.office_t", "humidity_sensor": "sensor.office_rh",
        "occupied_low": 23.0, "occupied_high": 26.0, "sleep_low": 20.0, "sleep_high": 23.0,
        "precool_low": 23.0, "precool_high": 26.0, "power_management": "off", "profile": profile_dict(),
    }
    room.update(extra)
    return room


def set_house_states(hass: HomeAssistant, climate: str = "climate.office", mode: str = "cool") -> None:
    hass.states.async_set(climate, mode, CLIMATE_ATTRS)
    hass.states.async_set("sensor.office_t", "25.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.office_rh", "55", {"unit_of_measurement": "%", "device_class": "humidity"})
    hass.states.async_set("sensor.outdoor_t", "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.outdoor_rh", "50", {"unit_of_measurement": "%", "device_class": "humidity"})


@pytest.fixture
async def climate_calls(hass: HomeAssistant) -> dict[str, list[ServiceCall]]:
    """Every climate service the component may call, recorded. The climate
    integration is set up first, so its own services do not replace these."""
    assert await async_setup_component(hass, "climate", {})
    return {s: async_mock_service(hass, "climate", s) for s in
            ("set_hvac_mode", "set_temperature", "set_fan_mode", "set_swing_mode", "set_swing_horizontal_mode")}


async def setup_entry(hass: HomeAssistant, *, data: dict[str, Any] | None = None,
                      rooms: list[dict[str, Any]] | None = None, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Abode HVAC Coordinator", data=HOUSE if data is None else data,
                            options={"rooms": [stored_room()] if rooms is None else rooms, **options},
                            version=1, minor_version=1)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)  # seeding a new room
    return entry
