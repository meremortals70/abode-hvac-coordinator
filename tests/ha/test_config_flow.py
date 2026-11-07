"""Every flow in config_flow.py: first setup, Reconfigure and the options
(Section 13.2). Each error a screen can show is tested, then its cause is
corrected and the flow finished."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import data_entry_flow
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .conftest import (
    CLIMATE_ATTRS,
    DOMAIN,
    ROOM_ID,
    set_house_states,
    setup_entry,
    stored_room,
)
from .harness import (
    MockConfigEntry,
)

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")

ROOM_1 = {
    "name": "Office",
    "climate": ["climate.office"],
    "temperature_sensor": "sensor.office_t",
    "humidity_sensor": "sensor.office_rh",
}


def suggested(result: Any, key: str) -> Any:
    """The value a form pre-fills for a field."""
    for k in result["data_schema"].schema:
        if str(k) == key:
            return (k.description or {}).get("suggested_value")
    raise KeyError(key)


async def _house(hass: HomeAssistant, flow_id: str, power: dict[str, Any] | None = None) -> Any:
    """Tariff, Outdoor, Forecast, Power (and Grid direction), Announcements."""
    r = await hass.config_entries.flow.async_configure(flow_id, {})
    assert r["step_id"] == "outdoor"
    r = await hass.config_entries.flow.async_configure(
        flow_id, {"outdoor_temperature": "sensor.outdoor_t", "outdoor_humidity": "sensor.outdoor_rh"})
    assert r["step_id"] == "forecast"
    r = await hass.config_entries.flow.async_configure(flow_id, {})
    assert r["step_id"] == "power"
    r = await hass.config_entries.flow.async_configure(flow_id, power or {})
    return r


async def test_first_setup_with_every_room_error(hass: HomeAssistant,
                                                 climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    hass.states.async_set("climate.office", "unavailable")
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "tariff"
    r = await _house(hass, r["flow_id"], {"grid_power": "sensor.grid", "battery_charge": "sensor.battery",
                                          "battery_size": 10.0})
    assert r["step_id"] == "grid_direction"  # a new grid sensor: its direction is asked [D038]
    assert suggested(r, "grid_positive_is_import") is None  # no evidence at first setup
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"grid_positive_is_import": "export"})
    assert r["step_id"] == "announcements"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["step_id"] == "room_1"
    # an air conditioner not reporting [D048]
    r = await hass.config_entries.flow.async_configure(r["flow_id"], ROOM_1)
    assert r["errors"] == {"climate": "not_reporting"}
    assert r["description_placeholders"] == {"entity": "climate.office"}
    # a name that is only spaces
    hass.states.async_set("climate.office", "cool", CLIMATE_ATTRS)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**ROOM_1, "name": "  "})
    assert r["errors"] == {"name": "no_name"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**ROOM_1, "lockout_reason": "Not locked out",
                                                                      "window_facing": "nw"})
    assert r["step_id"] == "room_2"
    assert r["description_placeholders"] == {"climate": "climate.office", "count": "1 of 1"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"outdoor_unit": "Shed"})
    assert r["step_id"] == "room_3"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"sleep_low": 24.0, "sleep_high": 23.0})
    assert r["errors"] == {"base": "band_inverted"}
    assert r["description_placeholders"] == {"band": "sleep"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"sleep_low": 20.0, "sleep_high": 23.0,
                                                                      "power_management": "guidance"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Abode HVAC Coordinator"
    entry = r["result"]
    assert entry.data == {"outdoor_temperature": "sensor.outdoor_t", "outdoor_humidity": "sensor.outdoor_rh",
                          "grid_power": "sensor.grid", "battery_charge": "sensor.battery", "battery_size": 10.0,
                          "grid_positive_is_import": False}
    room = entry.options["rooms"][0]
    assert room["heads"] == [{"climate": "climate.office", "outdoor_unit": "Shed"}]
    assert room["profile"]["setpoint_step"] == 0.5  # read from the live unit [D048]
    assert room["power_management"] == "guidance"
    assert "lockout_reason" in room and entry.options["outdoor_unit_names"] == ["Shed"]
    assert room["window_facing"] == "nw"  # option values are lower case, as translation keys must be
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED


def visible(result: Any, key: str) -> Any:
    """The condition under which a form shows a field, None for always."""
    for k in result["data_schema"].schema:
        if str(k) == key:
            return getattr(k, "visible", None)
    raise KeyError(key)


HIDES_FIELDS = hasattr(data_entry_flow, "Optional")  # Home Assistant 2026.11 and later


async def test_fields_are_shown_only_where_they_matter(hass: HomeAssistant,
                                                       climate_calls: dict[str, list[ServiceCall]]) -> None:
    """From 2026.11 a field with no effect without another is shown only with
    it, and left out of the answers while hidden; before 2026.11 every field
    is shown, as it was [HA]."""
    set_house_states(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    for _ in range(3):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["step_id"] == "power"
    exists = {"field": "battery_charge", "operator": "exists"}
    for key in ("battery_size", "battery_max_discharge", "reserve_margin"):
        assert visible(r, key) == (exists if HIDES_FIELDS else None)
    assert visible(r, "battery_charge") is None
    # a battery size with no charge sensor: hidden, so not kept, from 2026.11
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"battery_size": 10.0})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["step_id"] == "room_1"
    assert visible(r, "announce_through") == ({"field": "announce", "value": True} if HIDES_FIELDS else None)
    assert visible(r, "overhang_height") == ({"field": "window_facing", "operator": "exists"} if HIDES_FIELDS
                                             else None)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        **ROOM_1, "announce": False, "announce_through": ["media_player.kitchen"], "overhang_height": 1.2})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    entry = r["result"]
    room = entry.options["rooms"][0]
    if HIDES_FIELDS:
        assert "battery_size" not in entry.data
        assert "announce_through" not in room and "overhang_height" not in room
    else:
        assert entry.data["battery_size"] == 10.0
        assert room["announce_through"] == ["media_player.kitchen"] and room["overhang_height"] == 1.2
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_second_setup_is_refused(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    await setup_entry(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "single_instance_allowed"


async def test_heads_whose_ranges_do_not_overlap(hass: HomeAssistant) -> None:
    set_house_states(hass)
    hass.states.async_set("climate.cold", "cool", {**CLIMATE_ATTRS, "min_temp": 10.0, "max_temp": 14.0})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    r = await _house(hass, r["flow_id"])
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    r = await hass.config_entries.flow.async_configure(r["flow_id"],
                                                       {**ROOM_1, "climate": ["climate.office", "climate.cold"]})
    assert r["errors"] == {"climate": "ranges_do_not_overlap"}
    # one takes only a range, the other cannot take one [D024]
    hass.states.async_set("climate.ranged", "cool", {**CLIMATE_ATTRS, "supported_features": 2 | 8})
    hass.states.async_set("climate.single", "cool", {**CLIMATE_ATTRS, "supported_features": 1 | 8})
    r = await hass.config_entries.flow.async_configure(r["flow_id"],
                                                       {**ROOM_1, "climate": ["climate.ranged", "climate.single"]})
    assert r["errors"] == {"climate": "heads_take_different_setpoints"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**ROOM_1, "climate": []})
    assert r["errors"] == {"climate": "no_climate"}
    hass.states.async_set("climate.den", "cool", CLIMATE_ATTRS)
    r = await hass.config_entries.flow.async_configure(r["flow_id"],
                                                       {**ROOM_1, "climate": ["climate.office", "climate.den"]})
    assert r["step_id"] == "room_2" and r["description_placeholders"]["count"] == "1 of 2"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"outdoor_unit": "Shed"})
    assert r["step_id"] == "room_2" and r["description_placeholders"] == {"climate": "climate.den",
                                                                          "count": "2 of 2"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"outdoor_unit": "Shed"})
    assert r["step_id"] == "room_3"


async def test_solar_forecast_sources_are_offered(hass: HomeAssistant) -> None:
    source = MockConfigEntry(domain="forecast_solar", title="Roof")
    source.add_to_hass(hass)

    async def forecast(hass: HomeAssistant, entry_id: str) -> Any:
        return None

    with patch("homeassistant.components.energy.websocket_api.async_get_energy_platforms",
               return_value={"forecast_solar": forecast}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        for _ in range(3):
            r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
        assert r["step_id"] == "power"
        assert "solar_forecast_sources" in [str(k) for k in r["data_schema"].schema]
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"solar_forecast_sources": [source.entry_id]})
    assert r["step_id"] == "announcements"


async def test_reconfigure_reopens_the_house(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE,
                                                                   "entry_id": entry.entry_id})
    assert r["step_id"] == "tariff"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert suggested(r, "outdoor_temperature") == "sensor.outdoor_t"  # pre-filled [R18]
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"outdoor_temperature": "sensor.outdoor_t"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"weather": "weather.home"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"tts_engine": "tts.speaker"})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "reconfigure_successful"
    assert entry.data == {"outdoor_temperature": "sensor.outdoor_t", "weather": "weather.home",
                          "tts_engine": "tts.speaker"}  # the humidity sensor emptied is gone
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_suggests_the_grid_direction(hass: HomeAssistant,
                                                       climate_calls: dict[str, list[ServiceCall]]) -> None:
    """At a moment of grid evidence (load above solar, the battery not
    falling) a positive reading is an import [D038]."""
    set_house_states(hass)
    hass.states.async_set("sensor.load", "2.0", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.solar", "0.0", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.battery", "50", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.grid", "1500", {"unit_of_measurement": "W"})
    entry = await setup_entry(hass, data={"house_load": "sensor.load", "solar_power": "sensor.solar",
                                          "battery_charge": "sensor.battery"})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE,
                                                                   "entry_id": entry.entry_id})
    for _ in range(3):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"house_load": "sensor.load", "solar_power": "sensor.solar", "battery_charge": "sensor.battery",
                       "grid_power": "sensor.grid"})
    assert r["step_id"] == "grid_direction"
    assert suggested(r, "grid_positive_is_import") == "import"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"grid_positive_is_import": "import"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["reason"] == "reconfigure_successful"
    assert entry.data["grid_positive_is_import"] is True



async def test_the_power_screen_opens_when_the_energy_platforms_fail(hass: HomeAssistant) -> None:
    """Another integration's energy platform failing leaves the solar forecast
    choice empty; the screen still opens [D061]."""
    set_house_states(hass)
    with patch("homeassistant.components.energy.websocket_api.async_get_energy_platforms",
               side_effect=RuntimeError("a platform failed")):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        for _ in range(3):
            r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["step_id"] == "power"

async def test_reconfigure_while_not_loaded_suggests_no_grid_direction(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={"rooms": []}, version=1, minor_version=1)
    entry.add_to_hass(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE,
                                                                   "entry_id": entry.entry_id})
    for _ in range(3):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"grid_power": "sensor.grid"})
    assert r["step_id"] == "grid_direction" and suggested(r, "grid_positive_is_import") is None


async def test_reconfigure_without_a_grid_sensor_drops_its_direction(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    set_house_states(hass)
    hass.states.async_set("sensor.grid", "1500", {"unit_of_measurement": "W"})
    entry = await setup_entry(hass, data={"grid_power": "sensor.grid", "grid_positive_is_import": True})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE,
                                                                   "entry_id": entry.entry_id})
    for _ in range(3):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"grid_power": "sensor.grid"})
    assert r["step_id"] == "announcements"  # the same sensor: not asked again
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert entry.data == {"grid_power": "sensor.grid", "grid_positive_is_import": True}
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE,
                                                                   "entry_id": entry.entry_id})
    for _ in range(5):  # every screen emptied: the grid sensor, and its direction, go
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["reason"] == "reconfigure_successful"
    assert entry.data == {}


async def test_reconfigure_asks_the_grid_direction_while_its_repair_stands(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    """The repair says to check the direction on Reconfigure, so the Grid
    direction screen is shown for the same sensor while it stands [D038]."""
    set_house_states(hass)
    hass.states.async_set("sensor.grid", "1500", {"unit_of_measurement": "W"})
    entry = await setup_entry(hass, data={"grid_power": "sensor.grid", "grid_positive_is_import": True})
    grid = entry.runtime_data.coordinator.grid
    assert grid is not None
    grid.sign_repair = True
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE,
                                                                   "entry_id": entry.entry_id})
    for _ in range(3):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"grid_power": "sensor.grid"})
    assert r["step_id"] == "grid_direction"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"grid_positive_is_import": "export"})
    assert r["step_id"] == "announcements"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["reason"] == "reconfigure_successful"
    assert entry.data["grid_positive_is_import"] is False

async def test_options_add_edit_and_remove_a_room_without_a_reload(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]
) -> None:
    set_house_states(hass)
    hass.states.async_set("climate.bedroom", "off", CLIMATE_ATTRS)
    entry = await setup_entry(hass)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.MENU and r["menu_options"] == ["rooms", "learning"]
    assert "Office: 1 air conditioner, comfort 23 to 26" in r["description_placeholders"]["summary"]
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "rooms"})
    assert r["menu_options"] == ["add_room", "edit_room", "remove_room"]
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "add_room"})
    assert r["step_id"] == "room_1"
    # an air conditioner already in another room [D021]
    r = await hass.config_entries.options.async_configure(r["flow_id"], {**ROOM_1, "name": "Bedroom"})
    assert r["errors"] == {"climate": "climate_in_use"}
    assert r["description_placeholders"] == {"entity": "climate.office", "room": "Office"}
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {**ROOM_1, "name": "Bedroom", "climate": ["climate.bedroom"], "lockout_reason": "Painting"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options["lockout_reasons"] == ["Painting"]
    bedroom = next(rm for rm in entry.options["rooms"] if rm["name"] == "Bedroom")
    state = hass.states.get("sensor.bedroom")
    assert state is not None and state.state == "lockout"  # added at once, no reload [R18][D021]

    # edit: pre-filled, its learning kept, its device renamed
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "rooms"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "edit_room"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"room_id": bedroom["room_id"]})
    assert r["step_id"] == "room_1" and suggested(r, "name") == "Bedroom"
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {**ROOM_1, "name": "Main bedroom", "climate": ["climate.bedroom"]})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {})
    assert suggested(r, "occupied_low") == 23.0
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"occupied_low": 22.0,
                                                                         "power_management": "guidance"})
    await hass.async_block_till_done(wait_background_tasks=True)
    edited = next(rm for rm in entry.options["rooms"] if rm["room_id"] == bedroom["room_id"])
    assert edited["name"] == "Main bedroom" and edited["lockout_reason"] == "Not locked out"
    assert (edited["occupied_low"], edited["power_management"]) == (22.0, "guidance")
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, bedroom["room_id"]), entry.entry_id)
    assert device is not None and device.name == "Main bedroom"

    # edit again, clearing the comfort fields: each takes its default again, as on screen 1
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "rooms"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "edit_room"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"room_id": bedroom["room_id"]})
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {**ROOM_1, "name": "Main bedroom", "climate": ["climate.bedroom"]})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {})
    assert suggested(r, "occupied_low") == 22.0
    r = await hass.config_entries.options.async_configure(r["flow_id"], {})
    await hass.async_block_till_done(wait_background_tasks=True)
    edited = next(rm for rm in entry.options["rooms"] if rm["room_id"] == bedroom["room_id"])
    assert (edited["occupied_low"], edited["power_management"]) == (23.0, "off")

    # remove: its device and entities go with it [R18]
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "rooms"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "remove_room"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"room_id": bedroom["room_id"]})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [rm["room_id"] for rm in entry.options["rooms"]] == [ROOM_ID]
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, bedroom["room_id"]), entry.entry_id) is None
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{bedroom['room_id']}_mode") is None


async def test_options_learning(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass)
    for step, reason in (("seed", "seed_started"), ("reset", "reset_started")):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "learning"})
        assert r["menu_options"] == ["seed", "reset"]
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": step})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"room_id": ROOM_ID})
        assert r["step_id"] == f"confirm_{step}" and r["description_placeholders"] == {"room": "Office"}
        r = await hass.config_entries.options.async_configure(r["flow_id"], {})
        assert r["type"] is FlowResultType.ABORT and r["reason"] == reason
        await hass.async_block_till_done(wait_background_tasks=True)


async def test_learning_refused_while_the_room_is_seeding(hass: HomeAssistant,
                                                          climate_calls: dict[str, list[ServiceCall]]) -> None:
    """One seeding at a time: a second would be dropped, so the screen says
    so rather than that it started."""
    set_house_states(hass)
    entry = await setup_entry(hass)
    entry.runtime_data.coordinator.rooms[ROOM_ID].seeding = True
    for step in ("seed", "reset"):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "learning"})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": step})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"room_id": ROOM_ID})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {})
        assert r["type"] is FlowResultType.ABORT and r["reason"] == "seeding_in_progress"
        assert r["description_placeholders"] == {"room": "Office"}
    entry.runtime_data.coordinator.rooms[ROOM_ID].seeding = False


async def test_options_with_no_rooms(hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass, rooms=[])
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert "No rooms yet." in r["description_placeholders"]["summary"]
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "learning"})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "no_rooms"
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "rooms"})
    assert r["menu_options"] == ["add_room"]


async def test_learning_needs_the_integration_loaded(hass: HomeAssistant,
                                                     climate_calls: dict[str, list[ServiceCall]]) -> None:
    set_house_states(hass)
    entry = await setup_entry(hass, rooms=[stored_room()])
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "learning"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "seed"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"room_id": ROOM_ID})
    assert await hass.config_entries.async_unload(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "not_loaded"
