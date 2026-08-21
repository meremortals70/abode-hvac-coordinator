"""Setup and unload. NOT YET RUN."""

from __future__ import annotations

from datetime import timedelta

from freezegun import freeze_time
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.abode_hvac_coordinator.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_RESERVE_MARGIN_KWH,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SOLAR_POWER_ENTITY,
    DOMAIN,
    ISSUE_SHARED_CLIMATE_ENTITY,
    SERVICE_HEADING_HOME,
)
from custom_components.abode_hvac_coordinator.tariff import TariffSeries
from custom_components.abode_hvac_coordinator.thermal import Coefficient


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
    """A no-import interval in force now, followed by one that clears it.

    The clearing interval matters: `hours_until_clear` returns None for a
    series in which the constraint never lifts, and since 0.8.6 that is a
    fail-open case — there is no relief time to project a battery against, so
    the room keeps its comfort. A series that does clear is what puts the
    arithmetic in charge.
    """
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
                },
                {
                    "start_time": (now + timedelta(hours=6)).isoformat(),
                    "end_time": (now + timedelta(hours=12)).isoformat(),
                    "rate": "off_peak",
                    "per_kwh": 0.20,
                    "export_per_kwh": 0.05,
                    "constraints": [],
                },
            ]
        },
        now,
    )


async def _setup_with_power(hass, mock_config_entry, **states) -> None:
    """Set the entry up with all five power inputs configured.

    `add_to_hass` first: `async_update_entry` raises `UnknownEntry` for an
    entry the manager has never seen, which is why this test had never passed.
    """
    mock_config_entry.add_to_hass(hass)
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
    for entity_id, value in states.items():
        hass.states.async_set(entity_id.replace("__", "."), value)
    hass.states.async_set(
        "climate.test",
        "cool",
        {
            "hvac_modes": ["off", "cool", "dry", "fan_only"],
            "hvac_action": "cooling",
            "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE.value,
        },
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()


async def _let_occupancy_settle(hass, coordinator) -> None:
    """Advance past the occupancy grace so the room reaches OCCUPIED.

    Raw presence is not the signal the controller acts on: a room has to be
    occupied for the grace period before the compressor is allowed to start.
    """
    with freeze_time(dt_util.utcnow() + timedelta(minutes=5)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()


async def test_an_unprojectable_room_keeps_its_comfort(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.6. Power management fails open, not closed.

    This asserted the opposite until 0.8.6, and the opposite was the defect:
    a freshly set-up room has no converged thermal model and no solved target,
    so `_power_available` could not project a need — and returned False, which
    stopped the compressor in an occupied room for the whole of a no-import
    window. Every fresh install is in exactly that state.

    Comfort is a hard constraint. A projection the controller cannot make is
    not grounds for withdrawing it, and the room must keep cooling.
    """
    await _setup_with_power(
        hass,
        mock_config_entry,
        sensor__test_temperature="30.0",
        sensor__test_humidity="60.0",
        binary_sensor__test_presence="on",
        sensor__battery_soc="20.0",
        sensor__solar_power="0",
        sensor__house_load="500",
    )

    coordinator = mock_config_entry.runtime_data
    coordinator.tariff = _no_grid_import_series(dt_util.utcnow())
    await _let_occupancy_settle(hass, coordinator)

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert not any(
        "no grid import permitted" in reason
        for reason in state.attributes["rejected"]
    ), state.attributes["rejected"]
    assert state.attributes["actuator"] == "compressor", state.attributes


async def test_an_unconverged_model_keeps_its_comfort(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.6. The unconverged-model path specifically, with a target present.

    Distinct from the test above, which exercises the no-target-yet path. This
    one gives the room a solved target so the check reaches `energy_for`, and
    leaves the thermal model unconverged so `energy_for` returns None. That is
    the state a fresh install sits in for its first days, and it must not cost
    the room its comfort.
    """
    await _setup_with_power(
        hass,
        mock_config_entry,
        sensor__test_temperature="30.0",
        sensor__test_humidity="60.0",
        binary_sensor__test_presence="on",
        sensor__battery_soc="20.0",
        sensor__solar_power="0",
        sensor__house_load="500",
    )

    coordinator = mock_config_entry.runtime_data
    coordinator.tariff = _no_grid_import_series(dt_util.utcnow())
    coordinator._last_target["test_room"] = 24.0
    assert not coordinator.model_for("test_room").k_sensible.converged

    await _let_occupancy_settle(hass, coordinator)

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "compressor", state.attributes
    assert not any(
        "no grid import permitted" in reason
        for reason in state.attributes["rejected"]
    )


async def test_a_projected_shortfall_still_holds_the_compressor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.6. A computed shortfall is still a refusal.

    Failing open applies to what the controller cannot answer, not to what it
    can. With a converged model and a solved target, 20% of 10 kWh against an
    8 kWh reserve margin leaves nothing to spend and the room is refused.
    """
    await _setup_with_power(
        hass,
        mock_config_entry,
        sensor__test_temperature="30.0",
        sensor__test_humidity="60.0",
        binary_sensor__test_presence="on",
        sensor__battery_soc="20.0",
        sensor__solar_power="0",
        sensor__house_load="500",
    )

    coordinator = mock_config_entry.runtime_data
    coordinator.tariff = _no_grid_import_series(dt_util.utcnow())

    # Converge the room's model and give it a solved target, so the projection
    # is available and the arithmetic — not an unknown — decides.
    model = coordinator.model_for("test_room")
    model.k_sensible = Coefficient(value=2.0, variance=0.01, samples=40)
    model.k_loss = Coefficient(value=0.15, variance=0.01, samples=40)
    model.k_solar = Coefficient(value=1.0, variance=0.01, samples=40)
    coordinator._last_target["test_room"] = 24.0

    await _let_occupancy_settle(hass, coordinator)

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "none"
    assert any(
        "no grid import permitted" in reason
        for reason in state.attributes["rejected"]
    )


async def test_two_rooms_on_one_climate_entity_are_locked_out(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.6. One climate entity cannot be driven by two rooms.

    Both rooms solve their own setpoint and command the same entity every
    cycle, and each one's dedupe cache sees only its own writes — so nothing
    errors and the unit does whatever arrived last. The configuration was
    reachable through the options flow and produced no log line at all.
    """
    rooms = list(mock_config_entry.options[CONF_ROOMS])
    duplicate = {**rooms[0], CONF_ROOM_ID: "second_room", "name": "Second Room"}
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_ROOMS: [*rooms, duplicate]},
    )
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    for entity_id in ("sensor.test_room_mode", "sensor.second_room_mode"):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.attributes["mode"] == "lockout", entity_id
        assert state.attributes["actuator"] == "none", entity_id

    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, ISSUE_SHARED_CLIMATE_ENTITY) is not None


async def test_one_room_per_climate_entity_raises_no_issue(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The ordinary case is untouched."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, ISSUE_SHARED_CLIMATE_ENTITY) is None
