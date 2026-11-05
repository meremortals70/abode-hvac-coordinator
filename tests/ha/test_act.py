"""act.py through Home Assistant: sending, failures, and the user's commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from tests.pure._load import load

from .harness import (
    async_mock_service,
)

act = load("act")
models = load("models")
T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def room(**kw: Any) -> Any:
    profile = models.CapabilityProfile(("off", "cool", "heat"), ("auto", "quiet"), ("fixed",), (), 17.0, 30.0,
                                       0.5, False)
    base: dict[str, Any] = {
        "room_id": "r1", "name": "Office", "heads": (models.HeadConfig("climate.a"), models.HeadConfig("climate.b")),
        "temperature_sensor": "sensor.t", "humidity_sensor": "sensor.rh", "occupied_band": (23.0, 26.0),
        "sleep_band": (20.0, 23.0), "precool_band": (23.0, 26.0), "profile": profile,
    }
    base.update(kw)
    return models.RoomConfig(**base)


async def test_each_call_sent_and_recorded(hass: HomeAssistant) -> None:
    modes = async_mock_service(hass, "climate", "set_hvac_mode")
    temps = async_mock_service(hass, "climate", "set_temperature")
    log = act.CommandLog()
    calls = [models.ServiceCall("climate", "set_hvac_mode", {"entity_id": "climate.a", "hvac_mode": "cool"}),
             models.ServiceCall("climate", "set_temperature", {"entity_id": "climate.a", "temperature": 24.0})]
    records = await act.async_send(hass, calls, room_id="r1", log=log, now=T0,
                                   about=act.CommandContext(2.0, 25.0, 24.5))
    assert [c.data for c in modes] == [{"entity_id": "climate.a", "hvac_mode": "cool"}]
    assert [c.data for c in temps] == [{"entity_id": "climate.a", "temperature": 24.0}]
    assert [(r.service, r.ok, r.rate) for r in records] == [("set_hvac_mode", True, 2.0), ("set_temperature", True, 2.0)]
    assert list(log.records) == records  # [D052]


async def test_a_failed_call_does_not_stop_the_others(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    async def fail(call: ServiceCall) -> None:
        raise HomeAssistantError("unit offline")

    hass.services.async_register("climate", "set_hvac_mode", fail)
    fans = async_mock_service(hass, "climate", "set_fan_mode")
    calls = [models.ServiceCall("climate", "set_hvac_mode", {"entity_id": "climate.a", "hvac_mode": "cool"}),
             models.ServiceCall("climate", "set_fan_mode", {"entity_id": "climate.a", "fan_mode": "auto"})]
    records = await act.async_send(hass, calls, room_id="r1", log=act.CommandLog(), now=T0,
                                   about=act.CommandContext(None, None, None))
    assert [(r.ok, r.error) for r in records] == [(False, "unit offline"), (True, None)]
    assert len(fans) == 1
    assert "set_hvac_mode {'hvac_mode': 'cool'} failed: unit offline" in caplog.text


async def test_user_command_sent_to_every_head(hass: HomeAssistant) -> None:
    temps = async_mock_service(hass, "climate", "set_temperature")
    log = act.CommandLog()
    off = models.RoomChoices(automatic_control=False)
    records = await act.async_user_command(hass, room(), off, act.UserCommand(setpoint=22.0), log=log, now=T0)
    assert [c.data for c in temps] == [{"entity_id": "climate.a", "temperature": 22.0},
                                       {"entity_id": "climate.b", "temperature": 22.0}]
    assert all(r.by_user for r in records)  # [D052]


async def test_user_command_refused_with_a_translated_reason(hass: HomeAssistant) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await act.async_user_command(hass, room(), models.RoomChoices(), act.UserCommand(hvac_mode="heat"),
                                     log=act.CommandLog(), now=T0)
    assert err.value.translation_domain == "abode_hvac_coordinator"
    assert err.value.translation_key == "automatic_control_on"
    assert err.value.translation_placeholders == {"room": "Office"}


async def test_user_command_the_unit_fails(hass: HomeAssistant) -> None:
    async def fail(call: ServiceCall) -> None:
        raise HomeAssistantError("unit offline")

    hass.services.async_register("climate", "set_fan_mode", fail)
    with pytest.raises(HomeAssistantError) as err:
        await act.async_user_command(hass, room(), models.RoomChoices(automatic_control=False),
                                     act.UserCommand(fan_mode="quiet"), log=act.CommandLog(), now=T0)
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "command_failed"
    assert err.value.translation_placeholders == {"entity": "climate.a", "error": "unit offline"}


async def test_fahrenheit_system_is_sent_fahrenheit(hass: HomeAssistant) -> None:
    from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

    hass.config.units = US_CUSTOMARY_SYSTEM
    temps = async_mock_service(hass, "climate", "set_temperature")
    await act.async_user_command(hass, room(heads=(models.HeadConfig("climate.a"),)),
                                 models.RoomChoices(automatic_control=False), act.UserCommand(setpoint=22.0),
                                 log=act.CommandLog(), now=T0)
    assert [c.data["entity_id"] for c in temps] == ["climate.a"]
    sent = temps[0].data["temperature"]
    assert abs(sent - 71.6) < 1e-9 and (sent - 32.0) / 1.8 >= 22.0  # converts to the unit's 22 C, never under [HA]


async def test_a_c_unit_in_a_f_house_at_its_maximum_is_not_refused(hass: HomeAssistant) -> None:
    """At the top of the unit's range the value sent converts to no more
    than its maximum: Home Assistant refuses even a float above (2026.11.0b0
    climate/services.py:150; 2026.9.4 climate/__init__.py:828) [HA][D051]."""
    from homeassistant.components.climate import (
        ClimateEntity,
        ClimateEntityFeature,
        HVACMode,
    )
    from homeassistant.const import UnitOfTemperature
    from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

    try:
        from homeassistant.components.climate.services import (
            _async_service_temperature_set as handler,
        )
    except ImportError:  # 2026.9
        from homeassistant.components.climate import (
            async_service_temperature_set as handler,
        )

    capabilities = load("capabilities")

    class Unit(ClimateEntity):
        _attr_temperature_unit = UnitOfTemperature.CELSIUS
        _attr_min_temp = 16.0
        _attr_max_temp = 31.0
        _attr_target_temperature_step = 0.5
        _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]  # noqa: RUF012 - Home Assistant's own attribute type
        _attr_hvac_mode = HVACMode.HEAT
        sent: list[float] = []  # noqa: RUF012 - one entity per test

        async def async_set_temperature(self, **kwargs: Any) -> None:
            self.sent.append(kwargs["temperature"])

    hass.config.units = US_CUSTOMARY_SYSTEM
    ent = Unit()
    ent.hass = hass
    profile = capabilities.read_profile("heat", {**ent.capability_attributes, "supported_features": 1}, "°F", "°C",
                                        (ent.min_temp, ent.max_temp))
    assert (profile.min_setpoint, profile.max_setpoint) == (16.0, 31.0)  # the unit's own, not the shown 61-88 F
    for wanted in (31.0, 30.5, 16.0, 22.0):
        value = act.to_system(wanted, "°F", profile.setpoint_step, profile.max_setpoint)
        await handler(ent, ServiceCall(hass, "climate", "set_temperature", {"temperature": value}))
        got = ent.sent[-1]
        assert abs(got - wanted) < 1e-9 and 16.0 <= got <= 31.0
        if wanted < 31.0:
            assert got >= wanted  # never under, below the top


async def test_a_half_degree_drop_shown_in_whole_f_is_sent(hass: HomeAssistant) -> None:
    """A C unit at 24.5 C in a F house shows 76 F, which reads 24.44 C: a
    wanted 24.0 C is within the step but not within half a shown degree,
    so it is sent [HA][D007]."""
    from homeassistant.components.climate import (
        ClimateEntity,
        ClimateEntityFeature,
        HVACMode,
    )
    from homeassistant.const import UnitOfTemperature
    from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

    capabilities = load("capabilities")
    models = load("models")

    class Unit(ClimateEntity):
        _attr_temperature_unit = UnitOfTemperature.CELSIUS
        _attr_min_temp = 16.0
        _attr_max_temp = 31.0
        _attr_target_temperature_step = 0.5
        _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]  # noqa: RUF012 - Home Assistant's own attribute type
        _attr_hvac_mode = HVACMode.HEAT

    hass.config.units = US_CUSTOMARY_SYSTEM
    ent = Unit()
    ent.hass = hass
    t0 = datetime(2026, 11, 2, tzinfo=UTC)
    for held, wanted, sent in ((24.5, 24.0, True), (24.0, 24.0, True), (23.5, 24.0, True)):
        ent._attr_target_temperature = held
        attrs = {**ent.state_attributes, **ent.capability_attributes, "supported_features": 1}
        profile = capabilities.read_profile("heat", attrs, "°F", "°C")
        room = models.RoomConfig(room_id="r1", name="Office", heads=(models.HeadConfig("climate.a"),),
                                 temperature_sensor="sensor.t", humidity_sensor="sensor.rh",
                                 occupied_band=(23.0, 26.0), sleep_band=(20.0, 23.0), precool_band=(23.0, 26.0),
                                 profile=profile)
        head = models.HeadCommand("climate.a", "heat", wanted, None, None, wanted, None, None)
        step = models.LimitsResult(models.StepKind.HEAT, (head,), (), (), (), False, False, False, False, (), (), (),
                                   ())
        live = {"climate.a": models.RawState("climate.a", "heat", attrs, t0, t0)}
        ra = act.RoomAct(room)
        calls = ra.calls(now=t0, step=step, live=live, system_unit="°F", choices=models.RoomChoices())
        assert isinstance(attrs["temperature"], int | float) and float(attrs["temperature"]).is_integer()
        # shown too coarsely to tell and nothing sent yet: sent once
        assert ([c.service for c in calls] == ["set_temperature"]) is sent, (held, wanted)
        again = ra.calls(now=t0 + timedelta(minutes=1), step=step, live=live, system_unit="°F",
                         choices=models.RoomChoices())
        # then only a shown value more than half a shown degree off is sent again
        assert ([c.service for c in again] == ["set_temperature"]) is (held != wanted), (held, wanted)
