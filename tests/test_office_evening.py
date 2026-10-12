"""0.9.4: the Office on the evening of 12 October 2026.

Battery 93% of 13 kWh with a 5 kWh reserve, the no-import window running to
midnight, the rest of the house drawing 0.6-0.7 kW. The Office was turned off
the minute after each start, because its own draw was counted as the house's
(DR-067), and turned off at 16:04 by a restart while its presence sensor was
still coming up (DR-068).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_living_room_night import _UNIT, _ceiling, _setup

from custom_components.abode_hvac_coordinator.const import (
    CONF_ALLOW_COMFORT_REDUCTION,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_MAX_DISCHARGE_KW,
    CONF_BATTERY_SOC_ENTITY,
    CONF_HOUSE_LOAD_ENTITY,
    CONF_RESERVE_MARGIN_KWH,
    CONF_SOLAR_POWER_ENTITY,
    POWER_MANAGEMENT_ENFORCED,
)
from custom_components.abode_hvac_coordinator.models import ActuatorStep
from custom_components.abode_hvac_coordinator.tariff import TariffSeries
from custom_components.abode_hvac_coordinator.thermal import Coefficient

_POWER = {
    CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
    CONF_BATTERY_CAPACITY_KWH: 13.0,
    CONF_BATTERY_MAX_DISCHARGE_KW: 5.0,
    CONF_SOLAR_POWER_ENTITY: "sensor.solar_power",
    CONF_HOUSE_LOAD_ENTITY: "sensor.house_load",
    CONF_RESERVE_MARGIN_KWH: 5.0,
}


def _evening(now: datetime) -> TariffSeries:
    """No grid import from an hour ago until 6.7 hours from now."""
    return TariffSeries.from_response(
        {
            "intervals": [
                {
                    "start_time": (now - timedelta(hours=1)).isoformat(),
                    "end_time": (now + timedelta(hours=6, minutes=42)).isoformat(),
                    "rate": "peak",
                    "per_kwh": 0.45,
                    "export_per_kwh": 0.05,
                    "constraints": ["no_grid_import"],
                },
                {
                    "start_time": (now + timedelta(hours=6, minutes=42)).isoformat(),
                    "end_time": (now + timedelta(hours=10)).isoformat(),
                    "rate": "offpeak",
                    "per_kwh": 0.20,
                    "export_per_kwh": 0.05,
                    "constraints": [],
                },
            ]
        },
        now,
    )


async def _office(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    hass.states.async_set("sensor.battery_soc", "96")
    hass.states.async_set("sensor.solar_power", "0")
    hass.states.async_set("sensor.house_load", "650")
    coordinator = await _setup(
        hass,
        entry,
        temperature="25.4",
        humidity="55.0",
        room_extra={CONF_ALLOW_COMFORT_REDUCTION: POWER_MANAGEMENT_ENFORCED},
        options_extra=_POWER,
    )
    coordinator.tariff = _evening(dt_util.utcnow())
    # The Office's learned draw: little at setpoint, more the harder it works.
    draw = coordinator.draw_for("climate.test")
    for index, kw in enumerate((0.2, 0.5, 0.9, 1.5)):
        draw.bins[index] = Coefficient(value=kw, variance=0.001, samples=40)
    coordinator._budget_house_samples.clear()
    coordinator._budget_solar_samples.clear()
    coordinator._base_load_samples.clear()
    coordinator._ceiling_bins.clear()
    coordinator._shortfall_since.clear()
    return coordinator


def _minute(
    hass: HomeAssistant,
    coordinator: Any,
    at: datetime,
    *,
    house_w: float,
    running: bool,
) -> None:
    """One evaluation's readings: the unit's state and the house load."""
    with freeze_time(at):
        hass.states.async_set(
            "climate.test",
            "cool" if running else "fan_only",
            {**_UNIT, "hvac_action": "cooling" if running else "fan"},
        )
        hass.states.async_set("sensor.house_load", str(house_w), force_update=True)
        hass.states.async_set("sensor.battery_soc", "96", force_update=True)
        hass.states.async_set("sensor.solar_power", "0", force_update=True)
        hass.states.async_set("sensor.test_temperature", "25.4", force_update=True)
        hass.states.async_set("sensor.test_humidity", "55.0", force_update=True)
        coordinator._power_context = coordinator._compute_power_context(at)
        coordinator._note_power_samples(at, coordinator._power_context)


def _expected_kw(coordinator: Any, at: datetime, rest_kw: float) -> float:
    """(96% of 13 kWh less the 5 kWh reserve) over the hours left, less the
    rest of the house: the budget worked out by hand."""
    hours = coordinator.tariff.hours_until_clear("no_grid_import", at)
    return (0.96 * 13.0 - 5.0) / hours - rest_kw


def _budget(coordinator: Any, at: datetime) -> Any:
    with freeze_time(at):
        return _ceiling(coordinator, now=at, shortfall_lasted=False)


async def test_starting_the_unit_does_not_shrink_its_own_budget(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """17:17: 0.52 kW allowed with the unit off, 0.08 kW a minute after it started."""
    coordinator = await _office(hass, mock_config_entry)
    start = dt_util.utcnow() + timedelta(minutes=1)
    for minute in range(10):
        _minute(hass, coordinator, start + timedelta(minutes=minute), house_w=650, running=False)
    off = _budget(coordinator, start + timedelta(minutes=9))
    # The unit starts and the house load jumps with it.
    for minute in range(10, 13):
        _minute(hass, coordinator, start + timedelta(minutes=minute), house_w=1460, running=True)
    on = _budget(coordinator, start + timedelta(minutes=12))
    assert off.power_budget_kw is not None and on.power_budget_kw is not None
    # About 0.5 kW, with the unit off and with it running.
    expected = _expected_kw(coordinator, start + timedelta(minutes=9), 0.65)
    assert abs(off.power_budget_kw - expected) < 0.01, off.reasons
    assert abs(on.power_budget_kw - off.power_budget_kw) < 0.02, on.reasons
    assert on.actuator is not ActuatorStep.OFF, on.reasons


async def test_a_spike_in_the_house_load_is_averaged(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _office(hass, mock_config_entry)
    start = dt_util.utcnow() + timedelta(minutes=1)
    loads = [650] * 9 + [2650]
    for minute, watts in enumerate(loads):
        _minute(hass, coordinator, start + timedelta(minutes=minute), house_w=watts, running=False)
    trace = _budget(coordinator, start + timedelta(minutes=9))
    # 0.83 kW of house on average (nine minutes at 0.65, one at 2.65), not
    # the 2.65 kW of the last minute.
    assert trace.power_budget_kw is not None
    expected = _expected_kw(coordinator, start + timedelta(minutes=9), 0.83)
    assert abs(trace.power_budget_kw - expected) < 0.01, trace.reasons


async def test_a_running_room_is_held_before_it_is_turned_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _office(hass, mock_config_entry)
    start = dt_util.utcnow() + timedelta(minutes=1)
    for minute in range(5):
        _minute(hass, coordinator, start + timedelta(minutes=minute), house_w=650, running=False)
    afforded = _budget(coordinator, start + timedelta(minutes=4))
    assert afforded.actuator is not ActuatorStep.OFF, afforded.reasons
    # The battery reading drops to the reserve: nothing can be afforded.
    hass.states.async_set("sensor.battery_soc", "38")
    for minute in range(5, 20):
        at = start + timedelta(minutes=minute)
        with freeze_time(at):
            hass.states.async_set("sensor.battery_soc", "38", force_update=True)
            hass.states.async_set("sensor.house_load", "650", force_update=True)
            hass.states.async_set("sensor.test_temperature", "25.4", force_update=True)
            hass.states.async_set("sensor.test_humidity", "55.0", force_update=True)
            coordinator._power_context = coordinator._compute_power_context(at)
            coordinator._note_power_samples(at, coordinator._power_context)
        trace = _budget(coordinator, at)
        if minute < 14:
            assert trace.actuator is not ActuatorStep.OFF, (minute, trace.reasons)
            assert any(
                "held at the gentlest setting" in note for note in trace.reasons
            ), trace.reasons
            assert trace.power_bin == "at_setpoint"
    assert trace.actuator is ActuatorStep.OFF, trace.reasons


async def test_a_room_that_has_run_since_startup_uses_the_estimate(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """No reading of the house with this room off yet: house load less its estimate."""
    coordinator = await _office(hass, mock_config_entry)
    start = dt_util.utcnow() + timedelta(minutes=1)
    for minute in range(3):
        _minute(hass, coordinator, start + timedelta(minutes=minute), house_w=1850, running=True)
    room = coordinator.rooms["test_room"]
    rest = coordinator._rest_of_house_kw(room, running=True, own_draw_kw=1.2)
    assert abs(rest - 0.65) < 1e-9


# ---------------------------------------------------------------------------
# DR-068 - occupancy survives a restart
# ---------------------------------------------------------------------------


async def test_a_reload_with_presence_still_coming_up_keeps_the_room_occupied(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """16:04: the update reloaded the integration, the presence sensor read
    unavailable for a minute, and the Office was turned off."""
    coordinator = await _setup(hass, mock_config_entry)
    with freeze_time(dt_util.utcnow() + timedelta(minutes=10)):
        hass.states.async_set("binary_sensor.test_presence", "on", force_update=True)
        hass.states.async_set("sensor.test_temperature", "25.9", force_update=True)
        hass.states.async_set("sensor.test_humidity", "60.0", force_update=True)
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert coordinator.store.occupancy() == {"test_room": True}

    hass.states.async_set("binary_sensor.test_presence", "unavailable")
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    reloaded = mock_config_entry.runtime_data
    assert reloaded is not coordinator
    await reloaded.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.state != "unoccupied", state.attributes["reasons"]
    assert state.attributes["actuator"] != "off", state.attributes["reasons"]
    assert "presence unknown, holding last known occupancy" in state.attributes["reasons"]


async def test_a_room_last_known_empty_stays_off_after_a_reload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Unknown presence holds what was last known, and that can be empty."""
    coordinator = await _setup(hass, mock_config_entry)
    hass.states.async_set("binary_sensor.test_presence", "unavailable")
    await hass.async_block_till_done()
    coordinator.store.set_occupancy("test_room", False)
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    reloaded = mock_config_entry.runtime_data
    await reloaded.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    assert state.state == "unoccupied", state.attributes["reasons"]


async def test_a_shortfall_ends_with_the_window(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Tomorrow's window starts with the full hold, not today's shortfall."""
    coordinator = await _office(hass, mock_config_entry)
    coordinator._shortfall_since["test_room"] = dt_util.utcnow()
    after = dt_util.utcnow() + timedelta(hours=7)
    _minute(hass, coordinator, after, house_w=650, running=True)
    _budget(coordinator, after)
    assert "test_room" not in coordinator._shortfall_since
