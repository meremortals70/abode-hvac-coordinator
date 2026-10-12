"""0.9.3: what the living room did on the night of 11/12 October 2026.

Each test names the decision record it belongs to (DR-059 to DR-066). The pure
decisions are in `test_core.py`; this file is what only a running Home
Assistant can show.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun import freeze_time
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.abode_hvac_coordinator.const import (
    CONF_ALLOW_COMFORT_REDUCTION,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_DISCHARGE_KW,
    CONF_BATTERY_SOC_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMPERATURE_ENTITY,
    CONF_RESERVE_MARGIN_KWH,
    CONF_ROOMS,
    CONF_SOLAR_POWER_ENTITY,
    DOMAIN,
    ISSUE_SOLAR_FORECAST_UNAVAILABLE,
    POWER_MANAGEMENT_ENFORCED,
    POWER_MANAGEMENT_GUIDANCE,
)
from custom_components.abode_hvac_coordinator.models import (
    ActuatorStep,
    DecisionTrace,
    Mode,
)
from custom_components.abode_hvac_coordinator.power import OFF_HOLD
from custom_components.abode_hvac_coordinator.regulate import CompressorState
from custom_components.abode_hvac_coordinator.staleness import INDOOR_TOLERANCE
from custom_components.abode_hvac_coordinator.tariff import TariffSeries
from custom_components.abode_hvac_coordinator.thermal import Coefficient

_STORE = "abode_hvac_coordinator.model"
_UNIT = {
    "hvac_modes": ["off", "cool", "heat", "dry", "fan_only"],
    "hvac_action": "cooling",
    "fan_modes": ["auto", "quiet"],
    "min_temp": 18.0,
    "max_temp": 30.0,
    "target_temp_step": 1.0,
    "supported_features": (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
    ).value,
}


async def _setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    temperature: str = "25.9",
    humidity: str = "60.0",
    room_extra: dict | None = None,
    options_extra: dict | None = None,
    freezer=None,
) -> object:
    room = {**entry.options[CONF_ROOMS][0], **(room_extra or {})}
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_ROOMS: [room], **(options_extra or {})}
    )
    hass.states.async_set("sensor.test_temperature", temperature)
    hass.states.async_set("sensor.test_humidity", humidity)
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set("climate.test", "cool", _UNIT)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    if freezer is not None:
        # The store's delayed writes run on the event loop's clock, which
        # only the freezer moves; freeze_time would move it back again.
        freezer.tick(timedelta(minutes=5))
        async_fire_time_changed(hass)
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        return coordinator
    with freeze_time(dt_util.utcnow() + timedelta(minutes=5)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    return coordinator


async def _run_for(hass: HomeAssistant, freezer, minutes: float) -> None:
    """Let the clock run thirty seconds at a time.

    The coordinator evaluates on its own timer as it does in a house, and the
    store's delayed writes fire when they are due.
    """
    for _ in range(int(minutes * 2)):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def _switch_off(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.test_room_automatic_control"},
        blocking=True,
    )
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# DR-064 - the store reaches disk while the integration is running
# ---------------------------------------------------------------------------


async def test_a_choice_reaches_disk_while_the_room_keeps_being_evaluated(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict,
    freezer,
) -> None:
    """Every evaluation used to push the write out again, so Automatic
    control switched off at 07:39 was never written and came back on."""
    await _setup(hass, mock_config_entry, freezer=freezer)
    await _switch_off(hass)
    await _run_for(hass, freezer, minutes=2)
    assert hass_storage[_STORE]["data"]["switched_off"] == ["test_room"]


async def test_learned_state_reaches_disk_while_the_room_keeps_being_evaluated(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict,
    freezer,
) -> None:
    coordinator = await _setup(hass, mock_config_entry, freezer=freezer)
    model = coordinator.model_for("test_room")  # type: ignore[attr-defined]
    model.k_loss = Coefficient(value=0.17, variance=0.01, samples=99)
    await _run_for(hass, freezer, minutes=6)
    stored = hass_storage[_STORE]["data"]["rooms"]["test_room"]["thermal"]
    assert stored["k_loss"]["samples"] >= 99


async def test_unloading_writes_everything_first(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    await _setup(hass, mock_config_entry)
    await _switch_off(hass)
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[_STORE]["data"]["switched_off"] == ["test_room"]


async def test_a_reload_reads_what_was_chosen_a_moment_before(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Sleep set at 20:27 was gone after the reload at 21:40."""
    await _setup(hass, mock_config_entry)
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.test_room_comfort_mode", "option": "sleep"},
        blocking=True,
    )
    await _switch_off(hass)
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_config_entry.runtime_data
    assert coordinator.comfort_mode("test_room").value == "sleep"
    assert coordinator.is_room_switched_off("test_room")


# ---------------------------------------------------------------------------
# DR-059 - a refused restart leaves the unit on its fan
# ---------------------------------------------------------------------------


async def test_a_refused_restart_leaves_the_unit_on_its_fan(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """06:35:57 and 06:52:01: a fan-only coast was turned off for a minute
    because the compressor had stopped less than five minutes before."""
    coordinator = await _setup(hass, mock_config_entry)
    room = coordinator.rooms["test_room"]  # type: ignore[attr-defined]
    now = dt_util.utcnow()
    for group in room.groups:
        coordinator._compressors[group] = CompressorState(  # type: ignore[attr-defined]
            running=False, changed_at=now - timedelta(minutes=2)
        )
    trace = DecisionTrace(
        room_id="test_room", at=now, mode=Mode.OCCUPIED, actuator=ActuatorStep.COMPRESSOR
    )
    coordinator._guard_cycling(room, trace, now)  # type: ignore[attr-defined]
    assert trace.actuator is ActuatorStep.FAN
    assert any("starting refused" in note for note in trace.rejected), trace.rejected


async def test_a_refused_restart_on_a_unit_with_no_fan_mode_is_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    hass.states.async_set(
        "climate.test", "cool", {**_UNIT, "hvac_modes": ["off", "cool", "heat"]}
    )
    room = coordinator.rooms["test_room"]  # type: ignore[attr-defined]
    coordinator.rooms["test_room"] = room.__class__(  # type: ignore[attr-defined]
        **{**{f: getattr(room, f) for f in room.__dataclass_fields__}, "capabilities": None}
    )
    room = coordinator.rooms["test_room"]  # type: ignore[attr-defined]
    now = dt_util.utcnow()
    for group in room.groups:
        coordinator._compressors[group] = CompressorState(  # type: ignore[attr-defined]
            running=False, changed_at=now - timedelta(minutes=2)
        )
    trace = DecisionTrace(
        room_id="test_room", at=now, mode=Mode.OCCUPIED, actuator=ActuatorStep.COMPRESSOR
    )
    coordinator._guard_cycling(room, trace, now)  # type: ignore[attr-defined]
    assert trace.actuator is ActuatorStep.OFF


# ---------------------------------------------------------------------------
# DR-060 - the coast counts the moisture the coil gives back
# ---------------------------------------------------------------------------


def _outdoor(hass: HomeAssistant) -> dict:
    hass.states.async_set("sensor.outdoor_temperature", "20.0")
    hass.states.async_set("sensor.outdoor_humidity", "90.0")
    return {
        CONF_OUTDOOR_TEMPERATURE_ENTITY: "sensor.outdoor_temperature",
        CONF_OUTDOOR_HUMIDITY_ENTITY: "sensor.outdoor_humidity",
    }


_COAST_BANDS = {"bands": {"occupied": {"low": 23.5, "high": 26.0}}}


def _learn(coordinator: object) -> None:
    model = coordinator.model_for("test_room")  # type: ignore[attr-defined]
    model.k_sensible = Coefficient(value=2.0, variance=0.01, samples=40)
    model.k_loss = Coefficient(value=0.5, variance=0.01, samples=40)
    model.k_solar = Coefficient(value=1.0, variance=0.01, samples=40)


async def test_an_unlearned_rebound_refuses_the_weather_coast_while_cooling(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(
        hass,
        mock_config_entry,
        temperature="24.3",
        humidity="55.0",
        room_extra=_COAST_BANDS,
        options_extra=_outdoor(hass),
    )
    _learn(coordinator)
    with freeze_time(dt_util.utcnow() + timedelta(minutes=10)):
        await coordinator.async_refresh()  # type: ignore[attr-defined]
        await hass.async_block_till_done()
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.state != "coast", state.attributes["reasons"]
    assert any(
        "moisture comes back off the coil" in note
        for note in state.attributes["rejected"]
    ), state.attributes["rejected"]


async def test_a_learned_rebound_is_counted_in_the_projection(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(
        hass,
        mock_config_entry,
        temperature="24.3",
        humidity="55.0",
        room_extra=_COAST_BANDS,
        options_extra=_outdoor(hass),
    )
    _learn(coordinator)
    room = coordinator.rooms["test_room"]  # type: ignore[attr-defined]
    now = dt_util.utcnow()
    coordinator._was_cooling["test_room"] = True  # type: ignore[attr-defined]
    without = coordinator._unaided_inputs(room, now)  # type: ignore[attr-defined]
    model = coordinator.model_for("test_room")  # type: ignore[attr-defined]
    model.k_rh_rebound = Coefficient(value=24.0, variance=0.01, samples=40)
    counted = coordinator._unaided_inputs(room, now)  # type: ignore[attr-defined]
    assert without["unaided_refused"] is not None
    assert counted["unaided_refused"] is None
    assert counted["rebound_relative_humidity"] == 61.0
    # The same room takes longer to come back once the water is counted.
    assert counted["unaided_return_minutes"] is None or (
        without["unaided_return_minutes"] is not None
        and counted["unaided_return_minutes"] > without["unaided_return_minutes"]
    ), (without, counted)


async def test_the_minutes_after_a_stop_teach_the_rebound(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry, temperature="24.0")
    room = coordinator.rooms["test_room"]  # type: ignore[attr-defined]
    # After the setup's own evaluation, five minutes on.
    start = dt_util.utcnow() + timedelta(minutes=6)
    # Every state change happens inside its frozen instant: a state change
    # refreshes the coordinator, and outside the freeze that refresh would
    # anchor the learning interval at an earlier, real clock time.
    with freeze_time(start):
        hass.states.async_set("sensor.test_humidity", "55.0")
        await hass.async_block_till_done()
        await coordinator.async_refresh()  # type: ignore[attr-defined]
        # The compressor stops; the unit is on its fan.
        hass.states.async_set(
            "climate.test", "fan_only", {**_UNIT, "hvac_action": "fan"}
        )
        await hass.async_block_till_done()
        await coordinator.async_refresh()  # type: ignore[attr-defined]
    for minutes, humidity in ((2, "57.0"), (4, "59.0")):
        with freeze_time(start + timedelta(minutes=minutes)):
            hass.states.async_set("sensor.test_humidity", humidity)
            await hass.async_block_till_done()
            await coordinator.async_refresh()  # type: ignore[attr-defined]
            await hass.async_block_till_done()
    model = coordinator.model_for(room.room_id)  # type: ignore[attr-defined]
    assert model.k_rh_rebound.samples >= 1
    assert model.k_rh_rebound.value > 0.0
    assert model.k_loss.samples == 0


# ---------------------------------------------------------------------------
# DR-061, DR-062, DR-063 - the power budget
# ---------------------------------------------------------------------------


def _morning_series(now) -> TariffSeries:
    return TariffSeries.from_response(
        {
            "intervals": [
                {
                    "start_time": (now - timedelta(minutes=35)).isoformat(),
                    "end_time": (now + timedelta(hours=4, minutes=25)).isoformat(),
                    "rate": "standard",
                    "per_kwh": 0.30,
                    "export_per_kwh": 0.05,
                    "constraints": ["no_grid_import"],
                },
                {
                    "start_time": (now + timedelta(hours=4, minutes=25)).isoformat(),
                    "end_time": (now + timedelta(hours=8)).isoformat(),
                    "rate": "free",
                    "per_kwh": 0.0,
                    "export_per_kwh": 0.0,
                    "constraints": [],
                },
            ]
        },
        now,
    )


async def _setup_power(
    hass: HomeAssistant, entry: MockConfigEntry, management: str
) -> object:
    hass.states.async_set("sensor.battery_soc", "20.0")
    hass.states.async_set("sensor.solar_power", "100")
    hass.states.async_set("sensor.house_load", "500")
    coordinator = await _setup(
        hass,
        entry,
        temperature="23.4",
        humidity="63.0",
        room_extra={CONF_ALLOW_COMFORT_REDUCTION: management},
        options_extra={
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_BATTERY_CAPACITY_KWH: 13.5,
            CONF_BATTERY_MAX_DISCHARGE_KW: 5.0,
            CONF_SOLAR_POWER_ENTITY: "sensor.solar_power",
            CONF_HOUSE_LOAD_ENTITY: "sensor.house_load",
            CONF_RESERVE_MARGIN_KWH: 1.0,
        },
    )
    now = dt_util.utcnow()
    coordinator.tariff = _morning_series(now)  # type: ignore[attr-defined]
    coordinator._power_context = coordinator._compute_power_context(now)  # type: ignore[attr-defined]
    return coordinator


def _ceiling(
    coordinator: object,
    *,
    mode=Mode.OCCUPIED,
    actuator=ActuatorStep.COMPRESSOR,
    now=None,
    shortfall_lasted: bool = True,
):
    """The budget's decision for the room at `now`.

    `shortfall_lasted` (the default) treats any shortfall as having lasted
    the ten-minute hold of DR-067 already, so these tests see the budget's own
    decision; the hold is tested on its own.
    """
    now = now or dt_util.utcnow()
    if shortfall_lasted:
        coordinator._shortfall_since.setdefault(  # type: ignore[attr-defined]
            "test_room", now - OFF_HOLD - timedelta(minutes=1)
        )
    room = coordinator.rooms["test_room"]  # type: ignore[attr-defined]
    inputs = coordinator._inputs_for(room, now)  # type: ignore[attr-defined]
    trace = DecisionTrace(
        room_id="test_room", at=now, mode=mode, actuator=actuator, demand="cool"
    )
    coordinator._power_ceiling(  # type: ignore[attr-defined]
        room, inputs, trace, now, coordinator._capabilities(room)  # type: ignore[attr-defined]
    )
    return trace


def _forecast_from(now) -> list:
    hour = now.replace(minute=0, second=0, microsecond=0)
    return [
        (hour + timedelta(hours=offset), wh)
        for offset, wh in enumerate((200.0, 900.0, 1800.0, 2800.0, 3500.0))
    ]


async def test_the_mornings_forecast_sun_keeps_the_room_running(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """06:35, 20% battery, 0.1 kW of sun so far: turned off that morning."""
    coordinator = await _setup_power(hass, mock_config_entry, POWER_MANAGEMENT_ENFORCED)
    before = _ceiling(coordinator)
    assert before.actuator is ActuatorStep.OFF, before.reasons
    coordinator._ceiling_bins.clear()  # type: ignore[attr-defined]
    coordinator._solar_points = _forecast_from(dt_util.utcnow())  # type: ignore[attr-defined]
    after = _ceiling(coordinator)
    assert after.actuator is ActuatorStep.COMPRESSOR, after.reasons
    assert after.power_budget_kw is not None and after.power_budget_kw > 1.0
    assert any("kWh of forecast solar" in note for note in after.reasons)


async def test_guidance_never_turns_the_unit_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup_power(hass, mock_config_entry, POWER_MANAGEMENT_GUIDANCE)
    trace = _ceiling(coordinator)
    assert trace.actuator is ActuatorStep.COMPRESSOR
    assert trace.power_ceiling_c == 0.0
    assert any("guidance only" in note for note in trace.reasons)


async def test_enforced_turns_a_coasting_unit_off_when_nothing_can_be_afforded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """D4: low humidity coasts on the fan unless power constrains it."""
    coordinator = await _setup_power(hass, mock_config_entry, POWER_MANAGEMENT_ENFORCED)
    trace = _ceiling(coordinator, mode=Mode.COAST, actuator=ActuatorStep.FAN)
    assert trace.actuator is ActuatorStep.OFF


async def test_a_coasting_unit_the_budget_affords_is_left_alone(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup_power(hass, mock_config_entry, POWER_MANAGEMENT_ENFORCED)
    coordinator._solar_points = _forecast_from(dt_util.utcnow())  # type: ignore[attr-defined]
    trace = _ceiling(coordinator, mode=Mode.COAST, actuator=ActuatorStep.FAN)
    assert trace.actuator is ActuatorStep.FAN
    assert trace.power_ceiling_c is None


async def test_the_ceiling_does_not_jump_back_up_a_minute_later(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """06:31-07:13: the setpoint flipped between 18 and 24 C about twenty
    times as the allowance crossed the at-setpoint draw."""
    coordinator = await _setup_power(hass, mock_config_entry, POWER_MANAGEMENT_ENFORCED)
    now = dt_util.utcnow()
    first = _ceiling(coordinator, now=now)
    assert first.actuator is ActuatorStep.OFF
    hass.states.async_set("sensor.solar_power", "2500")
    later = now + timedelta(minutes=1)
    coordinator._budget_solar_samples.clear()  # type: ignore[attr-defined]
    coordinator._power_context = coordinator._compute_power_context(later)  # type: ignore[attr-defined]
    coordinator._note_power_samples(later, coordinator._power_context)  # type: ignore[attr-defined]
    second = _ceiling(coordinator, now=later)
    assert second.power_budget_kw is not None and second.power_budget_kw > 1.0
    assert second.actuator is ActuatorStep.OFF, second.reasons


async def test_the_selected_forecasts_are_read_and_added_together(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    north = MockConfigEntry(domain="forecast_solar", title="North")
    west = MockConfigEntry(domain="forecast_solar", title="West")
    north.add_to_hass(hass)
    west.add_to_hass(hass)
    stamp = "2026-10-12T07:00:00+10:00"

    async def _forecast(hass_: HomeAssistant, entry_id: str) -> dict:
        return {"wh_hours": {stamp: 900 if entry_id == north.entry_id else 300}}

    coordinator.solar_forecast_entry_ids = [north.entry_id, west.entry_id]  # type: ignore[attr-defined]
    with patch(
        "homeassistant.components.energy.websocket_api.async_get_energy_platforms",
        AsyncMock(return_value={"forecast_solar": _forecast}),
    ):
        assert await coordinator._async_refresh_solar_forecast()  # type: ignore[attr-defined]
    assert [wh for _, wh in coordinator._solar_points] == [1200.0]  # type: ignore[attr-defined]


async def test_a_forecast_that_cannot_be_read_is_named_and_the_rest_used(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    north = MockConfigEntry(domain="forecast_solar", title="North")
    north.add_to_hass(hass)

    async def _forecast(hass_: HomeAssistant, entry_id: str) -> dict:
        return {"wh_hours": {"2026-10-12T07:00:00+10:00": 900}}

    coordinator.solar_forecast_entry_ids = [north.entry_id, "gone"]  # type: ignore[attr-defined]
    with patch(
        "homeassistant.components.energy.websocket_api.async_get_energy_platforms",
        AsyncMock(return_value={"forecast_solar": _forecast}),
    ):
        await coordinator._async_refresh_solar_forecast()  # type: ignore[attr-defined]
    assert [wh for _, wh in coordinator._solar_points] == [900.0]  # type: ignore[attr-defined]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_SOLAR_FORECAST_UNAVAILABLE)
    assert issue is not None
    assert "gone" in issue.translation_placeholders["reason"]


async def test_with_nothing_chosen_the_energy_dashboards_forecasts_are_used(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    manager = MagicMock()
    manager.data = {
        "energy_sources": [
            {"type": "solar", "config_entry_solar_forecast": ["north", "west"]}
        ]
    }
    assert coordinator.solar_forecast_entry_ids is None  # type: ignore[attr-defined]
    with patch(
        "homeassistant.components.energy.data.async_get_manager",
        AsyncMock(return_value=manager),
    ):
        entries = await coordinator._async_solar_forecast_entries()  # type: ignore[attr-defined]
    assert entries == ["north", "west"]


# ---------------------------------------------------------------------------
# DR-065 - the setpoint rounds the way the room is driven
# ---------------------------------------------------------------------------


async def test_a_cooling_setpoint_rounds_down_not_to_the_room(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """20:55: the loop wanted 0.2 C below a 25.9 C room; 26.0 was sent."""
    coordinator = await _setup(hass, mock_config_entry, temperature="25.9")
    room = coordinator.rooms["test_room"]  # type: ignore[attr-defined]
    now = dt_util.utcnow()
    inputs = coordinator._inputs_for(room, now)  # type: ignore[attr-defined]
    trace = DecisionTrace(
        room_id="test_room",
        at=now,
        mode=Mode.SLEEP,
        actuator=ActuatorStep.COMPRESSOR,
        demand="cool",
        target_dry_bulb_c=25.7,
    )
    coordinator._regulate(room, inputs, trace, now)  # type: ignore[attr-defined]
    assert trace.commanded_dry_bulb_c == 25.0, trace.reasons


# ---------------------------------------------------------------------------
# DR-066 - a sensor reporting an unchanged value is not stale
# ---------------------------------------------------------------------------


async def test_a_sensor_reporting_the_same_value_for_hours_is_still_read(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, freezer
) -> None:
    """03:32-06:00: the room's temperature did not change, and at 05:33 the
    room was left with no reading for 27 minutes."""
    coordinator = await _setup(hass, mock_config_entry, temperature="23.7")
    freezer.tick(INDOOR_TOLERANCE + timedelta(hours=1))
    hass.states.async_set("sensor.test_temperature", "23.7")
    assert coordinator._number("sensor.test_temperature") == 23.7  # type: ignore[attr-defined]


async def test_a_sensor_that_has_gone_silent_is_still_caught(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, freezer
) -> None:
    coordinator = await _setup(hass, mock_config_entry, temperature="23.7")
    freezer.tick(INDOOR_TOLERANCE + timedelta(hours=1))
    assert coordinator._number("sensor.test_temperature") is None  # type: ignore[attr-defined]
