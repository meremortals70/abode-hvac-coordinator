"""The cycle through Home Assistant (Section 8, coordinator.py): the reads
on their timers, the house's figures, repairs, speech, failures, midnight,
seeding and the diagnostics download."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import (
    CoreState,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .conftest import (
    CLIMATE_ATTRS,
    DOMAIN,
    HOUSE,
    ROOM_ID,
    set_house_states,
    setup_entry,
    stored_room,
)
from .harness import (
    ClientSessionGenerator,
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
    async_wait_recording_done,
    get_diagnostics_for_config_entry,
)

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")

TARIFF = "tariff_entry_1"


def _intervals(rule: list[str] | None = None) -> dict[str, Any]:
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    return {"intervals": [
        {"start_time": (now - timedelta(hours=1)).isoformat(), "end_time": (now + timedelta(hours=1)).isoformat(),
         "per_kwh": 0.30, "rate": "peak", "constraints": rule or [], "enforceable_constraints": rule or []},
        {"start_time": (now + timedelta(hours=1)).isoformat(), "end_time": (now + timedelta(hours=9)).isoformat(),
         "per_kwh": 0.20, "rate": "shoulder", "constraints": [], "enforceable_constraints": []},
    ]}


class Tariff:
    """A stand-in for Abode Power Tariffs' get_intervals action."""

    def __init__(self, hass: HomeAssistant, response: dict[str, Any]) -> None:
        self.response: dict[str, Any] | None = response
        self.calls: list[dict[str, Any]] = []
        hass.services.async_register("abode_power_tariffs", "get_intervals", self._call,
                                     supports_response=SupportsResponse.ONLY)

    async def _call(self, call: ServiceCall) -> ServiceResponse:
        self.calls.append(dict(call.data))
        if self.response is None:
            raise HomeAssistantError("the tariff is not loaded")
        return self.response


def _issue(hass: HomeAssistant, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


@pytest.fixture
def solar_source(hass: HomeAssistant) -> Any:
    """A loaded forecast source, back to not loaded before Home Assistant
    stops, so the teardown does not try to unload an integration that is
    not installed."""
    source = MockConfigEntry(domain="forecast_solar", title="Roof")
    source.add_to_hass(hass)
    source.mock_state(hass, ConfigEntryState.LOADED)
    yield source
    source.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)  # the cycle runs as a background task


async def test_the_tariff_prices_the_plan(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    tariff = Tariff(hass, _intervals(["no_grid_import"]))
    await setup_entry(hass, data={**HOUSE, "tariff_entry": TARIFF})
    assert tariff.calls == [{"config_entry_id": TARIFF, "hours": 24, "resolution_minutes": 30}]
    rate = hass.states.get("sensor.abode_hvac_coordinator_tariff_rate_now")
    assert rate is not None and float(rate.state) == 0.30
    assert rate.attributes["unit_of_measurement"] == f"{hass.config.currency}/kWh"
    assert rate.attributes["rate"] == "peak"
    constraints = hass.states.get("sensor.abode_hvac_coordinator_constraints_in_force")
    assert constraints is not None and constraints.attributes["constraints"] == ["no_grid_import"]
    demand = hass.states.get("sensor.abode_hvac_coordinator_demand_forecast")
    assert demand is not None and float(demand.state) > 0.0  # the plan runs the unit [D005]
    assert len(demand.attributes["intervals"]) == 1  # up to the next tariff change
    assert demand.attributes["to_tariff_change"] is True
    assert demand.attributes["until"] == tariff.response["intervals"][1]["start_time"]  # type: ignore[index]
    cost = hass.states.get("sensor.abode_hvac_coordinator_projected_cost")
    assert cost is not None and float(cost.state) > 0.0


async def test_a_failed_tariff_read_raises_a_repair_and_clears(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    tariff = Tariff(hass, _intervals())
    tariff.response = None
    await setup_entry(hass, data={**HOUSE, "tariff_entry": TARIFF})
    issue = _issue(hass, "tariff_unreadable")
    assert issue is not None and issue.translation_placeholders == {"error": "the tariff is not loaded"}
    rate = hass.states.get("sensor.abode_hvac_coordinator_tariff_rate_now")
    assert rate is not None and rate.state == "unknown"  # every hour priced the same [D004]
    tariff.response = _intervals(["something_new"])
    await _tick(hass, freezer, timedelta(minutes=15))
    await _tick(hass, freezer, timedelta(seconds=30))
    assert _issue(hass, "tariff_unreadable") is None
    unknown = _issue(hass, "unknown_constraint")
    assert unknown is not None and unknown.translation_placeholders == {"constraints": "something_new"}  # [D034]
    tariff.response = _intervals(["something_else"])
    await _tick(hass, freezer, timedelta(minutes=15))
    await _tick(hass, freezer, timedelta(seconds=30))
    unknown = _issue(hass, "unknown_constraint")
    assert unknown is not None and unknown.translation_placeholders == {"constraints": "something_else"}  # updated


async def test_a_tariff_answer_that_cannot_be_read(hass: HomeAssistant,
                                                   climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    Tariff(hass, {"intervals": "no"})
    await setup_entry(hass, data={**HOUSE, "tariff_entry": TARIFF})
    issue = _issue(hass, "tariff_unreadable")
    assert issue is not None and issue.translation_placeholders == {"error": "the response has no intervals"}


async def test_the_weather_forecast(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]],
                                    freezer: FrozenDateTimeFactory) -> None:
    set_house_states(hass)
    hass.states.async_set("weather.home", "sunny", {"temperature_unit": "°C", "wind_speed_unit": "km/h"})
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    hours = [{"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 30.0 + h, "humidity": 50,
              "wind_speed": 10.0, "cloud_coverage": 20} for h in range(8)]
    answers: list[Any] = [{"weather.home": {"forecast": hours}}]

    async def forecasts(call: ServiceCall) -> ServiceResponse:
        reply = answers[0]
        if isinstance(reply, Exception):
            raise reply
        return reply  # type: ignore[no-any-return]

    hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)
    await setup_entry(hass, data={**HOUSE, "weather": "weather.home"})
    peak = hass.states.get("sensor.abode_hvac_coordinator_forecast_peak")
    assert peak is not None and float(peak.state) == 37.0
    answers[0] = HomeAssistantError("no forecast")
    await _tick(hass, freezer, timedelta(minutes=30))
    await _tick(hass, freezer, timedelta(seconds=30))  # the cycle after the read
    peak = hass.states.get("sensor.abode_hvac_coordinator_forecast_peak")
    assert peak is not None and peak.state == "unknown"


async def test_the_solar_forecast(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]],
                                  freezer: FrozenDateTimeFactory, solar_source: MockConfigEntry) -> None:
    set_house_states(hass)
    hass.states.async_set("sensor.solar", "3.0", {"unit_of_measurement": "kW"})
    source = solar_source
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    replies: list[Any] = [{"wh_hours": {(start + timedelta(hours=h)).isoformat(): 1500 for h in range(6)}}]

    async def forecast(hass: HomeAssistant, entry_id: str) -> Any:
        if isinstance(replies[0], Exception):
            raise replies[0]
        return replies[0]

    with patch("homeassistant.components.energy.websocket_api.async_get_energy_platforms",
               return_value={"forecast_solar": forecast}):
        await setup_entry(hass, data={**HOUSE, "solar_power": "sensor.solar",
                                      "solar_forecast_sources": [source.entry_id]})
        assert _issue(hass, "solar_forecast_unreadable") is None
        replies[0] = ValueError("the forecast is offline")
        await _tick(hass, freezer, timedelta(minutes=15))
        issue = _issue(hass, "solar_forecast_unreadable")
        assert issue is not None and issue.translation_placeholders == {"error": "the forecast is offline"}


async def test_a_solar_source_still_setting_up_is_read_later(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
    solar_source: MockConfigEntry,
) -> None:
    """A forecast source still setting up at boot is not read (its platform
    would read runtime_data before it is set); nothing is wrong yet, and it
    is read once loaded [D061][HA]."""
    set_house_states(hass)
    hass.states.async_set("sensor.solar", "3.0", {"unit_of_measurement": "kW"})
    source = solar_source
    source.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    calls: list[str] = []

    async def forecast(hass: HomeAssistant, entry_id: str) -> Any:
        calls.append(entry_id)
        if hass.config_entries.async_get_entry(entry_id).state is not ConfigEntryState.LOADED:  # type: ignore[union-attr]
            raise AttributeError("'ConfigEntry' object has no attribute 'runtime_data'")
        return {"wh_hours": {(start + timedelta(hours=h)).isoformat(): 1500 for h in range(6)}}

    with patch("homeassistant.components.energy.websocket_api.async_get_energy_platforms",
               return_value={"forecast_solar": forecast}):
        entry = await setup_entry(hass, data={**HOUSE, "solar_power": "sensor.solar",
                                              "solar_forecast_sources": [source.entry_id]})
        assert entry.state is ConfigEntryState.LOADED
        assert calls == [] and _issue(hass, "solar_forecast_unreadable") is None
        source.mock_state(hass, ConfigEntryState.LOADED)
        await _tick(hass, freezer, timedelta(minutes=15))
        assert calls == [source.entry_id] and _issue(hass, "solar_forecast_unreadable") is None
        source.mock_state(hass, ConfigEntryState.SETUP_ERROR)  # failed for good: read, and its error shown
        await _tick(hass, freezer, timedelta(minutes=15))
        assert _issue(hass, "solar_forecast_unreadable") is not None


async def test_a_disabled_solar_source_is_not_waited_for(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """A disabled source is never set up again, so it is not waited for
    and the forecast repair says nothing was read [D061]."""
    set_house_states(hass)
    hass.states.async_set("sensor.solar", "3.0", {"unit_of_measurement": "kW"})
    source = MockConfigEntry(domain="forecast_solar", title="Roof", disabled_by=ConfigEntryDisabler.USER)
    source.add_to_hass(hass)

    async def forecast(hass: HomeAssistant, entry_id: str) -> Any:
        raise AssertionError("a disabled source is not read")

    with patch("homeassistant.components.energy.websocket_api.async_get_energy_platforms",
               return_value={"forecast_solar": forecast}):
        await setup_entry(hass, data={**HOUSE, "solar_power": "sensor.solar",
                                      "solar_forecast_sources": [source.entry_id]})
    assert _issue(hass, "solar_forecast_unreadable") is not None


async def test_a_disabled_solar_source_is_not_waited_for_even_at_boot(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    set_house_states(hass)
    hass.states.async_set("sensor.solar", "3.0", {"unit_of_measurement": "kW"})
    source = MockConfigEntry(domain="forecast_solar", title="Roof", disabled_by=ConfigEntryDisabler.USER)
    source.add_to_hass(hass)
    hass.set_state(CoreState.starting)
    with patch("homeassistant.components.energy.websocket_api.async_get_energy_platforms", return_value={}):
        await setup_entry(hass, data={**HOUSE, "solar_power": "sensor.solar",
                                      "solar_forecast_sources": [source.entry_id]})
    hass.set_state(CoreState.running)
    assert _issue(hass, "solar_forecast_unreadable") is not None  # disabled: never set up again [D061]


async def test_a_solar_source_not_yet_set_up_at_boot_raises_nothing(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    """While Home Assistant starts, a source whose integration is not set up
    yet has no energy platform registered (helpers/integration_platform.py)
    and is on its way: nothing is wrong yet [D061][HA]."""
    set_house_states(hass)
    hass.states.async_set("sensor.solar", "3.0", {"unit_of_measurement": "kW"})
    source = MockConfigEntry(domain="forecast_solar", title="Roof")
    source.add_to_hass(hass)
    hass.set_state(CoreState.starting)
    with patch("homeassistant.components.energy.websocket_api.async_get_energy_platforms", return_value={}):
        await setup_entry(hass, data={**HOUSE, "solar_power": "sensor.solar",
                                      "solar_forecast_sources": [source.entry_id]})
    hass.set_state(CoreState.running)
    assert _issue(hass, "solar_forecast_unreadable") is None


async def test_a_solar_source_raising_anything_raises_the_repair(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], solar_source: MockConfigEntry
) -> None:
    set_house_states(hass)
    hass.states.async_set("sensor.solar", "3.0", {"unit_of_measurement": "kW"})
    source = solar_source

    async def forecast(hass: HomeAssistant, entry_id: str) -> Any:
        raise AttributeError("runtime_data")

    with patch("homeassistant.components.energy.websocket_api.async_get_energy_platforms",
               return_value={"forecast_solar": forecast}):
        entry = await setup_entry(hass, data={**HOUSE, "solar_power": "sensor.solar",
                                              "solar_forecast_sources": [source.entry_id]})
    assert entry.state is ConfigEntryState.LOADED  # never a setup error [D061]
    assert _issue(hass, "solar_forecast_unreadable") is not None


async def test_the_energy_dashboards_solar_forecast_is_used_when_none_is_chosen(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], hass_client: ClientSessionGenerator,
    solar_source: MockConfigEntry,
) -> None:
    set_house_states(hass)
    hass.states.async_set("sensor.solar", "3.0", {"unit_of_measurement": "kW"})
    source = solar_source

    class Manager:
        def __init__(self) -> None:
            self.data = {"energy_sources": [{"type": "grid"},
                                            {"type": "solar", "config_entry_solar_forecast": [source.entry_id]}]}

    async def forecast(hass: HomeAssistant, entry_id: str) -> Any:
        return None

    with patch("homeassistant.components.energy.data.async_get_manager", return_value=Manager()), patch(
        "homeassistant.components.energy.websocket_api.async_get_energy_platforms",
        return_value={"forecast_solar": forecast},
    ):
        entry = await setup_entry(hass, data={**HOUSE, "solar_power": "sensor.solar"})
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["forecast"]["solar_error"] == "the solar forecast gave no hours"


async def test_a_spoken_warning_before_an_empty_room_turns_off(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    set_house_states(hass)
    hass.states.async_set("binary_sensor.presence", "on")
    hass.states.async_set("tts.speaker", "unknown")
    speak = async_mock_service(hass, "tts", "speak")
    await setup_entry(hass, rooms=[stored_room(presence_sensor="binary_sensor.presence", announce=True,
                                               announce_through=["media_player.office"])])
    hass.states.async_set("binary_sensor.presence", "off")
    await _tick(hass, freezer, timedelta(minutes=11))
    assert [c.data for c in speak] == [{
        "entity_id": "tts.speaker", "media_player_entity_id": ["media_player.office"],
        "message": "Office is empty. The air conditioner turns off in 3 minutes.",
    }]  # [D069]


async def test_an_empty_room_with_no_speech_engine_turns_off_at_once(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    """Two speech engines and none chosen: no warning can be said, so the
    empty room does not wait for one [D069][D082]."""
    set_house_states(hass)
    hass.states.async_set("binary_sensor.presence", "on")
    hass.states.async_set("tts.one", "unknown")
    hass.states.async_set("tts.two", "unknown")
    speak = async_mock_service(hass, "tts", "speak")
    entry = await setup_entry(hass, rooms=[stored_room(presence_sensor="binary_sensor.presence", announce=True,
                                                       announce_through=["media_player.office"])])
    hass.states.async_set("binary_sensor.presence", "off")
    await _tick(hass, freezer, timedelta(minutes=11))
    from custom_components.abode_hvac_coordinator.models import StepKind

    run = entry.runtime_data.coordinator.rooms[ROOM_ID]
    assert run.step is not None and run.step.kind is StepKind.OFF
    assert not speak
    assert [c.data.get("hvac_mode") for c in climate_calls["set_hvac_mode"]][-1:] == ["off"]


async def test_free_cooling_is_not_advised_in_an_empty_room(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    """Outdoor air better, but nobody in: no advice and no hold, even with
    Precool cooling the empty room [D073]."""
    hass.states.async_set("climate.office", "off", {**CLIMATE_ATTRS, "current_temperature": 29.0})
    hass.states.async_set("sensor.office_t", "29.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.office_rh", "70", {"unit_of_measurement": "%", "device_class": "humidity"})
    hass.states.async_set("sensor.outdoor_t", "14.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.outdoor_rh", "40", {"unit_of_measurement": "%", "device_class": "humidity"})
    hass.states.async_set("binary_sensor.presence", "off")
    entry = await setup_entry(hass, rooms=[stored_room(presence_sensor="binary_sensor.presence")])
    for _ in range(25):  # past the empty grace
        await _tick(hass, freezer, timedelta(seconds=30))
    await hass.services.async_call("button", "press", {"entity_id": "button.office_precool"}, blocking=True)
    for _ in range(12):
        await _tick(hass, freezer, timedelta(seconds=30))
    run = entry.runtime_data.coordinator.rooms[ROOM_ID]
    assert run.comfort_result is not None and run.comfort_result.occupied is False
    assert run.comfort_result.precool_on
    assert run.control.plan is not None and run.control.plan.first.kind.value == "cool"
    assert run.free_cooling_since is None


async def test_a_failed_spoken_warning_is_logged_once(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture
) -> None:
    set_house_states(hass)
    hass.states.async_set("binary_sensor.presence", "on")
    hass.states.async_set("binary_sensor.window", "off")

    async def fail(call: ServiceCall) -> None:
        raise HomeAssistantError("the speaker is offline")

    hass.services.async_register("tts", "speak", fail)
    await setup_entry(hass, data={**HOUSE, "tts_engine": "tts.speaker"},
                      rooms=[stored_room(presence_sensor="binary_sensor.presence", openings=["binary_sensor.window"],
                                         announce=True, announce_through=["media_player.office"])])
    hass.states.async_set("binary_sensor.window", "on")
    for _ in range(12):
        await _tick(hass, freezer, timedelta(seconds=30))
    failures = [r for r in caplog.records if "could not be said" in r.getMessage()]
    assert len(failures) == 1 and failures[0].levelno == logging.WARNING  # [D069]


async def test_a_failing_room_is_logged_once_and_comes_back(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture
) -> None:
    set_house_states(hass)
    hass.states.async_set("climate.bedroom", "cool", hass.states.get("climate.office").attributes)  # type: ignore[union-attr]
    await setup_entry(hass, rooms=[stored_room(), stored_room("b" * 32, "Bedroom", "climate.bedroom")])
    from custom_components.abode_hvac_coordinator.observe import RoomObserver

    real = RoomObserver.build

    def build(self: RoomObserver, **kw: Any) -> Any:
        if self.room.name == "Office":
            raise RuntimeError("broken")
        return real(self, **kw)

    with patch.object(RoomObserver, "build", build):
        for _ in range(3):
            await _tick(hass, freezer, timedelta(seconds=30))
        office, bedroom = hass.states.get("sensor.office"), hass.states.get("sensor.bedroom")
        assert office is not None and office.state == "unavailable"
        assert bedroom is not None and bedroom.state == "occupied"  # the other room runs [R18]
    await _tick(hass, freezer, timedelta(seconds=30))
    office = hass.states.get("sensor.office")
    assert office is not None and office.state == "occupied"
    assert len([r for r in caplog.records if "the cycle failed" in r.getMessage()]) == 1
    assert len([r for r in caplog.records if "the cycle runs again" in r.getMessage()]) == 1


async def test_when_no_room_can_run_every_entity_is_unavailable(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    from custom_components.abode_hvac_coordinator.observe import RoomObserver

    with patch.object(RoomObserver, "build", side_effect=RuntimeError("broken")):
        await _tick(hass, freezer, timedelta(seconds=30))
        house = hass.states.get("sensor.abode_hvac_coordinator_rooms_configured")
        switch = hass.states.get("switch.office_automatic_control")
        assert house is not None and house.state == "unavailable"
        assert switch is not None and switch.state == "unavailable"
    await _tick(hass, freezer, timedelta(seconds=30))
    house = hass.states.get("sensor.abode_hvac_coordinator_rooms_configured")
    assert house is not None and house.state == "1"


async def test_an_air_conditioner_in_two_rooms_locks_both_out(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    set_house_states(hass)
    await setup_entry(hass, rooms=[stored_room(), stored_room("b" * 32, "Bedroom")])
    for e in ("sensor.office", "sensor.bedroom"):
        state = hass.states.get(e)
        assert state is not None and state.state == "lockout"  # [D021]
    issue = _issue(hass, "shared_climate_climate.office")
    assert issue is not None and issue.translation_placeholders == {"entity": "climate.office",
                                                                     "rooms": "Office, Bedroom"}


async def test_a_missing_sensor_and_a_silent_filter_raise_repairs(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    hass.states.async_remove("sensor.office_rh")
    await setup_entry(hass)
    assert _issue(hass, f"room_sensor_missing_{ROOM_ID}_sensor.office_rh") is None  # not yet a quarter hour
    await _tick(hass, freezer, timedelta(minutes=14, seconds=30))
    assert _issue(hass, f"room_sensor_missing_{ROOM_ID}_sensor.office_rh") is None
    await _tick(hass, freezer, timedelta(seconds=30))  # gone 15 minutes [D046][Set]
    issue = _issue(hass, f"room_sensor_missing_{ROOM_ID}_sensor.office_rh")
    assert issue is not None and issue.translation_placeholders == {"room": "Office", "entity": "sensor.office_rh"}
    index = hass.states.get("sensor.office_comfort_index")
    assert index is not None and index.state == "unavailable"  # its reading is missing [Prop]
    hass.states.async_set("sensor.office_t", "unavailable")
    await _tick(hass, freezer, timedelta(hours=24, seconds=30))
    assert _issue(hass, f"filter_silent_{ROOM_ID}") is not None
    set_house_states(hass)
    await _tick(hass, freezer, timedelta(seconds=30))
    assert _issue(hass, f"room_sensor_missing_{ROOM_ID}_sensor.office_rh") is None


async def test_a_sensor_no_integration_provides_counts_as_missing(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    # the stand-in the entity registry writes for an entity no integration provides [HA]
    hass.states.async_set("sensor.office_rh", "unavailable", {"restored": True})
    await setup_entry(hass)
    await _tick(hass, freezer, timedelta(minutes=15))
    assert _issue(hass, f"room_sensor_missing_{ROOM_ID}_sensor.office_rh") is not None  # [D046]
    hass.states.async_set("sensor.office_rh", "unavailable")  # provided, just not reading
    await _tick(hass, freezer, timedelta(seconds=30))
    assert _issue(hass, f"room_sensor_missing_{ROOM_ID}_sensor.office_rh") is None


async def test_a_sensor_only_away_raises_no_missing_sensor_repair(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """The registry's stand-in is also written while the sensor's own
    integration is retrying or reloading: away, not gone [D046][HA]."""
    set_house_states(hass)
    owner = MockConfigEntry(domain="sensor_maker", state=ConfigEntryState.SETUP_RETRY)
    owner.add_to_hass(hass)
    hass.states.async_remove("sensor.office_rh")
    reg = er.async_get(hass).async_get_or_create("sensor", "sensor_maker", "rh1", suggested_object_id="office_rh",
                                                 config_entry=owner)
    assert reg.entity_id == "sensor.office_rh"
    hass.states.async_set("sensor.office_rh", "unavailable", {"restored": True})
    await setup_entry(hass)
    issue_id = f"room_sensor_missing_{ROOM_ID}_sensor.office_rh"
    assert _issue(hass, issue_id) is None
    await _tick(hass, freezer, timedelta(minutes=30))
    assert _issue(hass, issue_id) is None  # still only retrying
    owner.mock_state(hass, ConfigEntryState.LOADED)  # its integration is up and does not provide it
    await _tick(hass, freezer, timedelta(seconds=30))
    assert _issue(hass, issue_id) is None  # gone from now: the quarter hour starts again
    await _tick(hass, freezer, timedelta(minutes=15))
    assert _issue(hass, issue_id) is not None


@pytest.mark.parametrize(("state", "disabled"), [(ConfigEntryState.SETUP_ERROR, None),
                                                (ConfigEntryState.NOT_LOADED, ConfigEntryDisabler.USER)])
async def test_a_sensor_whose_integration_failed_or_was_disabled_is_gone(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
    state: ConfigEntryState, disabled: Any,
) -> None:
    """Failed for good, or disabled (never set up again,
    config_entries.py:2689-2692): nothing is coming, so it is gone [D046]."""
    set_house_states(hass)
    owner = MockConfigEntry(domain="sensor_maker", state=state, disabled_by=disabled)
    owner.add_to_hass(hass)
    hass.states.async_remove("sensor.office_rh")
    er.async_get(hass).async_get_or_create("sensor", "sensor_maker", "rh1", suggested_object_id="office_rh",
                                           config_entry=owner)
    hass.states.async_set("sensor.office_rh", "unavailable", {"restored": True})
    await setup_entry(hass)
    await _tick(hass, freezer, timedelta(minutes=15))
    assert _issue(hass, f"room_sensor_missing_{ROOM_ID}_sensor.office_rh") is not None


async def test_no_missing_sensor_repair_before_home_assistant_has_started(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    hass.states.async_remove("sensor.office_rh")
    hass.set_state(CoreState.starting)  # its integration may not have loaded yet
    await setup_entry(hass)
    issue_id = f"room_sensor_missing_{ROOM_ID}_sensor.office_rh"
    assert _issue(hass, issue_id) is None
    hass.set_state(CoreState.running)
    await _tick(hass, freezer, timedelta(seconds=30))
    await _tick(hass, freezer, timedelta(minutes=15))
    assert _issue(hass, issue_id) is not None


async def test_speech_engine_repairs(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    await setup_entry(hass, rooms=[stored_room(announce=True, announce_through=["media_player.office"])])
    assert _issue(hass, "speech_engine") is not None  # announcing with no engine [D069]


async def test_grid_import_in_a_no_import_window_raises_a_repair(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    hass.states.async_set("sensor.grid", "2000", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.load", "2.5", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.solar", "0.0", {"unit_of_measurement": "kW"})
    now = dt_util.utcnow().replace(second=0, microsecond=0)
    Tariff(hass, {"intervals": [
        {"start_time": (now - timedelta(minutes=5)).isoformat(), "end_time": (now + timedelta(minutes=10)).isoformat(),
         "per_kwh": 0.3, "rate": "peak", "constraints": ["no_grid_import"],
         "enforceable_constraints": ["no_grid_import"]},
        {"start_time": (now + timedelta(minutes=10)).isoformat(), "end_time": (now + timedelta(hours=9)).isoformat(),
         "per_kwh": 0.2, "rate": "shoulder", "constraints": [], "enforceable_constraints": []},
    ]})
    await setup_entry(hass, data={**HOUSE, "tariff_entry": TARIFF, "grid_power": "sensor.grid",
                                  "grid_positive_is_import": True, "house_load": "sensor.load",
                                  "solar_power": "sensor.solar"})
    for _ in range(24):
        hass.states.async_set("sensor.grid", "2000", {"unit_of_measurement": "W"}, force_update=True)
        await _tick(hass, freezer, timedelta(seconds=30))
    issue = _issue(hass, "grid_import")
    assert issue is not None and float(issue.translation_placeholders["kwh"]) > 0.05  # [D043]


async def test_midnight_scores_the_day(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]],
                                       freezer: FrozenDateTimeFactory) -> None:
    set_house_states(hass)
    local = dt_util.as_local(dt_util.utcnow())
    freezer.move_to(local.replace(hour=23, minute=58, second=0, microsecond=0))
    await setup_entry(hass)
    for _ in range(6):
        await _tick(hass, freezer, timedelta(seconds=30))
    in_band = hass.states.get("sensor.office_comfort_in_band")
    assert in_band is not None and in_band.state != "unknown"  # yesterday's score [Prop]


async def test_rooms_on_one_outdoor_unit_are_each_charged_their_own_share(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """Two rooms running on one outdoor unit: each room's cost of comfort is
    its own draw (1.2 kW without a house-load sensor), not the unit's [D097]."""
    set_house_states(hass)
    set_house_states(hass, climate="climate.den")
    for c in ("climate.office", "climate.den"):
        hass.states.async_set(c, "cool", {**CLIMATE_ATTRS, "hvac_action": "cooling"})
    local = dt_util.as_local(dt_util.utcnow())
    freezer.move_to(local.replace(hour=23, minute=50, second=0, microsecond=0))
    Tariff(hass, _intervals())
    shed = [{"climate": "climate.office", "outdoor_unit": "Shed"}]
    await setup_entry(hass, data={**HOUSE, "tariff_entry": TARIFF}, rooms=[
        stored_room(heads=shed),
        stored_room(room_id="f" * 32, name="Den", climate="climate.den",
                    heads=[{"climate": "climate.den", "outdoor_unit": "Shed"}])])
    for _ in range(24):  # past midnight: yesterday's cost is scored
        await _tick(hass, freezer, timedelta(seconds=30))
    office, den = (hass.states.get(f"sensor.{n}_cost_of_comfort") for n in ("office", "den"))
    assert office is not None and den is not None
    # 10 minutes running at 1.2 kW and 0.30 a kWh is 0.06; the whole unit's 2.4 kW would be 0.12
    assert float(office.state) == pytest.approx(0.06, abs=0.012)
    assert float(den.state) == pytest.approx(0.06, abs=0.012)


async def test_a_new_room_is_seeded_from_history(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], caplog: pytest.LogCaptureFixture
) -> None:
    set_house_states(hass)
    await async_wait_recording_done(hass)
    caplog.set_level(logging.INFO)
    await setup_entry(hass)
    assert any("seeded from" in r.getMessage() for r in caplog.records)


async def test_seeding_a_room_that_has_learned_starts_from_the_beginning(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    """Old readings replayed onto a filter already at now would be learned
    twice and out of time order, so a seed starts from the starting
    figures; a replay that teaches nothing leaves the room as it was [Prop]."""
    set_house_states(hass)
    await async_wait_recording_done(hass)
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.coordinator
    run = coordinator.rooms[ROOM_ID]
    live = run.state.model
    given: list[Any] = []

    def replay(model: Any, *args: Any) -> Any:
        given.append(model)
        model.heat = live.heat  # the replay learned something
        return type("Result", (), {"days": 1.0, "corrections": 3})()

    with patch.object(coordinator, "_replay", replay):
        await coordinator.async_seed(ROOM_ID)
    assert given and given[0] is not live and given[0].unit.u_min is None  # a fresh model
    assert run.state.model is given[0]

    def teaches_nothing(model: Any, *args: Any) -> Any:
        return type("Result", (), {"days": 0.0, "corrections": 0})()

    with patch.object(coordinator, "_replay", teaches_nothing):
        await coordinator.async_seed(ROOM_ID)
    assert run.state.model is given[0]  # kept

    def filter_but_no_corrections(model: Any, *args: Any) -> Any:
        model.heat = live.heat  # a filter was started from a reading, and never corrected
        return type("Result", (), {"days": 1.0, "corrections": 0})()

    with patch.object(coordinator, "_replay", filter_but_no_corrections):
        await coordinator.async_seed(ROOM_ID)
    assert run.state.model is given[0]  # still kept


def test_the_power_budget_works_on_the_curve_of_the_way_the_room_runs() -> None:
    """A heating room's ceiling is found on the heating curve, with its
    floor, top and draw; a resting room is budgeted the way it must go
    [I10][D028]."""
    import math

    from custom_components.abode_hvac_coordinator.coordinator import (
        _budget_direction,
        _in_direction,
    )
    from custom_components.abode_hvac_coordinator.economics import PowerBudget
    from custom_components.abode_hvac_coordinator.model.filter import start_state
    from custom_components.abode_hvac_coordinator.model.physics import (
        I_COOL,
        I_HEAT,
        curve_values,
        figures,
    )
    from custom_components.abode_hvac_coordinator.model.unit import (
        UnitModel,
        effort_for_rate,
        top_rate,
    )
    from custom_components.abode_hvac_coordinator.models import (
        BandPoint,
        PlanAction,
        PowerManagement,
        RoomPower,
        StepKind,
    )

    z = start_state(22.0)
    for j in range(6):
        z[I_COOL + j] = math.log(0.5)  # cooling 0.5 .. 3.0 C/h
        z[I_HEAT + j] = math.log(1.0)  # heating 1.0 .. 6.0 C/h
    fig = figures(z)

    def at(rate: float) -> float:
        return 0.4 * effort_for_rate(fig, rate) + 0.2

    room = RoomPower("r", "u", PowerManagement.ENFORCED, False, True, top_rate(fig, -1),
                     UnitModel().lowest_rate(fig, -1))
    ceiling, _off, _a, _why = PowerBudget()._ceiling(room, 1.0, _in_direction(at, -1), dt_util.utcnow(), 5.0, None)
    assert ceiling is not None and abs(ceiling - curve_values(fig.heat_steps)[3]) < 1e-9  # 4 C/h of heating fits 1 kW

    class Plan:
        def __init__(self, kind: Any) -> None:
            self.first = PlanAction(kind, 0.0, None)

    class Run:
        def __init__(self, kind: Any, index: float | None) -> None:
            self.control = type("C", (), {"plan": Plan(kind) if kind is not None else None})()
            self.comfort_result = type("R", (), {"room_index": index,
                                                 "points": (BandPoint(dt_util.utcnow(), 22.0, 25.0, True),)})()

    assert _budget_direction(Run(StepKind.HEAT, 30.0)) == -1  # type: ignore[arg-type]
    assert _budget_direction(Run(StepKind.DRY, 10.0)) == 1  # type: ignore[arg-type]
    assert _budget_direction(Run(StepKind.REST, 19.0)) == -1  # type: ignore[arg-type]
    assert _budget_direction(Run(StepKind.REST, 27.0)) == 1  # type: ignore[arg-type]
    assert _budget_direction(Run(None, None)) == 1  # type: ignore[arg-type]


async def test_an_enforced_ceiling_reaches_the_pid_and_the_plan_in_the_cycle(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """Through the cycle: a ceiling appearing has the room planned again at
    once, and the PID is never given a top above it [D041][D062]."""
    from custom_components.abode_hvac_coordinator.models import RoomEconomics

    set_house_states(hass)
    entry = await setup_entry(hass)
    run = entry.runtime_data.coordinator.rooms[ROOM_ID]
    await _tick(hass, freezer, timedelta(minutes=5, seconds=30))  # a plan in force
    tops: list[float] = []
    plans: list[Any] = []
    kind = type(run.control)
    cycle, replan = kind.cycle, kind.replan

    def spy_cycle(self: Any, **kw: Any) -> Any:
        tops.append(kw["top"])
        return cycle(self, **kw)

    def spy_replan(self: Any, inputs: Any, g: Any) -> Any:
        plans.append((inputs.ceiling, inputs.ceiling_heat))
        return replan(self, inputs, g)

    econ = RoomEconomics((1.0,) * 12, 1.0, 0.7, None, False, 0.5, "ceiling 0.70 C/h")
    with patch.object(kind, "cycle", spy_cycle), patch.object(kind, "replan", spy_replan), \
            patch("custom_components.abode_hvac_coordinator.coordinator.room_economics", return_value=econ):
        await _tick(hass, freezer, timedelta(seconds=30))  # not a 5-minute boundary
    assert [c for c, _ in plans] == [0.7]  # planned again at once under the new ceiling
    assert plans[0][1] is not None  # and as a heating rate for the same power [D028]
    assert tops and max(tops) <= 0.7


async def test_a_heating_pid_is_held_to_the_heating_ceiling_in_the_cycle(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """Heating, the PID is capped by the heating ceiling, not the cooling
    one [D028][D041]."""
    from custom_components.abode_hvac_coordinator import coordinator as coord

    set_house_states(hass, mode="heat")
    hass.states.async_set("sensor.office_t", "15.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.outdoor_t", "6.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    entry = await setup_entry(hass)
    run = entry.runtime_data.coordinator.rooms[ROOM_ID]
    seen: list[tuple[int, int]] = []
    real = coord._pid_top

    def spy(top: float, ceilings: Any, direction: int) -> float:
        assert run.control.plan is not None
        seen.append((direction, 1 if run.control.plan.first.rate >= 0 else -1))
        return real(top, ceilings, direction)

    with patch.object(coord, "_pid_top", spy):
        await _tick(hass, freezer, timedelta(minutes=5, seconds=30))
        await _tick(hass, freezer, timedelta(seconds=30))
    assert seen and all(d == want for d, want in seen)
    assert (-1, -1) in seen  # a heating step was capped as heating


def test_the_ceiling_is_the_same_power_as_a_rate_either_way() -> None:
    """Found on one curve, the ceiling is turned into the other direction's
    rate for the same power, so a plan turning the other way is held to the
    same allowance [D028][D041]."""
    import math

    from custom_components.abode_hvac_coordinator.coordinator import (
        _ceilings,
        _in_direction,
    )
    from custom_components.abode_hvac_coordinator.economics import highest_rate
    from custom_components.abode_hvac_coordinator.model.filter import start_state
    from custom_components.abode_hvac_coordinator.model.physics import (
        I_COOL,
        I_HEAT,
        figures,
    )
    from custom_components.abode_hvac_coordinator.model.unit import (
        effort_for_rate,
        top_rate,
    )

    z = start_state(22.0)
    for j in range(6):
        z[I_COOL + j] = math.log(0.5)  # cooling 0.5 .. 3.0 C/h
        z[I_HEAT + j] = math.log(1.0)  # heating 1.0 .. 6.0 C/h
    fig = figures(z)

    def draw(rate: float) -> float:
        return 0.4 * effort_for_rate(fig, rate) + 0.2

    cool, heat = _ceilings(1, 1.5, draw, fig)  # found cooling: 1.5 C/h cooling is effort 1.5
    assert cool == 1.5
    assert heat == highest_rate(_in_direction(draw, -1), draw(1.5), top_rate(fig, -1))
    assert heat is not None and abs(draw(-heat) - draw(1.5)) < 0.05  # the same power, as heating
    cool2, heat2 = _ceilings(-1, heat, draw, fig)  # and back
    assert heat2 == heat and cool2 is not None and abs(cool2 - 1.5) <= 0.05
    assert _ceilings(1, None, draw, fig) == (None, None)
    assert _ceilings(1, 0.0, draw, fig) == (0.0, 0.0)
    assert _ceilings(-1, 2.0, lambda r: None, fig) == (2.0, 2.0)  # draw not learned: the same figure


def test_an_enforced_ceiling_holds_the_pid_and_is_planned_under_at_once() -> None:
    """The ceiling limits the PID as it limits the plan, and a new one is
    planned under at once [D041][D062]."""
    from custom_components.abode_hvac_coordinator.coordinator import (
        _note_ceiling,
        _pid_top,
    )

    assert _pid_top(6.0, (None, None), 1) == 6.0
    assert _pid_top(6.0, (1.0, 2.0), 1) == 1.0
    assert _pid_top(0.8, (1.0, 2.0), 1) == 0.8
    assert _pid_top(6.0, (1.0, 2.0), -1) == 2.0  # heating: the heating ceiling
    assert _pid_top(6.0, (1.0, None), -1) == 6.0
    run = type("Run", (), {"ceiling": None, "replan": False})()
    _note_ceiling(run, None)  # type: ignore[arg-type]
    assert run.replan is False
    _note_ceiling(run, 1.5)  # type: ignore[arg-type]
    assert (run.ceiling, run.replan) == (1.5, True)
    run.replan = False
    _note_ceiling(run, 1.5)  # type: ignore[arg-type]
    assert run.replan is False
    _note_ceiling(run, 1.0)  # type: ignore[arg-type]
    assert run.replan is True


def test_a_rooms_power_figures_follow_the_way_it_runs() -> None:
    """Top rate, lowest rate and draw, all on the heating curve for a room
    that heats [I10][D028]."""
    import math

    from custom_components.abode_hvac_coordinator.coordinator import _room_power
    from custom_components.abode_hvac_coordinator.model import RoomModel
    from custom_components.abode_hvac_coordinator.model.filter import HeatFilter
    from custom_components.abode_hvac_coordinator.model.physics import (
        I_COOL,
        I_HEAT,
        figures,
    )
    from custom_components.abode_hvac_coordinator.model.unit import top_rate
    from custom_components.abode_hvac_coordinator.models import (
        BandPoint,
        PlanAction,
        StepKind,
    )

    model = RoomModel()
    model.heat = HeatFilter.start(22.0)
    for j in range(6):
        model.heat.z[I_COOL + j] = math.log(0.5)
        model.heat.z[I_HEAT + j] = math.log(1.0)
    fig = figures(model.heat.z)
    asked: list[float] = []
    run = type("Run", (), {
        "model": model, "outdoor_units": ("u",),
        "config": type("Cfg", (), {"room_id": "r", "power_management": "enforced"})(),
        "snapshot": type("Snap", (), {"heads": ()})(),
        "comfort_result": type("CR", (), {"precool_on": False, "room_index": 18.0,
                                          "points": (BandPoint(dt_util.utcnow(), 22.0, 25.0, True),)})(),
        "control": type("Ctl", (), {"plan": type("P", (), {"first": PlanAction(StepKind.HEAT, -2.0, None)})()})(),
    })()
    power, draw = _room_power(run, lambda r: asked.append(r) or 1.0)  # type: ignore[arg-type,func-returns-value]
    assert power.top_rate == top_rate(fig, -1) and power.top_rate != top_rate(fig, 1)
    assert power.lowest_rate == model.unit.lowest_rate(fig, -1)
    draw(3.0)
    assert asked == [-3.0]  # asked on the heating curve


async def test_a_climate_entitys_own_limits_are_read_from_the_entity(hass: HomeAssistant) -> None:
    """Its own min_temp and max_temp, in its own unit, what Home Assistant
    checks a setpoint against [HA]."""
    from custom_components.abode_hvac_coordinator.coordinator import (
        CLIMATE_COMPONENT,
        native_limits,
    )

    class Unit:
        min_temp = 18.0
        max_temp = 31.0

    class Odd:
        min_temp = None
        max_temp = 31.0

    class Component:
        def get_entity(self, entity_id: str) -> Any:
            return {"climate.a": Unit(), "climate.odd": Odd()}.get(entity_id)

    assert native_limits(hass, "climate.a") is None  # climate not set up
    hass.data[CLIMATE_COMPONENT] = Component()
    try:
        assert native_limits(hass, "climate.a") == (18.0, 31.0)
        assert native_limits(hass, "climate.odd") is None
        assert native_limits(hass, "climate.gone") is None
    finally:
        del hass.data[CLIMATE_COMPONENT]


async def test_both_profile_readers_use_the_entitys_own_limits(hass: HomeAssistant) -> None:
    """The coordinator and the setup screens read a room's profile with the
    unit's own limits, not the shown ones [HA][D051]."""
    from custom_components.abode_hvac_coordinator.config_flow import AbodeHvacConfigFlow
    from custom_components.abode_hvac_coordinator.coordinator import (
        CLIMATE_COMPONENT,
        AbodeHvacCoordinator,
    )

    class Unit:
        native_temperature_unit = "°C"
        min_temp = 17.5
        max_temp = 30.5

    class Component:
        def get_entity(self, entity_id: str) -> Any:
            return Unit() if entity_id == "climate.a" else None

    hass.states.async_set("climate.a", "cool", {
        "hvac_modes": ["off", "heat", "cool"], "min_temp": 18.0, "max_temp": 30.0,
        "target_temp_step": 0.5, "supported_features": 1, "current_temperature": 24.0,
        "temperature": 24.0})
    hass.data[CLIMATE_COMPONENT] = Component()
    try:
        fake = SimpleNamespace(hass=hass)
        room = SimpleNamespace(heads=[SimpleNamespace(climate="climate.a")])
        p = AbodeHvacCoordinator._read_profile(fake, room)  # type: ignore[arg-type]
        assert p is not None and (p.min_setpoint, p.max_setpoint) == (17.5, 30.5)
        p2, error, _ = AbodeHvacConfigFlow._read_profile(fake, ["climate.a"])  # type: ignore[arg-type]
        assert error is None and p2 is not None and (p2.min_setpoint, p2.max_setpoint) == (17.5, 30.5)
    finally:
        del hass.data[CLIMATE_COMPONENT]


async def test_a_climate_entitys_own_unit_is_read_from_the_entity(hass: HomeAssistant) -> None:
    """Its state shows only the system's unit, so the step's unit comes from
    the entity: native_temperature_unit from 2026.11, temperature_unit
    before [HA]."""
    from custom_components.abode_hvac_coordinator.coordinator import (
        CLIMATE_COMPONENT,
        native_temperature_unit,
    )

    class Native:
        native_temperature_unit = "°F"

    class Older:
        temperature_unit = "°F"

    class Component:
        def get_entity(self, entity_id: str) -> Any:
            return {"climate.new": Native(), "climate.old": Older()}.get(entity_id)

    assert native_temperature_unit(hass, "climate.new") is None  # climate not set up
    hass.data[CLIMATE_COMPONENT] = Component()
    try:
        assert native_temperature_unit(hass, "climate.new") == "°F"
        assert native_temperature_unit(hass, "climate.old") == "°F"
        assert native_temperature_unit(hass, "climate.gone") is None
    finally:
        del hass.data[CLIMATE_COMPONENT]


async def test_diagnostics_never_hold_the_homes_position(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], hass_client: ClientSessionGenerator
) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass, data={**HOUSE, "latitude": -33.9, "location": "home"})
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["house"]["latitude"] == diag["house"]["location"] == "**REDACTED**"  # [R18]
    room = diag["rooms"][ROOM_ID]
    assert room["settings"]["presence_sensor"] == "unset"  # [D048]
    assert room["profile"]["setpoint_step"] == 0.5
    assert room["learned"]["heat"] is not None and room["trace"]["action"] == "cool"
    assert diag["commands"][0]["entity_id"] == "climate.office"  # the last 200 [D052]
    assert "climate.office" in diag["last_command"]  # what Act sent is handed to Model [D081]


async def test_precool_ends_when_someone_comes_in(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    hass.states.async_set("binary_sensor.presence", "off")
    await setup_entry(hass, rooms=[stored_room(presence_sensor="binary_sensor.presence")])
    await _tick(hass, freezer, timedelta(minutes=11))
    await hass.services.async_call(DOMAIN, "precool", {"room_id": ROOM_ID}, blocking=True)
    state = hass.states.get("sensor.office")
    assert state is not None and state.state == "precool"
    hass.states.async_set("binary_sensor.presence", "on")
    await _tick(hass, freezer, timedelta(minutes=3))
    await _tick(hass, freezer, timedelta(seconds=30))
    state = hass.states.get("sensor.office")
    assert state is not None and state.state == "occupied"  # Precool ended by someone coming in [I8]
    hass.states.async_set("binary_sensor.presence", "off")
    await _tick(hass, freezer, timedelta(minutes=11))
    state = hass.states.get("sensor.office")
    assert state is not None and state.state == "unoccupied"  # the ended Precool does not come back [I8]


@pytest.mark.parametrize("target", ["delivery.RoomDelivery.deliver", "act.RoomAct.calls"])
async def test_a_room_failing_later_in_the_cycle(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory, target: str
) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    with patch(f"custom_components.abode_hvac_coordinator.{target}", side_effect=RuntimeError("broken")):
        await _tick(hass, freezer, timedelta(seconds=30))
        state = hass.states.get("sensor.office")
        assert state is not None and state.state == "unavailable"


async def test_a_running_compressor_and_the_house_load(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    set_house_states(hass)
    office = hass.states.get("climate.office")
    assert office is not None
    hass.states.async_set("climate.office", "cool", {**office.attributes, "hvac_action": "cooling"})
    hass.states.async_set("sensor.load", "2.0", {"unit_of_measurement": "kW"})
    await setup_entry(hass, data={**HOUSE, "house_load": "sensor.load"})
    mode = hass.states.get("sensor.office")
    assert mode is not None
    assert mode.attributes["compressors"] == {"climate.office": {"state": "running", "source": "hvac_action"}}
    hass.states.async_set("climate.office", "cool", {**office.attributes, "hvac_action": "idle"})
    await _tick(hass, freezer, timedelta(seconds=30))
    mode = hass.states.get("sensor.office")
    assert mode is not None
    assert mode.attributes["compressors"] == {"climate.office": {"state": "stopped", "source": "hvac_action"}}


async def test_the_profile_is_read_at_start_up_when_none_is_stored(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    set_house_states(hass)
    room = stored_room()
    del room["profile"]
    await setup_entry(hass, rooms=[room])
    assert hass.states.get("select.office_unit_mode") is not None  # [D048]


async def test_speech_works_again_after_a_failure(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture
) -> None:
    set_house_states(hass)
    hass.states.async_set("binary_sensor.presence", "on")
    hass.states.async_set("binary_sensor.window", "off")
    fail = [True]

    async def speak(call: ServiceCall) -> None:
        if fail[0]:
            raise HomeAssistantError("offline")

    hass.services.async_register("tts", "speak", speak)
    caplog.set_level(logging.INFO)
    await setup_entry(hass, data={**HOUSE, "tts_engine": "tts.speaker"},
                      rooms=[stored_room(openings=["binary_sensor.window"], announce=True,
                                         announce_through=["media_player.office"])])
    hass.states.async_set("binary_sensor.window", "on")
    await _tick(hass, freezer, timedelta(minutes=2, seconds=1))
    fail[0] = False
    for _ in range(5):
        await _tick(hass, freezer, timedelta(seconds=30))
    assert any("work again" in r.getMessage() for r in caplog.records)


async def test_seeding_when_the_history_cannot_be_read(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], caplog: pytest.LogCaptureFixture
) -> None:
    set_house_states(hass)
    with patch("custom_components.abode_hvac_coordinator.coordinator.async_read_history",
               side_effect=HomeAssistantError("the recorder is busy")):
        await setup_entry(hass)
    assert any("could not be read: the recorder is busy" in r.getMessage() for r in caplog.records)


async def test_reset_with_no_history_starts_afresh(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], hass_client: ClientSessionGenerator
) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    with patch("custom_components.abode_hvac_coordinator.coordinator.async_read_history", return_value={}):
        for step in ("reset", "seed"):  # a seed with nothing to replay leaves the model as it stands
            r = await hass.config_entries.options.async_init(entry.entry_id)
            r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "learning"})
            r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": step})
            r = await hass.config_entries.options.async_configure(r["flow_id"], {"room_id": ROOM_ID})
            r = await hass.config_entries.options.async_configure(r["flow_id"], {})
            await hass.async_block_till_done(wait_background_tasks=True)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["rooms"][ROOM_ID]["learned"]["heat"] is None  # returned to its start


async def test_editing_a_rooms_settings_keeps_its_learning(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], hass_client: ClientSessionGenerator
) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    room = stored_room(occupied_low=22.0)
    del room["profile"]
    hass.config_entries.async_update_entry(entry, options={"rooms": [room]})
    await hass.async_block_till_done(wait_background_tasks=True)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    office = diag["rooms"][ROOM_ID]
    assert office["settings"]["occupied_band"] == [22.0, 26.0]
    assert office["learned"]["heat"] is not None  # kept [D031]
    assert office["profile"] is not None  # read again from the live unit [D048]


async def test_editing_a_room_keeps_what_its_modules_remember(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """An edit keeps what each module remembers between cycles: the running
    command the short-cycle guard holds a stop on, today's scorecard, the
    plan and the band's ramp. A reading the room no longer has is
    forgotten [D016][D098]. Read from the coordinator, because what the
    guard holds only shows once a stop is wanted inside 10 minutes."""
    set_house_states(hass, mode="off")
    hass.states.async_set("sensor.office_t", "27.5", {"unit_of_measurement": "°C", "device_class": "temperature"})
    entry = await setup_entry(hass)
    hass.states.async_set("climate.office", "cool", {**CLIMATE_ATTRS, "temperature": 21.0, "hvac_action": "cooling"})
    await _tick(hass, freezer, timedelta(seconds=30))
    run = entry.runtime_data.coordinator.rooms[ROOM_ID]
    limits, scorecard, comfort, control, act = run.limits, run.scorecard, run.comfort, run.control, run.act
    running = limits.last_running
    assert running and control.plan is not None
    run.availability.update({"sensor.gone": False})  # a reading lost, then taken out of the room
    hass.config_entries.async_update_entry(entry, options={"rooms": [stored_room(allow_blind_control=False)]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert run.config.allow_blind_control is False
    assert (run.limits is limits, run.scorecard is scorecard, run.comfort is comfort, run.control is control,
            run.act is act) == (True, True, True, True, True)
    assert run.limits.room is run.config and run.act.room is run.config
    assert run.limits.last_running == running  # what the guard keeps the room on
    assert run.availability.lost == frozenset()  # forgotten: the room no longer has it


async def test_the_models_heavy_steps_run_on_a_copy(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """The 5-minute and hourly model steps run in the executor on a copy,
    which replaces the room's model when done: a save or a diagnostics
    download in the meantime never reads a model being changed [HA]."""
    from custom_components.abode_hvac_coordinator.model import RoomModel

    set_house_states(hass)
    entry = await setup_entry(hass)
    run = entry.runtime_data.coordinator.rooms[ROOM_ID]
    seen: list[tuple[str, bool]] = []
    five, hour = RoomModel.five_minutes, RoomModel.hour

    def spy_five(self: RoomModel, *args: Any) -> Any:
        seen.append(("five", self is run.state.model))
        return five(self, *args)

    def spy_hour(self: RoomModel, *args: Any) -> Any:
        seen.append(("hour", self is run.state.model))
        return hour(self, *args)

    with patch.object(RoomModel, "five_minutes", spy_five), patch.object(RoomModel, "hour", spy_hour):
        for _ in range(130):  # past the next 5 minutes and the next hour
            await _tick(hass, freezer, timedelta(seconds=30))
    assert {k for k, _ in seen} == {"five", "hour"}
    assert not any(live for _, live in seen)  # never the model the event loop reads
    assert run.state.model.heat is not None  # the copy took its place


def test_an_outdoor_units_draw_is_the_sum_of_its_rooms() -> None:
    """Observe is given each outdoor unit's draw: the sum over the rooms
    running on it, unknown if any of theirs is [D028][D097]."""
    from custom_components.abode_hvac_coordinator.coordinator import _by_unit

    got = _by_unit({("ou1", "a"): 0.5, ("ou1", "b"): 0.7, ("ou2", "c"): 0.4, ("ou3", "d"): 0.3, ("ou3", "e"): None,
                    ("ou4", "f"): None, ("ou4", "g"): 0.2})
    assert got == {"ou1": pytest.approx(1.2), "ou2": 0.4, "ou3": None, "ou4": None}


async def test_solar_sources_that_give_nothing_usable(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], hass_client: ClientSessionGenerator
) -> None:
    set_house_states(hass)
    hass.states.async_set("sensor.solar", "3.0", {"unit_of_measurement": "kW"})
    other = MockConfigEntry(domain="met", title="Weather")
    other.add_to_hass(hass)

    async def forecast(hass: HomeAssistant, entry_id: str) -> Any:
        return {"wh_hours": {"not a time": 100, dt_util.utcnow().isoformat(): float("nan")}}

    with patch("homeassistant.components.energy.websocket_api.async_get_energy_platforms",
               return_value={"forecast_solar": forecast}):
        entry = await setup_entry(hass, data={**HOUSE, "solar_power": "sensor.solar",
                                              "solar_forecast_sources": ["missing", other.entry_id]})
        diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
        assert diag["forecast"]["solar_error"] == "the solar forecast gave no hours"


async def test_the_trace_stays_out_of_the_recorder(hass: HomeAssistant,
                                                   climate_calls: dict[str, list[ServiceCall]]) -> None:
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import get_last_state_changes

    set_house_states(hass)
    await setup_entry(hass)
    await async_wait_recording_done(hass)
    found = await get_instance(hass).async_add_executor_job(get_last_state_changes, hass, 1, "sensor.office")
    attrs = found["sensor.office"][-1].attributes
    assert "action" in attrs and not {"plan", "model", "limits", "delivery"} & set(attrs)  # [HA]


async def test_the_plan_is_kept_out_of_the_recorder(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    """The Controller sensor carries the plan, kept out of the recorder like
    the Mode sensor's (docs/entities.md) [HA]."""
    from homeassistant.components.recorder.history import get_significant_states
    from homeassistant.helpers.recorder import get_instance

    start = dt_util.utcnow() - timedelta(minutes=1)
    set_house_states(hass)
    await setup_entry(hass)
    live = hass.states.get("sensor.office_controller")
    assert live is not None and live.attributes.get("plan") is not None  # shown live
    await async_wait_recording_done(hass)
    found = await get_instance(hass).async_add_executor_job(
        lambda: get_significant_states(hass, start, None, ["sensor.office_controller", "sensor.office"],
                                       significant_changes_only=False, minimal_response=False))
    assert found["sensor.office_controller"] and found["sensor.office"]
    for entity_id in ("sensor.office_controller", "sensor.office"):
        assert not [s for s in found[entity_id] if "plan" in s.attributes]  # type: ignore[union-attr]


async def test_the_hourly_trust_check_never_scores_a_stood_in_reading(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """With the room sensor gone the unit's own reading stands in; it is
    never scored by the hourly trust check [D074]."""
    from custom_components.abode_hvac_coordinator.model import RoomModel

    set_house_states(hass)  # the room sensor reads 25.0 C
    hass.states.async_set("climate.office", "cool", {**CLIMATE_ATTRS, "current_temperature": 21.0})
    freezer.move_to(dt_util.utcnow().replace(minute=50, second=0, microsecond=0))
    await setup_entry(hass)
    seen: list[tuple[float | None, float]] = []
    real = RoomModel.hour

    def spy(self: Any, now: Any, room_now: float | None, rest: Any, e_now: float) -> Any:
        seen.append((room_now, e_now))
        return real(self, now, room_now, rest, e_now)

    with patch.object(RoomModel, "hour", spy):
        for _ in range(3):
            await _tick(hass, freezer, timedelta(minutes=1))
        hass.states.async_set("sensor.office_t", "unavailable")
        for _ in range(12):  # across the top of the hour
            await _tick(hass, freezer, timedelta(minutes=1))
    assert seen and all(room_now is None for room_now, _ in seen)


async def test_the_hourly_trust_check_never_uses_a_stood_in_humidity(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """With the room's humidity sensor gone the unit's own humidity stands
    in; the moisture figure for the trust check never uses it [D074]."""
    from custom_components.abode_hvac_coordinator.hci import vapour_pressure
    from custom_components.abode_hvac_coordinator.model import RoomModel

    set_house_states(hass)  # the room reads 25.0 C, 55 %
    hass.states.async_set("climate.office", "cool", {**CLIMATE_ATTRS, "current_temperature": 25.0,
                                                     "current_humidity": 95.0})
    freezer.move_to(dt_util.utcnow().replace(minute=50, second=0, microsecond=0))
    await setup_entry(hass)
    seen: list[tuple[float | None, float]] = []
    real = RoomModel.hour

    def spy(self: Any, now: Any, room_now: float | None, rest: Any, e_now: float) -> Any:
        seen.append((room_now, e_now))
        return real(self, now, room_now, rest, e_now)

    with patch.object(RoomModel, "hour", spy):
        for _ in range(3):
            await _tick(hass, freezer, timedelta(minutes=1))
        hass.states.async_set("sensor.office_rh", "unavailable")
        for _ in range(12):
            await _tick(hass, freezer, timedelta(minutes=1))
    stood_in = vapour_pressure(25.0, 95.0)
    assert seen and all(abs(e - stood_in) > 1e-6 for _, e in seen)


async def test_a_lost_room_sensor_is_logged_while_the_unit_stands_in(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture
) -> None:
    """The unit's own reading standing in does not hide that the room
    sensor is lost: it is logged once [D074][HA]."""
    set_house_states(hass)
    await setup_entry(hass)
    await _tick(hass, freezer, timedelta(seconds=30))
    caplog.clear()
    hass.states.async_set("climate.office", "cool", {**CLIMATE_ATTRS, "current_humidity": 60.0})
    hass.states.async_set("sensor.office_t", "unavailable")
    hass.states.async_set("sensor.office_rh", "unavailable")
    await _tick(hass, freezer, timedelta(seconds=30))
    assert hass.states.get("sensor.office") is not None
    for entity_id in ("sensor.office_t", "sensor.office_rh"):
        assert [r for r in caplog.records if entity_id in r.getMessage() and r.levelno >= logging.INFO], entity_id


async def test_free_cooling_holds_a_start_for_ten_minutes(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """The plan is to cool and outdoor air would do better: the start is
    held for 10 minutes from the advice, which shows meanwhile [D073]."""
    hass.states.async_set("climate.office", "off", {**CLIMATE_ATTRS, "current_temperature": 29.0})
    hass.states.async_set("sensor.office_t", "29.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.office_rh", "70", {"unit_of_measurement": "%", "device_class": "humidity"})
    hass.states.async_set("sensor.outdoor_t", "14.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.outdoor_rh", "40", {"unit_of_measurement": "%", "device_class": "humidity"})
    entry = await setup_entry(hass)
    run = entry.runtime_data.coordinator.rooms[ROOM_ID]
    advised_at = run.free_cooling_since
    assert advised_at is not None  # judged before the first step went out
    assert not [c for c in climate_calls["set_hvac_mode"] if c.data.get("hvac_mode") == "cool"]
    assert run.delivery_result is not None and run.delivery_result.free_cooling_advised  # shown while held
    started = None
    for _ in range(40):  # 20 minutes
        await _tick(hass, freezer, timedelta(seconds=30))
        if started is None and any(c.data.get("hvac_mode") == "cool" for c in climate_calls["set_hvac_mode"]):
            started = dt_util.utcnow()
    assert run.free_cooling_since == advised_at  # the advice stood throughout
    assert started is not None and (started - advised_at).total_seconds() >= 600


async def test_free_cooling_advice_is_forgotten_when_outdoor_air_is_no_better(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """The advice, and the hold with it, ends as soon as outdoor air no
    longer does better [D073]."""
    hass.states.async_set("climate.office", "off", {**CLIMATE_ATTRS, "current_temperature": 29.0})
    hass.states.async_set("sensor.office_t", "29.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.office_rh", "70", {"unit_of_measurement": "%", "device_class": "humidity"})
    hass.states.async_set("sensor.outdoor_t", "14.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.outdoor_rh", "40", {"unit_of_measurement": "%", "device_class": "humidity"})
    entry = await setup_entry(hass)
    run = entry.runtime_data.coordinator.rooms[ROOM_ID]
    assert run.free_cooling_since is not None
    hass.states.async_set("sensor.outdoor_t", "32.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await _tick(hass, freezer, timedelta(seconds=30))
    assert run.free_cooling_since is None


async def test_a_unit_seen_running_is_given_to_the_guard_as_it_runs(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """A start made by hand while Automatic control is off: the guard is
    given the heads as they run, the command a held stop keeps [D016][D089]."""
    from custom_components.abode_hvac_coordinator.limits import HouseGuard
    from custom_components.abode_hvac_coordinator.models import HeadCommand

    set_house_states(hass)
    entry = await setup_entry(hass)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.office_automatic_control"},
                                   blocking=True)
    hass.states.async_set("climate.office", "off", CLIMATE_ATTRS)
    await _tick(hass, freezer, timedelta(seconds=30))
    hass.states.async_set("climate.office", "cool", {**CLIMATE_ATTRS, "hvac_action": "cooling"})  # by hand
    given: list[Any] = []
    real = HouseGuard.apply

    def spy(self: Any, now: Any, entries: Any) -> Any:
        given.extend(entries)
        return real(self, now, entries)

    with patch.object(HouseGuard, "apply", spy):
        await _tick(hass, freezer, timedelta(seconds=30))
    assert given and given[-1].observed_heads == (HeadCommand("climate.office", "cool", None, None, None, None,
                                                              None, None),)
    assert entry.runtime_data.coordinator.rooms[ROOM_ID].limits.last_running == given[-1].observed_heads


async def test_a_day_run_on_a_draw_not_yet_learned_is_not_priced(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """With a house-load sensor the draw is learned; until it is, the day's
    cost is unknown, never 0.00 [D097][D028]."""
    set_house_states(hass)
    hass.states.async_set("climate.office", "cool", {**CLIMATE_ATTRS, "hvac_action": "cooling"})
    hass.states.async_set("sensor.load", "1.5", {"unit_of_measurement": "kW", "device_class": "power"})
    local = dt_util.as_local(dt_util.utcnow())
    freezer.move_to(local.replace(hour=23, minute=50, second=0, microsecond=0))
    Tariff(hass, _intervals())
    await setup_entry(hass, data={**HOUSE, "tariff_entry": TARIFF, "house_load": "sensor.load"})
    for _ in range(24):  # past midnight: yesterday is scored
        await _tick(hass, freezer, timedelta(seconds=30))
    cost = hass.states.get("sensor.office_cost_of_comfort")
    assert cost is not None and cost.state == "unknown"
