"""`tts.speak` with Home Assistant's own schema, for the spoken-warning tests.

0.9.5, DR-069. The tests before 0.9.5 recorded `tts.speak` calls through a
mock with no schema, so a call Home Assistant rejects passed them. This sets up
the real `tts` integration, takes the schema it registers for `tts.speak`, and
records calls through a mock that validates against it.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

ENGINE = "tts.test_engine"


async def real_tts_speak(
    hass: HomeAssistant, engines: tuple[str, ...] = (ENGINE,)
) -> list[ServiceCall]:
    """Calls to `tts.speak`, checked against the real schema."""
    assert await async_setup_component(hass, "tts", {})
    await hass.async_block_till_done()
    schema = hass.services.async_services()["tts"]["speak"].schema
    hass.services.async_remove("tts", "speak")
    for engine in engines:
        hass.states.async_set(engine, "unknown")
    return async_mock_service(hass, "tts", "speak", schema=schema)
