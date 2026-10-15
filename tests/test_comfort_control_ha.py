"""0.9.7: the root-and-branch review of 15 October, against a real Home
Assistant. The pure decisions are in `test_comfort_control.py`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)
from test_controls import _learn, _outdoor, _publish, _settle, _setup

from custom_components.abode_hvac_coordinator.const import CONF_BANDS
from custom_components.abode_hvac_coordinator.models import (
    ActuatorStep,
    ComfortMode,
    DecisionTrace,
    Mode,
)
from custom_components.abode_hvac_coordinator.regulate import (
    INTEGRATE_WITHIN_C,
    RegulatorState,
)

_BANDS = {
    CONF_BANDS: {
        "occupied": {"low": 25.0, "high": 28.0},
        "sleep": {"low": 21.0, "high": 24.0},
    }
}


def _trace(hass: HomeAssistant) -> dict:
    state = hass.states.get("sensor.test_room_mode")
    assert state is not None
    return dict(state.attributes)


# ---------------------------------------------------------------------------
# DR-092 - the evaluation that sees a band change decides knowing it
# ---------------------------------------------------------------------------


async def test_leaving_sleep_does_not_heat_on_the_first_evaluation(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """15 October, 05:12. The first evaluation after Sleep ended saw the day
    band in full, a room below it, and sent heat."""
    coordinator = await _setup(
        hass, mock_config_entry, temperature="22.0", room_extra=_BANDS
    )
    await coordinator.async_set_comfort_mode("test_room", ComfortMode.SLEEP)
    await _settle(hass, coordinator, minutes=2)
    assert _trace(hass)["mode"] == "sleep"

    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    await coordinator.async_set_comfort_mode("test_room", ComfortMode.SCHEDULE)
    await hass.async_block_till_done()

    assert "heat" not in [call.data["hvac_mode"] for call in mode_calls]
    reasons = _trace(hass)["reasons"]
    assert any(r.startswith("band ramping") for r in reasons), reasons
    assert coordinator._waking == {"test_room"}


async def test_waking_lasts_until_the_ramp_is_over(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """DR-092. Inside the ramping band is not inside the daytime band."""
    coordinator = await _setup(
        hass, mock_config_entry, temperature="22.0", room_extra=_BANDS
    )
    await coordinator.async_set_comfort_mode("test_room", ComfortMode.SLEEP)
    await _settle(hass, coordinator, minutes=2)
    await coordinator.async_set_comfort_mode("test_room", ComfortMode.SCHEDULE)
    await hass.async_block_till_done()
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    for minutes in (10, 30, 50, 70):
        await _settle(hass, coordinator, minutes=minutes)
    assert "test_room" in coordinator._waking
    assert "heat" not in [call.data["hvac_mode"] for call in mode_calls]


# ---------------------------------------------------------------------------
# DR-090 - the comparison is not moved by the component's own command
# ---------------------------------------------------------------------------


class _Model:
    def __init__(self) -> None:
        self.asked: list[float] = []

    def sensible_rate_at(self, approach: float) -> float:
        self.asked.append(approach)
        return 1.0


async def test_the_cooling_rate_is_read_at_the_target_not_the_units_setpoint(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry, temperature="24.0")
    _publish(hass, state="cool", temperature=16.0)
    coordinator._last_target["test_room"] = 22.5
    model = _Model()
    coordinator.model_for = lambda room_id: model  # type: ignore[method-assign]
    room = coordinator.rooms["test_room"]
    coordinator._sensible_rate_for(room, dt_util.utcnow())
    assert model.asked == [1.5]


async def test_time_in_mode_restarts_when_the_unit_changes_mode(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    room = coordinator.rooms["test_room"]
    mode, cool_since = coordinator._unit_mode_since["test_room"]
    assert mode == "cool"
    later = coordinator._inputs_for(room, cool_since + timedelta(minutes=7))
    assert later.unit_mode_minutes == 7.0

    # The state change is itself evaluated, so the change is seen at once.
    _publish(hass, state="dry")
    await hass.async_block_till_done()
    mode, dry_since = coordinator._unit_mode_since["test_room"]
    assert mode == "dry"
    assert dry_since != cool_since
    held = coordinator._inputs_for(room, dry_since + timedelta(minutes=5))
    assert held.unit_mode == "dry"
    assert held.unit_mode_minutes == 5.0


async def test_a_unit_first_seen_carries_no_hold(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Nothing is known of how long a unit was in its mode before start-up."""
    coordinator = await _setup(hass, mock_config_entry)
    coordinator._unit_mode_since.clear()
    room = coordinator.rooms["test_room"]
    inputs = coordinator._inputs_for(room, dt_util.utcnow())
    assert inputs.unit_mode_minutes is not None
    assert inputs.unit_mode_minutes >= 20.0


# ---------------------------------------------------------------------------
# DR-095 - the unit's sensor measured against the room's
# ---------------------------------------------------------------------------


async def test_the_units_own_reading_is_measured_against_the_room(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry, temperature="26.0")
    _publish(hass, state="cool", current_temperature=23.0)
    await _settle(hass, coordinator, minutes=1)
    state = coordinator._regulators["test_room"]
    assert state.sensor_offset_c == -3.0


async def test_an_off_unit_is_not_measured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry, temperature="26.0")
    coordinator._regulators["test_room"] = RegulatorState()
    _publish(hass, state="off", current_temperature=23.0)
    await _settle(hass, coordinator, minutes=1)
    assert coordinator._regulators["test_room"].sensor_offset_c is None


# ---------------------------------------------------------------------------
# DR-097 - inside the band the unit's thermostat holds the target
# ---------------------------------------------------------------------------


def _in_band_trace(target: float) -> DecisionTrace:
    trace = DecisionTrace(
        room_id="test_room", at=dt_util.utcnow(), mode=Mode.OCCUPIED
    )
    trace.actuator = ActuatorStep.NONE
    trace.hold_setpoint = True
    trace.target_dry_bulb_c = target
    return trace


async def test_inside_the_band_the_unit_is_given_its_target_not_the_curve(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The simulated Office was asked for 2.2 C below the room with the room
    already below its target."""
    coordinator = await _setup(
        hass,
        mock_config_entry,
        temperature="24.0",
        options_extra=_outdoor(hass, "30.0"),
    )
    _learn(coordinator)
    room = coordinator.rooms["test_room"]
    state = RegulatorState(trim_c=-0.4, sensor_offset_c=0.5)
    inputs = coordinator._inputs_for(room, dt_util.utcnow())
    setpoint, pid = coordinator._room_loop_setpoint(
        room, state, inputs, _in_band_trace(24.5), "cool", "cool"
    )
    assert pid is None
    assert setpoint == 24.6

    # Out of band, the learned curve brings the room in.
    pulling = _in_band_trace(24.5)
    pulling.actuator = ActuatorStep.COMPRESSOR
    pulling.hold_setpoint = False
    _, pid = coordinator._room_loop_setpoint(
        room, state, inputs, pulling, "cool", "cool"
    )
    assert pid is not None


async def test_the_trim_is_learned_while_the_band_is_held(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Until 0.9.7 the trim was frozen inside the band."""
    coordinator = await _setup(
        hass, mock_config_entry, temperature="26.2", humidity="40.0"
    )
    _publish(hass, state="cool")
    await _settle(hass, coordinator, minutes=1)
    trace = coordinator.data["test_room"]
    assert trace.actuator is ActuatorStep.NONE and trace.hold_setpoint, trace
    before = coordinator._regulators["test_room"].trim_c
    for minutes in (10, 20, 30):
        await _settle(hass, coordinator, minutes=minutes)
    after = coordinator._regulators["test_room"].trim_c
    assert after != before


async def test_a_pull_down_far_from_target_learns_no_trim(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A humid evening's pull-down wound the trim to -1.3 C and the band was
    held too cold for three hours."""
    coordinator = await _setup(hass, mock_config_entry, temperature="33.0")
    trace = coordinator.data["test_room"]
    assert trace.actuator is ActuatorStep.COMPRESSOR
    assert abs(33.0 - trace.target_dry_bulb_c) > INTEGRATE_WITHIN_C
    for minutes in (10, 20, 30):
        await _settle(hass, coordinator, minutes=minutes)
    state = coordinator._regulators["test_room"]
    assert state.trim_c == 0.0
    assert state.heat_trim_c == 0.0


async def test_coasting_waits_for_the_target_in_the_running_house(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """DR-098 through the coordinator: a cooling unit in band, model saying
    the band holds, room short of its target: no coast."""
    coordinator = await _setup(hass, mock_config_entry, temperature="26.2")
    _publish(hass, state="cool")
    room = coordinator.rooms["test_room"]
    inputs = replace(
        coordinator._inputs_for(room, dt_util.utcnow()),
        predicted_to_hold=True,
        coasting_permitted=True,
    )
    from custom_components.abode_hvac_coordinator.modes import evaluate_room

    trace = evaluate_room(room, inputs)
    assert trace.mode is Mode.OCCUPIED
    assert any("coast: not yet" in r for r in trace.rejected), trace.rejected


# ---------------------------------------------------------------------------
# DR-086 - the setpoint sent stands until the loop has moved most of a step
# ---------------------------------------------------------------------------


async def test_a_setpoint_already_sent_stands_through_the_deadband(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry, temperature="26.2")
    room = coordinator.rooms["test_room"]
    now = dt_util.utcnow()
    inputs = coordinator._inputs_for(room, now)
    from custom_components.abode_hvac_coordinator.modes import evaluate_room

    trace = evaluate_room(room, inputs)
    state = coordinator._regulators.setdefault("test_room", RegulatorState())
    coordinator._regulate(room, inputs, trace, now)
    first = trace.commanded_dry_bulb_c
    assert first is not None

    def loop_value(trace_now) -> float:
        value, _ = coordinator._room_loop_setpoint(
            room, state, inputs, trace_now, "cool", "cool"
        )
        assert value is not None
        return value

    # The loop moves half a step below what was sent: rounding alone would
    # send a degree less; the deadband keeps what was sent.
    again = evaluate_room(room, inputs)
    state.trim_c += first - 0.5 - loop_value(again)
    coordinator._regulate(room, inputs, again, now + timedelta(seconds=30))
    assert again.commanded_dry_bulb_c == first
    assert any(r.startswith("setpoint held at") for r in again.reasons), again.reasons

    # Most of a step: a new one.
    moved = evaluate_room(room, inputs)
    state.trim_c += first - 0.9 - loop_value(moved)
    coordinator._regulate(room, inputs, moved, now + timedelta(seconds=60))
    assert moved.commanded_dry_bulb_c == first - 1.0


# ---------------------------------------------------------------------------
# DR-088 - the Leave button
# ---------------------------------------------------------------------------

_LEAVE = "button.test_room_leave"


async def _press_leave(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": _LEAVE}, blocking=True
    )
    await hass.async_block_till_done()


async def test_leave_turns_the_room_off_at_once(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    assert hass.states.get(_LEAVE) is not None
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    await _press_leave(hass)
    assert [call.data["hvac_mode"] for call in mode_calls] == ["off"]
    trace = _trace(hass)
    assert trace["mode"] == "unoccupied"
    assert "left: the owner has left the room" in trace["reasons"]
    assert coordinator.is_room_left("test_room")
    assert coordinator.store.left() == {"test_room"}


async def test_a_room_left_stays_off_while_presence_still_reads_on(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The sensor has not caught up with the person leaving."""
    coordinator = await _setup(hass, mock_config_entry)
    await _press_leave(hass)
    mode_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    for minutes in (5, 15, 30):
        await _settle(hass, coordinator, minutes=minutes)
    assert _trace(hass)["mode"] == "unoccupied"
    assert not [c for c in mode_calls if c.data["hvac_mode"] != "off"]


async def test_someone_arriving_after_the_room_empties_ends_leave(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    await _press_leave(hass)
    hass.states.async_set("binary_sensor.test_presence", "off")
    await _settle(hass, coordinator, minutes=5)
    assert coordinator.is_room_left("test_room")
    hass.states.async_set("binary_sensor.test_presence", "on")
    await _settle(hass, coordinator, minutes=10)
    assert not coordinator.is_room_left("test_room")
    assert coordinator.store.left() == set()


async def test_the_owner_turning_the_room_back_on_ends_leave(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    coordinator = await _setup(hass, mock_config_entry)
    await _press_leave(hass)
    await coordinator.async_set_comfort_mode("test_room", ComfortMode.SLEEP)
    await hass.async_block_till_done()
    assert not coordinator.is_room_left("test_room")


async def test_leave_survives_a_restart(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, hass_storage: dict
) -> None:
    hass_storage["abode_hvac_coordinator.model"] = {
        "version": 1,
        "minor_version": 1,
        "key": "abode_hvac_coordinator.model",
        "data": {"rooms": {}, "groups": {}, "left": ["test_room"]},
    }
    coordinator = await _setup(hass, mock_config_entry)
    assert coordinator.is_room_left("test_room")
    assert _trace(hass)["mode"] == "unoccupied"
