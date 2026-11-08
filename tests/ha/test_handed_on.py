"""What the cycle hands on: Observe is given each outdoor unit's learned draw
whatever its compressor is seen to do, so the house-load step (4.8, source 3)
can judge it [D028][Prop]; the Commanded setpoint of a range-only unit
[D024]."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from custom_components.abode_hvac_coordinator.models import CompressorState
from custom_components.abode_hvac_coordinator.observe import RoomObserver

from .conftest import (
    CLIMATE_ATTRS,
    HOUSE,
    ROOM_ID,
    profile_dict,
    set_house_states,
    setup_entry,
    stored_room,
)
from .test_cycle import TARIFF, Tariff, _intervals, _tick

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")


async def test_observe_is_given_the_draw_of_a_unit_whose_compressor_is_unknown(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    set_house_states(hass)  # no hvac_action, no frequency sensor: the compressor is unknown
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.coordinator
    await _tick(hass, freezer, timedelta(seconds=30))
    snap = coordinator.rooms[ROOM_ID].snapshot
    assert snap is not None and snap.heads[0].compressor is CompressorState.UNKNOWN
    unit = snap.heads[0].outdoor_unit
    coordinator.house_state.draw.table[unit] = {i: (1.2, 5) for i in range(6)}
    seen: list[Any] = []
    real = RoomObserver.build

    def spy(self: Any, **kw: Any) -> Any:
        seen.append(kw["draw_kw"])
        return real(self, **kw)

    with patch.object(RoomObserver, "build", spy):
        await _tick(hass, freezer, timedelta(seconds=30))
    assert seen and seen[-1].get(unit) == pytest.approx(1.2)


async def test_the_commanded_setpoint_of_a_range_only_unit_is_its_middle(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    """A unit that takes only a range is sent low and high; the Commanded
    setpoint sensor shows the middle of what was sent [D024]."""
    set_house_states(hass)
    attrs = {k: v for k, v in CLIMATE_ATTRS.items() if k != "temperature"}
    attrs.update(supported_features=2 | 8 | 32, target_temp_low=22.0, target_temp_high=26.0)
    hass.states.async_set("climate.office", "cool", attrs)
    entry = await setup_entry(hass, rooms=[stored_room(profile={**profile_dict(), "range_only": True})])
    run = entry.runtime_data.coordinator.rooms[ROOM_ID]
    for _ in range(12):
        await _tick(hass, freezer, timedelta(seconds=30))
    sent = [c.data for c in climate_calls.get("set_temperature", []) if "target_temp_low" in c.data]
    assert sent, "no range was sent"
    assert run.commanded_c == pytest.approx((sent[-1]["target_temp_low"] + sent[-1]["target_temp_high"]) / 2)


async def test_a_unit_running_unseen_is_not_counted_as_the_rest_of_the_house(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    """A head in a running mode with its compressor unknown is running for the
    power budget: its unit is not taken as stopped in the load samples, so
    its draw is not counted as the rest of the house [I10][D028]."""
    set_house_states(hass)  # cool, no hvac_action, no frequency sensor
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.coordinator
    await _tick(hass, freezer, timedelta(seconds=30))
    snap = coordinator.rooms[ROOM_ID].snapshot
    assert snap is not None and snap.heads[0].compressor is CompressorState.UNKNOWN
    seen: list[Any] = []
    loads = type(coordinator.budget.loads)
    real = loads.add

    def spy(self: Any, when: Any, load: Any, solar: Any, running: Any) -> Any:
        seen.append(running)
        return real(self, when, load, solar, running)

    with patch.object(loads, "add", spy):
        await _tick(hass, freezer, timedelta(seconds=30))
    assert seen and snap.heads[0].outdoor_unit in seen[-1]


async def test_a_blind_command_is_not_taken_as_another_unit_changing(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    """Only climate commands are kept as the last command the house-load step
    and the draw learner look at: a blind moved with a start does not spoil
    it [D028]."""
    from custom_components.abode_hvac_coordinator.models import CommandRecord

    set_house_states(hass)
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.coordinator

    async def sent(*_a: Any, **_k: Any) -> list[Any]:
        now = dt_util.utcnow()
        return [CommandRecord(now, ROOM_ID, e, s, {}, None, None, None, False, True)
                for e, s in (("climate.office", "set_hvac_mode"), ("cover.office_blind", "set_cover_position"))]

    with patch("custom_components.abode_hvac_coordinator.coordinator.async_send", sent):
        for _ in range(4):
            await _tick(hass, freezer, timedelta(seconds=30))
    assert "climate.office" in coordinator._last_command
    assert "cover.office_blind" not in coordinator._last_command


async def test_the_grid_repairs_survive_a_reload(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]],
) -> None:
    """A reload keeps the grid checks' standing repairs: the direction
    repair clears only on ten agreeing readings, as it says [D038][D043]."""
    set_house_states(hass)
    hass.states.async_set("sensor.grid", "1500", {"unit_of_measurement": "W"})
    entry = await setup_entry(hass, data={"grid_power": "sensor.grid", "grid_positive_is_import": True})
    grid = entry.runtime_data.coordinator.grid
    assert grid is not None
    grid.sign_repair, grid.contradictions, grid.import_repair_kwh = True, 10, 0.4
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    back = entry.runtime_data.coordinator.grid
    assert back is not None and back is not grid
    assert (back.sign_repair, back.import_repair_kwh) == (True, 0.4)


async def test_the_cost_is_unknown_while_the_draw_is_unlearned_and_the_compressor_unknown(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory
) -> None:
    """The living room's case: no frequency sensor, no hvac_action, a house-load
    sensor and no draw learned yet. The unit cools all evening; its cost is
    not known, so yesterday's cost reads unknown, not 0.00 [D097]."""
    set_house_states(hass)  # climate.office in cool, no hvac_action
    hass.states.async_set("sensor.load", "2500", {"unit_of_measurement": "W"})
    local = dt_util.as_local(dt_util.utcnow())
    freezer.move_to(local.replace(hour=23, minute=40, second=0, microsecond=0))
    Tariff(hass, _intervals())
    await setup_entry(hass, data={**HOUSE, "tariff_entry": TARIFF, "house_load": "sensor.load"})
    for _ in range(44):
        hass.states.async_set("sensor.load", "2500", {"unit_of_measurement": "W"}, force_update=True)
        await _tick(hass, freezer, timedelta(seconds=30))
    cost = hass.states.get("sensor.office_cost_of_comfort")
    assert cost is not None
    assert cost.state == "unknown", f"yesterday's cost reads {cost.state} for a day the unit ran with its draw unlearned"


async def test_the_guard_sees_a_unit_running_unseen(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    """A head cooling with its compressor unknown reaches the short-cycle
    guard as running, so a unit run by hand keeps its way on a shared
    outdoor unit and its run is held [D016][D020]."""
    set_house_states(hass)  # cool, no hvac_action, no frequency sensor
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.coordinator
    await _tick(hass, freezer, timedelta(seconds=30))
    snap = coordinator.rooms[ROOM_ID].snapshot
    assert snap is not None and snap.heads[0].compressor is CompressorState.UNKNOWN
    seen: list[Any] = []
    guard_type = type(coordinator.guard)
    real = guard_type.apply

    def capture(self: Any, now: Any, entries: Any) -> Any:
        seen.extend(entries)
        return real(self, now, entries)

    with patch.object(guard_type, "apply", capture):
        await _tick(hass, freezer, timedelta(seconds=30))
    unit = snap.heads[0].outdoor_unit
    assert seen and seen[-1].observed_running[unit] is True
    assert [h.climate for h in seen[-1].observed_heads] == ["climate.office"]


async def test_a_reversal_by_hand_restarts_the_run(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    """A running unit turned from cooling to heating is a stop and a start
    (decision of 6 November 2026) [D016]: the plan's minimum run and rest
    count from the turn, not from the first start."""
    set_house_states(hass)  # cool, running unseen
    entry = await setup_entry(hass)
    coordinator = entry.runtime_data.coordinator
    await _tick(hass, freezer, timedelta(seconds=30))
    await _tick(hass, freezer, timedelta(seconds=30))
    snap = coordinator.rooms[ROOM_ID].snapshot
    assert snap is not None
    unit = snap.heads[0].outdoor_unit
    assert coordinator._unit_running[unit] is True
    first = coordinator._running_since.get(unit)
    await _tick(hass, freezer, timedelta(minutes=20))
    set_house_states(hass, mode="heat")  # turned to heating by hand
    await _tick(hass, freezer, timedelta(seconds=30))
    turned = dt_util.utcnow()
    assert coordinator._unit_running[unit] is True
    assert coordinator._running_since[unit] != first
    assert abs((coordinator._running_since[unit] - turned).total_seconds()) <= 30
    assert coordinator._stopped_since[unit] == coordinator._running_since[unit]


async def test_the_decision_is_kept_in_the_recorder(
    hass: HomeAssistant, climate_calls: dict[str, list[ServiceCall]], freezer: FrozenDateTimeFactory,
) -> None:
    """Each plan's first step, its forecast every half-hour and its
    uncertainty are recorded, so a night can be read back; the full plan
    and the rest of the trace are not (decision of 8 November 2026)."""
    from custom_components.abode_hvac_coordinator.sensor import ModeSensor

    assert "decision" not in ModeSensor._unrecorded_attributes
    assert "plan" in ModeSensor._unrecorded_attributes
