"""Setup and unload. NOT YET RUN."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from freezegun import freeze_time
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.abode_hvac_coordinator.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_RESERVE_MARGIN_KWH,
    CONF_ROOM_ID,
    CONF_ROOMS,
    CONF_SOLAR_POWER_ENTITY,
    DOMAIN,
    ISSUE_MISSING_COMFORT_INPUTS,
    ISSUE_SHARED_CLIMATE_ENTITY,
    SERVICE_HEADING_HOME,
)
from custom_components.abode_hvac_coordinator.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.abode_hvac_coordinator.forecast import ASSUMED_UNIT_KW
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
    assert state.attributes["actuator"] == "off"


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
    assert state.attributes["actuator"] == "off"
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
        assert state.attributes["actuator"] == "off", entity_id

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


async def test_a_room_missing_comfort_inputs_raises_an_issue_naming_it(
    hass: HomeAssistant, room_config: dict, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.9. A room saved before comfort inputs existed, or edited to drop

    them, is a pre-0.8.9 storage record — the schema requires both on new
    entry, but nothing migrates an existing one. The issue must name which
    room and which input, not just that something is missing, and must
    leave a fully-configured neighbour alone.
    """
    incomplete = {
        **room_config,
        CONF_ROOM_ID: "second_room",
        "name": "Second Room",
    }
    del incomplete[CONF_HUMIDITY_ENTITY]
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={
            **mock_config_entry.options,
            CONF_ROOMS: [*mock_config_entry.options[CONF_ROOMS], incomplete],
        },
    )

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    issues = ir.async_get(hass)
    issue = issues.async_get_issue(DOMAIN, ISSUE_MISSING_COMFORT_INPUTS)
    assert issue is not None
    rooms = issue.translation_placeholders["rooms"]
    assert "Second Room: humidity" in rooms
    assert "Test Room" not in rooms


async def test_a_fully_configured_room_raises_no_missing_comfort_issue(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The ordinary 0.8.9 case, with both inputs present, is untouched."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, ISSUE_MISSING_COMFORT_INPUTS) is None


async def test_solar_headroom_uses_the_pulldown_bin_draw_for_an_off_unit(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.9, finding 14, test 20.

    `_rated_kw_for` sizes headroom and energy figures against the pulldown
    bin specifically, on the stated reasoning that the unit being sized is
    off or about to be asked to pull toward target — not the at-setpoint
    bin, which would understate what a cold start actually draws.
    """
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    room = coordinator.rooms["test_room"]
    (group,) = room.groups
    draw = coordinator.draw_for(group)

    # Converge the pulldown bin specifically, away from both the seed value
    # and the at-setpoint bin, so a wrong bin choice is distinguishable.
    for _ in range(25):
        draw.observe(3, 3.4, quality=1.0)
    for _ in range(25):
        draw.observe(0, 0.6, quality=1.0)

    assert draw.bins[3].converged
    assert coordinator._rated_kw_for(room) == pytest.approx(3.4, abs=0.1)


async def test_two_rooms_in_one_group_share_one_draw_model(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.9, finding 14, test 18.

    Keyed by outdoor unit group, the same way `CompressorState` was keyed
    from 0.8.8. Study and guest share a compressor, and observing draw
    through either room's head must reach the one model, not two.
    """
    coordinator = await _setup_two_rooms(
        hass, mock_config_entry, share_outdoor_unit=True
    )
    study = coordinator.rooms["study"]
    guest = coordinator.rooms["guest"]
    (study_group,) = study.groups
    (guest_group,) = guest.groups
    assert study_group == guest_group == "Study and guest"

    for _ in range(25):
        coordinator.draw_for(study_group).observe(3, 2.7, quality=1.0)

    assert coordinator.draw_for(guest_group) is coordinator.draw_for(study_group)
    assert coordinator.draw_for(guest_group).bins[3].converged
    assert coordinator.draw_for(guest_group).bins[3].value == pytest.approx(
        2.7, abs=0.1
    )


async def test_no_house_load_sensor_means_no_draw_observations(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.9, finding 14, test 19.

    No `CONF_HOUSE_LOAD_ENTITY` configured: `_record_house_load_sample`
    returns immediately, nothing is ever attributed to any group, and every
    consumer of `_rated_kw_for` sees `ASSUMED_UNIT_KW` for as long as the
    house runs without that sensor.
    """
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    assert coordinator.house_load_entity_id is None

    for _ in range(5):
        await coordinator.async_refresh()

    room = coordinator.rooms["test_room"]
    (group,) = room.groups
    draw = coordinator.draw_for(group)
    assert draw.bins[3].samples == 0
    assert draw.pooled.samples == 0
    assert coordinator._rated_kw_for(room) == ASSUMED_UNIT_KW


async def _setup_running(hass, mock_config_entry, **states) -> None:
    """Set the entry up with the unit already cooling."""
    mock_config_entry.add_to_hass(hass)
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


async def test_an_open_window_stops_a_running_unit(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.7. The interlock reaches the hardware.

    Before this, the open-window rejection reached the trace and stopped
    there. The unit carried on cooling: the thermostat saw return air above
    setpoint, ran continuously, and never got there — unbounded, and nothing
    in the log.

    The mock is installed after setup because the climate integration loads
    during it and re-registers its own handler over anything placed earlier.
    """
    room = {
        **mock_config_entry.options[CONF_ROOMS][0],
        "opening_entity_ids": ["binary_sensor.test_window"],
    }
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_ROOMS: [room]},
    )
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set("binary_sensor.test_window", "off")
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

    coordinator = mock_config_entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "compressor", state.attributes

    calls = async_mock_service(hass, "climate", "set_hvac_mode")
    hass.states.async_set("binary_sensor.test_window", "on")
    with freeze_time(dt_util.utcnow() + timedelta(minutes=16)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "off", state.attributes
    assert [call.data["hvac_mode"] for call in calls] == ["off"], [
        call.data for call in calls
    ]

    # And it projects no energy. `_build_forecast` decided this from the mode,
    # which is the same hole in a second place: a room stopped for an open
    # window is in none of the stopped modes and projected a full horizon of
    # draw it was never going to take.
    assert coordinator.forecast is not None
    projection = next(
        room
        for room in coordinator.forecast.as_attributes()["rooms"]
        if room["room_id"] == "test_room"
    )
    assert projection["kwh"] == 0.0, projection


async def test_a_room_within_band_is_left_alone(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.7. NONE commands nothing.

    The majority of the controller's life. The unit holds the trimmed
    setpoint against its own sensor between our thirty-second decisions;
    stopping here would cycle the compressor every time a room reached
    comfort.
    """
    await _setup_running(
        hass,
        mock_config_entry,
        sensor__test_temperature="25.5",
        sensor__test_humidity="45.0",
        binary_sensor__test_presence="on",
    )

    calls = async_mock_service(hass, "climate", "set_hvac_mode")
    coordinator = mock_config_entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "none", state.attributes
    assert calls == [], [call.data for call in calls]


async def test_reaching_the_band_does_not_record_a_compressor_stop(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.7. The guard's unit of account.

    `wants` was derived from the step, `NONE` was ambiguous, and it resolved
    the ambiguity as "not running" — so every time a room reached its band
    the guard logged a stop that never happened, and a start ninety seconds
    later was refused for a compressor that had never gone off.
    """
    await _setup_running(
        hass,
        mock_config_entry,
        sensor__test_temperature="32.0",
        sensor__test_humidity="60.0",
        binary_sensor__test_presence="on",
    )
    coordinator = mock_config_entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "compressor", state.attributes
    assert coordinator.compressor_state()["climate.test"].running is True

    # The room reaches its band. Nothing stopped the compressor, so the guard
    # must not record that anything did.
    # Past the minimum run time, so a stop would actually be recorded rather
    # than refused. Inside it the guard refuses the phantom stop and `running`
    # stays True by accident, which makes the test pass against the defect.
    hass.states.async_set("sensor.test_temperature", "25.5")
    hass.states.async_set("sensor.test_humidity", "45.0")
    with freeze_time(dt_util.utcnow() + timedelta(minutes=16)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "none", state.attributes
    assert coordinator.compressor_state()["climate.test"].running is True

    # Ninety seconds later it is out of band again. The minimum *off* time
    # must not be applied, because the compressor never went off.
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    with freeze_time(dt_util.utcnow() + timedelta(minutes=17, seconds=30)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "compressor", state.attributes
    assert not any(
        "minutes" in reason and "off" in reason
        for reason in state.attributes["rejected"]
    ), state.attributes["rejected"]


async def test_diagnostics_report_which_attribute_answered(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.7. `action_sources` had no caller anywhere in the repository.

    It is item 2 on the next-install watch list and it establishes whether
    the adapter publishes `hvac_action` — the reading that gates every
    learned sensible coefficient.
    """
    await _setup_running(
        hass,
        mock_config_entry,
        sensor__test_temperature="32.0",
        sensor__test_humidity="60.0",
        binary_sensor__test_presence="on",
    )
    coordinator = mock_config_entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diagnostics["action_sources"]["test_room"] == "hvac_action"


async def test_diagnostics_report_a_unit_publishing_no_action(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, caplog
) -> None:
    """A room learning from the mode string, and it says so once."""
    mock_config_entry.add_to_hass(hass)
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set(
        "climate.test",
        "cool",
        {
            "hvac_modes": ["off", "cool", "dry", "fan_only"],
            "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE.value,
        },
    )
    # The level has to be set around setup: the first evaluation runs inside
    # it, and the line is emitted once per room.
    caplog.clear()
    with caplog.at_level(
        logging.INFO, logger="custom_components.abode_hvac_coordinator"
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_config_entry.runtime_data
        await _let_occupancy_settle(hass, coordinator)
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diagnostics["action_sources"]["test_room"] == "hvac_mode"
    # Once per room, not once per evaluation.
    assert (
        sum(
            "publishes no hvac_action" in record.message
            for record in caplog.records
        )
        == 1
    ), [record.message for record in caplog.records]


async def test_a_door_open_briefly_does_not_stop_the_unit(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.7. The debounce, end to end.

    Someone carries washing through. The interlock fires — nothing new is
    actuated into an open room — but the compressor is not stopped, so it
    does not then sit out the five-minute minimum off for a twenty-second
    door.
    """
    room = {
        **mock_config_entry.options[CONF_ROOMS][0],
        "opening_entity_ids": ["binary_sensor.test_door"],
    }
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_ROOMS: [room]},
    )
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set("binary_sensor.test_door", "off")
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

    coordinator = mock_config_entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "compressor", state.attributes

    calls = async_mock_service(hass, "climate", "set_hvac_mode")
    hass.states.async_set("binary_sensor.test_door", "on")
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "none", state.attributes
    assert calls == [], [call.data for call in calls]

    # Still open later. Now it stops. Far enough out that the ten-minute
    # minimum run is not what is holding the unit — inside it the guard
    # refuses the stop and sets `hold_compressor`, which would make this pass
    # for a reason that has nothing to do with the debounce.
    with freeze_time(dt_util.utcnow() + timedelta(minutes=20)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] == "off", state.attributes
    assert [call.data["hvac_mode"] for call in calls] == ["off"], [
        call.data for call in calls
    ]


def _two_room_options(base: dict, *, share_outdoor_unit: bool) -> dict:
    """Study and guest, each with one head, on one or two outdoor units."""
    template = base[CONF_ROOMS][0]
    group = {"climate.study": "Study and guest"} if share_outdoor_unit else {}
    guest_group = {"climate.guest": "Study and guest"} if share_outdoor_unit else {}
    return {
        **base,
        CONF_ROOMS: [
            {
                **template,
                "room_id": "study",
                "name": "Study",
                "climate_entity_ids": ["climate.study"],
                "head_groups": group,
            },
            {
                **template,
                "room_id": "guest",
                "name": "Guest",
                "climate_entity_ids": ["climate.guest"],
                "head_groups": guest_group,
            },
        ],
    }


async def _setup_two_rooms(hass, entry, *, share_outdoor_unit: bool):
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        hass.config_entries.async_get_entry(entry.entry_id),
        options=_two_room_options(
            entry.options, share_outdoor_unit=share_outdoor_unit
        ),
    )
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    for entity_id in ("climate.study", "climate.guest"):
        hass.states.async_set(
            entity_id,
            "off",
            {
                "hvac_modes": ["off", "cool", "dry", "fan_only"],
                "hvac_action": "off",
                "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE.value,
            },
        )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)
    return coordinator


async def test_two_rooms_on_one_outdoor_unit_share_one_compressor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.8. `MIN_RUN` and `MIN_OFF` protect a compressor, not a room.

    Keyed by room, starting the second room's head while the first was
    already running was refused as a compressor start.
    """
    coordinator = await _setup_two_rooms(
        hass, mock_config_entry, share_outdoor_unit=True
    )
    compressors = coordinator.compressor_state()
    assert list(compressors) == ["Study and guest"], compressors
    assert compressors["Study and guest"].running is True

    # Neither room was refused a start on the other's account.
    for room_id in ("study", "guest"):
        state = hass.states.get(f"sensor.{room_id}_mode")
        assert state is not None
        assert state.attributes["actuator"] == "compressor", state.attributes
        assert not any(
            "short-cycle guard" in reason
            for reason in state.attributes["rejected"]
        ), state.attributes["rejected"]


async def test_two_rooms_on_separate_outdoor_units_are_separate_compressors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Declaring nothing must leave a house exactly as it was."""
    coordinator = await _setup_two_rooms(
        hass, mock_config_entry, share_outdoor_unit=False
    )
    assert sorted(coordinator.compressor_state()) == [
        "climate.guest",
        "climate.study",
    ]


async def test_one_room_reaching_its_band_does_not_stop_its_neighbour(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The compressor is still called on, so it must not read as stopped.

    Both rooms read the same sensors here; the study is given a band wide
    enough to sit inside and the guest room a band it is well above. So the
    study decides NONE while the guest room decides COMPRESSOR, on one shared
    outdoor unit.

    Derive the compressor's demand from the study alone and its NONE records
    a stop — after which the guest room's start is refused inside the minimum
    off time, for a compressor that never went off.
    """
    template = mock_config_entry.options[CONF_ROOMS][0]
    rooms = [
        {
            **template,
            "room_id": "study",
            "name": "Study",
            "climate_entity_ids": ["climate.study"],
            "head_groups": {"climate.study": "Study and guest"},
            "bands": {"occupied": {"low": 10.0, "high": 40.0}},
        },
        {
            **template,
            "room_id": "guest",
            "name": "Guest",
            "climate_entity_ids": ["climate.guest"],
            "head_groups": {"climate.guest": "Study and guest"},
        },
    ]
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_ROOMS: rooms},
    )
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    for entity_id in ("climate.study", "climate.guest"):
        hass.states.async_set(
            entity_id,
            "off",
            {
                "hvac_modes": ["off", "cool", "dry", "fan_only"],
                "hvac_action": "off",
                "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE.value,
            },
        )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)

    study = hass.states.get("sensor.study_mode")
    guest = hass.states.get("sensor.guest_mode")
    assert study is not None and guest is not None
    assert study.attributes["actuator"] == "none", study.attributes
    assert guest.attributes["actuator"] == "compressor", guest.attributes
    assert coordinator.compressor_state()["Study and guest"].running is True
    assert not any(
        "short-cycle guard" in reason for reason in guest.attributes["rejected"]
    ), guest.attributes["rejected"]


async def test_a_room_with_two_heads_commands_both(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.8. One band, one target, one setpoint — sent to both heads."""
    room = {
        **mock_config_entry.options[CONF_ROOMS][0],
        "climate_entity_ids": ["climate.lounge_n", "climate.lounge_s"],
    }
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_ROOMS: [room]},
    )
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    for entity_id in ("climate.lounge_n", "climate.lounge_s"):
        hass.states.async_set(
            entity_id,
            "off",
            {
                "hvac_modes": ["off", "cool", "dry", "fan_only"],
                "hvac_action": "off",
                "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE.value,
            },
        )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    calls = async_mock_service(hass, "climate", "set_hvac_mode")
    coordinator = mock_config_entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)

    commanded = {call.data[ATTR_ENTITY_ID] for call in calls}
    assert commanded == {"climate.lounge_n", "climate.lounge_s"}, [
        call.data for call in calls
    ]


async def test_a_room_can_only_do_what_all_its_heads_can_do(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The intersection.

    Claiming dry mode because one of two heads has it produces a decision the
    actuator cannot carry out on the other.
    """
    room = {
        **mock_config_entry.options[CONF_ROOMS][0],
        "climate_entity_ids": ["climate.lounge_n", "climate.lounge_s"],
    }
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_ROOMS: [room]},
    )
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "80.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set(
        "climate.lounge_n",
        "off",
        {
            "hvac_modes": ["off", "cool", "dry"],
            "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE.value,
        },
    )
    hass.states.async_set(
        "climate.lounge_s",
        "off",
        {
            "hvac_modes": ["off", "cool"],
            "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE.value,
        },
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)

    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.attributes["actuator"] != "dry", state.attributes


async def test_a_v1_entry_migrates_to_a_list_of_heads(
    hass: HomeAssistant,
) -> None:
    """0.8.8. A version bump ships a migration, always.

    No outdoor unit groups are written: every existing head becomes its own
    compressor, which is how the guard behaved before the change.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            CONF_ROOMS: [
                {
                    "room_id": "office",
                    "name": "Office",
                    "climate_entity_id": "climate.office",
                    "bands": {"occupied": {"low": 24.0, "high": 27.0}},
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    migrated = entry.data[CONF_ROOMS][0]
    assert migrated["climate_entity_ids"] == ["climate.office"]
    assert "climate_entity_id" not in migrated
    assert not migrated.get("head_groups")

    coordinator = entry.runtime_data
    assert coordinator.rooms["office"].climate_entity_ids == ("climate.office",)
    assert coordinator.rooms["office"].groups == ("climate.office",)


async def test_an_empty_room_does_not_stop_its_neighbours_compressor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """0.8.8. The compressor's demand is the OR across the rooms on it.

    An unoccupied study decides OFF and its own head is switched off. The
    outdoor unit is still driving the guest room's head, so it must not be
    recorded as stopped — otherwise the guest room's next start is refused
    inside the minimum off time, for a compressor that never stopped.
    """
    template = mock_config_entry.options[CONF_ROOMS][0]
    rooms = [
        {
            **template,
            "room_id": "study",
            "name": "Study",
            "climate_entity_ids": ["climate.study"],
            "head_groups": {"climate.study": "Study and guest"},
            "presence_entity_id": "binary_sensor.study_presence",
        },
        {
            **template,
            "room_id": "guest",
            "name": "Guest",
            "climate_entity_ids": ["climate.guest"],
            "head_groups": {"climate.guest": "Study and guest"},
        },
    ]
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_ROOMS: rooms},
    )
    hass.states.async_set("sensor.test_temperature", "32.0")
    hass.states.async_set("sensor.test_humidity", "60.0")
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set("binary_sensor.study_presence", "off")
    for entity_id in ("climate.study", "climate.guest"):
        hass.states.async_set(
            entity_id,
            "off",
            {
                "hvac_modes": ["off", "cool", "dry", "fan_only"],
                "hvac_action": "off",
                "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE.value,
            },
        )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data
    await _let_occupancy_settle(hass, coordinator)

    # Past the minimum run time. Inside it the phantom stop is refused rather
    # than recorded, so the compressor stays running by accident and this
    # would pass against the defect.
    with freeze_time(dt_util.utcnow() + timedelta(minutes=20)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    study = hass.states.get("sensor.study_mode")
    guest = hass.states.get("sensor.guest_mode")
    assert study is not None and guest is not None
    assert study.attributes["actuator"] == "off", study.attributes
    assert guest.attributes["actuator"] == "compressor", guest.attributes
    assert coordinator.compressor_state()["Study and guest"].running is True
    assert not any(
        "short-cycle guard" in reason for reason in guest.attributes["rejected"]
    ), guest.attributes["rejected"]
    # And the study's own head really was switched off. A refused phantom
    # stop sets `hold_compressor`, which suppresses it.
    assert not study.attributes.get("hold_compressor"), study.attributes
