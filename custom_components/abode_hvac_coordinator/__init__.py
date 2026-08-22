"""Abode HVAC Coordinator — Layer 3.

Owns rooms, modes, the comfort index, actuator ordering, the thermal model and
the decision trace. It commands climate entities and covers directly. It never
writes battery actuators.

It does not own a tariff. The plan is held by Abode Power Tariffs and read from
it as a forward interval series; this integration acts on the constraints that
series declares and holds no periods, prices or rates of its own.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_DEADLINE,
    ATTR_ROOM_ID,
    CONF_CLIMATE_ENTITIES,
    CONF_CLIMATE_ENTITY,
    CONF_ROOM_ID,
    CONF_ROOMS,
    DOMAIN,
    SERVICE_CLEAR_OVERRIDE,
    SERVICE_HEADING_HOME,
)
from .coordinator import HvacCoordinator
from .store import ModelStore

PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HvacConfigEntry = ConfigEntry[HvacCoordinator]

_ROOM_SERVICE_SCHEMA = vol.Schema({vol.Required(ATTR_ROOM_ID): cv.string})

_HEADING_HOME_SCHEMA = _ROOM_SERVICE_SCHEMA.extend(
    {vol.Optional(ATTR_DEADLINE): cv.datetime}
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services.

    Services are registered here rather than in async_setup_entry so that
    automations referencing them validate even when the entry is not loaded.
    """

    def _coordinators() -> list[HvacCoordinator]:
        return [
            entry.runtime_data
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        ]

    def _resolve(room_id: str) -> HvacCoordinator:
        for coordinator in _coordinators():
            if room_id in coordinator.rooms:
                return coordinator
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_room",
            translation_placeholders={"room_id": room_id},
        )

    async def _handle_heading_home(call: ServiceCall) -> None:
        room_id: str = call.data[ATTR_ROOM_ID]
        coordinator = _resolve(room_id)
        coordinator.async_request_heading_home(room_id, call.data.get(ATTR_DEADLINE))
        await coordinator.async_request_refresh()

    async def _handle_clear_override(call: ServiceCall) -> None:
        room_id: str = call.data[ATTR_ROOM_ID]
        coordinator = _resolve(room_id)
        coordinator.async_clear_override(room_id)
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_HEADING_HOME,
        _handle_heading_home,
        schema=_HEADING_HOME_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_OVERRIDE,
        _handle_clear_override,
        schema=_ROOM_SERVICE_SCHEMA,
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: HvacConfigEntry) -> bool:
    """Bring an older entry up to the current shape.

    **Version 1 to 2 — a room's head became a list.** Every stored room
    carried one `climate_entity_id`; from 0.8.8 it carries
    `climate_entity_ids`, because a room can be served by two indoor units.

    No outdoor unit groups are written. Every existing head becomes its own
    compressor, which is exactly how the short-cycle guard behaved before the
    change, so nothing about an existing house moves until it is told to.

    A version bump ships a migration, always. Without one every existing
    install logs "Migration handler not found" and refuses to load, and the
    user is told to delete and re-add — which is never an acceptable answer to
    a change of ours.
    """
    if entry.version > 2:
        # Downgraded from a future release. Nothing here can help.
        return False

    if entry.version == 1:
        data = {**entry.data}
        options = {**entry.options}
        for holder in (data, options):
            rooms = holder.get(CONF_ROOMS)
            if not rooms:
                continue
            holder[CONF_ROOMS] = [_room_to_v2(room) for room in rooms]
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, version=2
        )

    return True


def _room_to_v2(room: dict[str, Any]) -> dict[str, Any]:
    """One stored room, scalar head to list of heads."""
    migrated = {**room}
    single = migrated.pop(CONF_CLIMATE_ENTITY, None)
    if not migrated.get(CONF_CLIMATE_ENTITIES):
        migrated[CONF_CLIMATE_ENTITIES] = [single] if single else []
    return migrated


async def async_setup_entry(hass: HomeAssistant, entry: HvacConfigEntry) -> bool:
    """Set up from a config entry."""
    store = ModelStore(hass)
    await store.async_load()

    coordinator = HvacCoordinator(hass, entry, store)
    await coordinator.async_prepare()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HvacConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: HvacConfigEntry) -> None:
    """Drop learned state when the entry is removed."""
    await ModelStore(hass).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: HvacConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a room device once the room is no longer configured.

    runtime_data only exists while the entry is loaded. Home Assistant can call
    this with the entry unloaded, so fall back to the stored configuration
    rather than raising AttributeError.
    """
    if (coordinator := getattr(entry, "runtime_data", None)) is not None:
        configured = set(coordinator.rooms)
    else:
        configured = {
            room[CONF_ROOM_ID]
            for room in entry.options.get(
                CONF_ROOMS, entry.data.get(CONF_ROOMS, [])
            )
            if CONF_ROOM_ID in room
        }
    return not any(
        identifier[1] in configured
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN
    )


async def _async_reload_entry(hass: HomeAssistant, entry: HvacConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
