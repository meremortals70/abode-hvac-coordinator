"""The actuator: what is actually sent to a unit or a cover, and what is not.

Driven through Home Assistant's own service registry, with the climate and
cover services recorded (or made to fail) rather than patched out, so each
test sees exactly the calls a real unit would receive.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components.climate.const import ClimateEntityFeature
from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.abode_hvac_coordinator.actuator import (
    Actuator,
    _hvac_mode_of,
    _matches_live_state,
    mean_cover_position,
    profile_from_states,
    read_capabilities,
)
from custom_components.abode_hvac_coordinator.capabilities import RoomCapabilities
from custom_components.abode_hvac_coordinator.models import (
    ActuatorStep,
    DecisionTrace,
    Mode,
    RoomConfig,
)

_SINGLE = ClimateEntityFeature.TARGET_TEMPERATURE.value
_RANGE = ClimateEntityFeature.TARGET_TEMPERATURE_RANGE.value
_FAN = ClimateEntityFeature.FAN_MODE.value
_SWING = ClimateEntityFeature.SWING_MODE.value

_PROFILE = RoomCapabilities(
    hvac_modes=("off", "cool", "heat", "dry", "fan_only"),
    fan_modes=("auto", "quiet"),
    swing_modes=("off", "swing"),
    swing_horizontal_modes=("left", "right"),
    min_temp=18.0,
    max_temp=30.0,
    target_temp_step=1.0,
    single_target=True,
)


def _room(**extra: Any) -> RoomConfig:
    values: dict[str, Any] = {
        "room_id": "living",
        "name": "Living",
        "climate_entity_ids": ("climate.living",),
        "bands": {},
        "capabilities": _PROFILE,
    }
    values.update(extra)
    return RoomConfig(**values)


def _trace(actuator: ActuatorStep, **extra: Any) -> DecisionTrace:
    trace = DecisionTrace(room_id="living", at=dt_util.utcnow(), mode=Mode.OCCUPIED)
    trace.actuator = actuator
    for key, value in extra.items():
        setattr(trace, key, value)
    return trace


def _actuator(hass: HomeAssistant, *, vanes_manual: bool = False) -> Actuator:
    coordinator = MagicMock()
    coordinator.is_room_vanes_manual.return_value = vanes_manual
    return Actuator(hass, coordinator)


def _unit(
    hass: HomeAssistant,
    state: str = "off",
    entity_id: str = "climate.living",
    **attributes: Any,
) -> None:
    hass.states.async_set(
        entity_id,
        state,
        {
            "hvac_modes": ["off", "cool", "heat", "dry", "fan_only"],
            "supported_features": _SINGLE | _FAN | _SWING,
            "fan_modes": ["auto", "quiet"],
            "swing_modes": ["off", "swing"],
            **attributes,
        },
    )


def _calls(hass: HomeAssistant, domain: str = "climate") -> dict[str, list[ServiceCall]]:
    services = (
        ("set_hvac_mode", "set_temperature", "set_fan_mode", "set_swing_mode",
         "set_swing_horizontal_mode")
        if domain == "climate"
        else ("set_cover_position",)
    )
    return {name: async_mock_service(hass, domain, name) for name in services}


def _failing(hass: HomeAssistant, domain: str, service: str) -> None:
    async def fail(call: ServiceCall) -> None:
        raise HomeAssistantError(f"{service} refused")

    hass.services.async_register(domain, service, fail)


# ---------------------------------------------------------------------------
# Reading what a unit offers
# ---------------------------------------------------------------------------


def test_a_temperature_limit_that_is_not_a_number_is_unknown() -> None:
    state = State(
        "climate.x",
        "off",
        {"hvac_modes": ["off", "cool"], "min_temp": "cold", "max_temp": None},
    )
    profile = read_capabilities(state)
    assert profile is not None
    assert profile.min_temp is None
    assert profile.max_temp is None


def test_a_unit_publishing_no_modes_has_no_profile() -> None:
    assert read_capabilities(State("climate.x", "off", {})) is None


def test_a_room_with_no_heads_has_no_profile() -> None:
    assert profile_from_states([]) == (None, None)


def test_a_missing_head_leaves_the_room_without_a_profile() -> None:
    reporting = State("climate.a", "off", {"hvac_modes": ["off", "cool"]})
    assert profile_from_states([reporting, None]) == (None, None)


def test_a_head_that_is_not_reporting_leaves_the_room_without_a_profile() -> None:
    reporting = State("climate.a", "off", {"hvac_modes": ["off", "cool"]})
    silent = State("climate.b", "unavailable", {})
    assert profile_from_states([reporting, silent]) == (None, None)


def test_a_state_that_is_not_a_mode_is_no_mode() -> None:
    assert _hvac_mode_of(None) is None
    assert _hvac_mode_of(State("climate.x", "unavailable", {})) is None


def test_a_range_unit_matches_only_when_both_ends_match() -> None:
    features = {"supported_features": _RANGE}
    assert not _matches_live_state(
        State("climate.x", "heat_cool", features), "heat_cool", 24.0
    )
    assert _matches_live_state(
        State(
            "climate.x",
            "heat_cool",
            {**features, "target_temp_low": 23.0, "target_temp_high": 25.0},
        ),
        "heat_cool",
        24.0,
    )


def test_a_unit_reporting_no_setpoint_does_not_match() -> None:
    assert not _matches_live_state(State("climate.x", "cool", {}), "cool", 24.0)


async def test_covers_reporting_no_position_have_no_mean(hass: HomeAssistant) -> None:
    hass.states.async_set("cover.a", "open", {})
    assert mean_cover_position(hass, ("cover.a", "cover.missing")) is None


# ---------------------------------------------------------------------------
# Applying a decision
# ---------------------------------------------------------------------------


async def test_covers_are_the_whole_action_and_the_unit_is_turned_off(
    hass: HomeAssistant,
) -> None:
    _unit(hass, "cool")
    climate = _calls(hass)
    covers = _calls(hass, "cover")
    room = _room(cover_entity_ids=("cover.a",))
    trace = _trace(ActuatorStep.COVERS, demand="cool")
    await _actuator(hass).async_apply(room, trace)
    assert covers["set_cover_position"][0].data["position"] == 0
    assert climate["set_hvac_mode"][0].data["hvac_mode"] == "off"
    assert "moved covers to 0%" in trace.reasons


async def test_covers_do_not_stop_a_compressor_the_guard_is_holding(
    hass: HomeAssistant,
) -> None:
    _unit(hass, "cool")
    climate = _calls(hass)
    covers = _calls(hass, "cover")
    trace = _trace(ActuatorStep.COVERS, demand="heat", hold_compressor=True)
    await _actuator(hass).async_apply(_room(cover_entity_ids=("cover.a",)), trace)
    assert covers["set_cover_position"][0].data["position"] == 100
    assert climate["set_hvac_mode"] == []


async def test_covers_already_in_place_are_not_moved_again(hass: HomeAssistant) -> None:
    _unit(hass, "off")
    _calls(hass)
    covers = _calls(hass, "cover")
    actuator = _actuator(hass)
    room = _room(cover_entity_ids=("cover.a",))
    await actuator.async_apply(room, _trace(ActuatorStep.COVERS, demand="cool"))
    await actuator.async_apply(room, _trace(ActuatorStep.COVERS, demand="cool"))
    assert len(covers["set_cover_position"]) == 1


async def test_a_cover_that_fails_is_reported_and_tried_again_next_time(
    hass: HomeAssistant,
) -> None:
    _unit(hass, "off")
    _calls(hass)
    _failing(hass, "cover", "set_cover_position")
    actuator = _actuator(hass)
    trace = _trace(ActuatorStep.COVERS, demand="cool")
    await actuator.async_apply(_room(cover_entity_ids=("cover.a",)), trace)
    assert "covers: set_cover_position refused" in trace.rejected
    assert actuator._last_cover == {}


async def test_the_fan_coast_waits_while_the_guard_holds_the_compressor(
    hass: HomeAssistant,
) -> None:
    _unit(hass, "cool")
    climate = _calls(hass)
    await _actuator(hass).async_apply(
        _room(), _trace(ActuatorStep.FAN, hold_compressor=True)
    )
    assert climate["set_hvac_mode"] == []


async def test_dry_is_sent_as_dry_with_a_quiet_fan_and_settled_vane(
    hass: HomeAssistant,
) -> None:
    _unit(hass, "cool", fan_mode="auto", swing_mode="swing")
    climate = _calls(hass)
    trace = _trace(ActuatorStep.DRY)
    await _actuator(hass).async_apply(_room(), trace)
    assert climate["set_hvac_mode"][0].data["hvac_mode"] == "dry"
    assert climate["set_fan_mode"][0].data["fan_mode"] == "quiet"
    assert climate["set_swing_mode"][0].data["swing_mode"] == "off"


async def test_the_vane_is_left_alone_when_the_user_has_it(hass: HomeAssistant) -> None:
    _unit(hass, "off", swing_mode="swing")
    climate = _calls(hass)
    await _actuator(hass, vanes_manual=True).async_apply(
        _room(), _trace(ActuatorStep.DRY)
    )
    assert climate["set_hvac_mode"]
    assert climate["set_swing_mode"] == []


async def test_a_mode_the_unit_does_not_offer_is_refused_by_name(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set("climate.living", "off", {"hvac_modes": ["off", "cool"]})
    climate = _calls(hass)
    trace = _trace(ActuatorStep.DRY)
    await _actuator(hass).async_apply(_room(capabilities=None), trace)
    assert climate["set_hvac_mode"] == []
    assert "climate: climate.living advertises none of dry" in trace.rejected


async def test_a_fallback_mode_is_named_in_the_reasons(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "climate.living",
        "off",
        {"hvac_modes": ["off", "heat_cool"], "supported_features": _SINGLE},
    )
    climate = _calls(hass)
    trace = _trace(
        ActuatorStep.COMPRESSOR, demand="cool", commanded_dry_bulb_c=24.0
    )
    await _actuator(hass).async_apply(_room(capabilities=None), trace)
    assert climate["set_hvac_mode"][0].data["hvac_mode"] == "heat_cool"
    assert "cool unavailable, using heat_cool instead" in trace.reasons


async def test_a_missing_unit_is_reported(hass: HomeAssistant) -> None:
    trace = _trace(ActuatorStep.OFF)
    await _actuator(hass).async_apply(_room(), trace)
    assert "climate: climate.living is unavailable" in trace.rejected


async def test_a_failed_command_is_reported_and_not_remembered(
    hass: HomeAssistant,
) -> None:
    _unit(hass, "off")
    _failing(hass, "climate", "set_hvac_mode")
    actuator = _actuator(hass)
    trace = _trace(ActuatorStep.COMPRESSOR, demand="cool", commanded_dry_bulb_c=24.0)
    await actuator.async_apply(_room(), trace)
    assert "climate: set_hvac_mode refused" in trace.rejected
    assert actuator._last_climate == {}
    assert len(actuator.command_log) == 0


async def test_without_a_commanded_setpoint_the_target_is_sent(
    hass: HomeAssistant,
) -> None:
    _unit(hass, "off")
    climate = _calls(hass)
    trace = _trace(ActuatorStep.COMPRESSOR, demand="heat", target_dry_bulb_c=21.04)
    await _actuator(hass).async_apply(_room(), trace)
    assert climate["set_hvac_mode"][0].data["hvac_mode"] == "heat"
    assert climate["set_temperature"][0].data["temperature"] == 21.0


async def test_a_range_only_unit_is_sent_a_range_around_the_target(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(
        "climate.living",
        "off",
        {"hvac_modes": ["off", "heat_cool"], "supported_features": _RANGE},
    )
    climate = _calls(hass)
    trace = _trace(ActuatorStep.COMPRESSOR, demand="cool", commanded_dry_bulb_c=24.0)
    await _actuator(hass).async_apply(_room(capabilities=None), trace)
    sent = climate["set_temperature"][0].data
    assert (sent["target_temp_low"], sent["target_temp_high"]) == (23.0, 25.0)


async def test_a_unit_that_takes_no_target_says_so(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "climate.living", "off", {"hvac_modes": ["off", "cool"], "supported_features": 0}
    )
    climate = _calls(hass)
    trace = _trace(ActuatorStep.COMPRESSOR, demand="cool", commanded_dry_bulb_c=24.0)
    await _actuator(hass).async_apply(_room(capabilities=None), trace)
    assert climate["set_temperature"] == []
    assert "climate: climate.living takes no temperature target" in trace.rejected


async def test_without_a_profile_fan_and_vane_come_from_the_live_entity(
    hass: HomeAssistant,
) -> None:
    _unit(hass, "off", fan_mode="quiet", swing_mode="off")
    climate = _calls(hass)
    trace = _trace(ActuatorStep.COMPRESSOR, demand="cool", commanded_dry_bulb_c=24.0)
    await _actuator(hass).async_apply(_room(capabilities=None), trace)
    assert climate["set_fan_mode"][0].data["fan_mode"] == "auto"
    assert climate["set_swing_mode"][0].data["swing_mode"] == "swing"


async def test_without_a_profile_a_unit_with_no_fan_or_vane_gets_neither(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(
        "climate.living",
        "off",
        {"hvac_modes": ["off", "cool"], "supported_features": _SINGLE},
    )
    climate = _calls(hass)
    trace = _trace(ActuatorStep.COMPRESSOR, demand="cool", commanded_dry_bulb_c=24.0)
    await _actuator(hass).async_apply(_room(capabilities=None), trace)
    assert climate["set_hvac_mode"]
    assert climate["set_fan_mode"] == []
    assert climate["set_swing_mode"] == []


# ---------------------------------------------------------------------------
# Walking the setpoint of a unit in band (DR-056)
# ---------------------------------------------------------------------------


async def test_no_commanded_setpoint_means_nothing_is_walked(
    hass: HomeAssistant,
) -> None:
    _unit(hass, "cool", temperature=20.0)
    climate = _calls(hass)
    await _actuator(hass).async_apply(
        _room(), _trace(ActuatorStep.NONE, hold_setpoint=True)
    )
    assert climate["set_temperature"] == []


async def test_a_unit_reporting_no_setpoint_is_not_walked(hass: HomeAssistant) -> None:
    _unit(hass, "cool", temperature="unknown")
    climate = _calls(hass)
    await _actuator(hass).async_apply(
        _room(),
        _trace(ActuatorStep.NONE, hold_setpoint=True, commanded_dry_bulb_c=24.0),
    )
    assert climate["set_temperature"] == []


async def test_a_failed_walk_is_logged_and_the_room_carries_on(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    _unit(hass, "cool", temperature=20.0)
    _failing(hass, "climate", "set_temperature")
    actuator = _actuator(hass)
    trace = _trace(ActuatorStep.NONE, hold_setpoint=True, commanded_dry_bulb_c=24.0)
    await actuator.async_apply(_room(), trace)
    assert "Setting climate.living to 24.0 for living failed" in caplog.text
    assert actuator._last_climate == {}


# ---------------------------------------------------------------------------
# A change the user makes from the room's controls (DR-049)
# ---------------------------------------------------------------------------


async def test_a_room_whose_profile_was_never_read_refuses_controls(
    hass: HomeAssistant,
) -> None:
    with pytest.raises(ServiceValidationError) as raised:
        await _actuator(hass).async_user_command(
            _room(capabilities=None), hvac_mode="cool"
        )
    assert raised.value.translation_key == "control_profile_not_read"


async def test_a_setpoint_for_units_that_take_none_is_refused(
    hass: HomeAssistant,
) -> None:
    profile = RoomCapabilities(hvac_modes=("off", "fan_only"))
    with pytest.raises(ServiceValidationError) as raised:
        await _actuator(hass).async_user_command(
            _room(capabilities=profile), temperature=24.0
        )
    assert raised.value.translation_key == "control_value_not_offered"
    assert raised.value.translation_placeholders["control"] == "setpoint"


@pytest.mark.parametrize(
    ("argument", "value", "control"),
    [
        ("hvac_mode", "auto", "mode"),
        ("fan_mode", "turbo", "fan speed"),
        ("swing_mode", "3d", "vertical vane"),
        ("swing_horizontal_mode", "middle", "horizontal vane"),
    ],
)
async def test_a_value_the_units_do_not_offer_is_refused(
    hass: HomeAssistant, argument: str, value: str, control: str
) -> None:
    with pytest.raises(ServiceValidationError) as raised:
        await _actuator(hass).async_user_command(_room(), **{argument: value})
    assert raised.value.translation_placeholders["control"] == control


async def test_every_offered_control_reaches_every_head(hass: HomeAssistant) -> None:
    _unit(hass, "cool", entity_id="climate.living")
    _unit(hass, "cool", entity_id="climate.dining")
    climate = _calls(hass)
    actuator = _actuator(hass)
    room = _room(climate_entity_ids=("climate.living", "climate.dining"))
    actuator._last_climate["climate.living"] = ("cool", 24.0)
    await actuator.async_user_command(
        room,
        hvac_mode="cool",
        temperature=23.0,
        fan_mode="quiet",
        swing_mode="swing",
        swing_horizontal_mode="left",
    )
    for name in (
        "set_hvac_mode",
        "set_temperature",
        "set_fan_mode",
        "set_swing_mode",
        "set_swing_horizontal_mode",
    ):
        assert len(climate[name]) == 2, name
    assert climate["set_swing_horizontal_mode"][0].data["swing_horizontal_mode"] == "left"
    assert [entry["source"] for entry in actuator.command_log] == ["user"] * 4
    # The next evaluation reads the units rather than trusting what it sent.
    assert actuator._last_climate == {}


async def test_a_setpoint_for_a_unit_reporting_no_mode_fails_loudly(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set("climate.living", "unavailable", {})
    with pytest.raises(HomeAssistantError, match="is not reporting a mode"):
        await _actuator(hass).async_user_command(_room(), temperature=24.0)
    assert "Setting climate.living for living from its controls failed" in caplog.text


# ---------------------------------------------------------------------------
# Forgetting a removed room
# ---------------------------------------------------------------------------


async def test_forgetting_a_room_drops_what_was_sent_to_its_units(
    hass: HomeAssistant,
) -> None:
    actuator = _actuator(hass)
    actuator._last_climate = {
        "climate.living": ("cool", 24.0),
        "climate.office": ("cool", 23.0),
    }
    actuator._last_cover = {"living": 0, "office": 100}
    actuator.forget("living", {"climate.office"})
    assert actuator._last_climate == {"climate.office": ("cool", 23.0)}
    assert actuator._last_cover == {"office": 100}
