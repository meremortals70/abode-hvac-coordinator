"""The Abode HVAC Coordinator: setting up and taking down (Section 8).

Each room is learned as it is run: a model of the room is learned from its
own readings, and every half-hour of the next six is planned on that model
to keep the room near the centre of its comfort band for the least cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import probatio
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    ServiceValidationError,
    UnsupportedStorageVersionError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .comfort import UserAction
from .const import (
    ATTR_READY_BY,
    ATTR_ROOM_ID,
    DOMAIN,
    SERVICE_CANCEL_PRECOOL,
    SERVICE_PRECOOL,
)
from .coordinator import AbodeHvacCoordinator
from .entity import register_house_device
from .forms import ENTRY_MINOR_VERSION, ENTRY_VERSION, migrate_entry

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PRECOOL_SCHEMA = probatio.Schema(
    {probatio.Required(ATTR_ROOM_ID): cv.string, probatio.Optional(ATTR_READY_BY): cv.datetime}
)
CANCEL_PRECOOL_SCHEMA = probatio.Schema({probatio.Required(ATTR_ROOM_ID): cv.string})


@dataclass(slots=True)
class AbodeHvacRuntime:
    """What a loaded entry holds: the coordinator, which holds Memory [R18]."""

    coordinator: AbodeHvacCoordinator


type AbodeHvacConfigEntry = ConfigEntry[AbodeHvacRuntime]


def _coordinator_for(hass: HomeAssistant, room_id: str) -> AbodeHvacCoordinator:
    """The loaded coordinator that runs this room, or a translated refusal
    [R18]."""
    entries: list[AbodeHvacConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    for entry in entries:
        coordinator = entry.runtime_data.coordinator
        if coordinator.has_room(room_id):
            return coordinator
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_room",
                                 translation_placeholders={"room_id": room_id})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions precool and cancel_precool, once for the
    integration, not per entry [R18]."""

    async def precool(call: ServiceCall) -> None:
        room_id: str = call.data[ATTR_ROOM_ID]
        coordinator = _coordinator_for(hass, room_id)
        run = coordinator.rooms[room_id]
        if run.config.presence_sensor is None:  # [I8]
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="precool_needs_presence",
                                         translation_placeholders={"room": run.config.name})
        ready_by: datetime | None = call.data.get(ATTR_READY_BY)
        when = dt_util.as_utc(ready_by) if ready_by is not None else dt_util.utcnow()
        await coordinator.async_set_choices(room_id, UserAction.PRECOOL, precool_ready_by=when)

    async def cancel_precool(call: ServiceCall) -> None:
        room_id: str = call.data[ATTR_ROOM_ID]
        coordinator = _coordinator_for(hass, room_id)
        await coordinator.async_set_choices(room_id, UserAction.OTHER, precool_ready_by=None)

    hass.services.async_register(DOMAIN, SERVICE_PRECOOL, precool, schema=PRECOOL_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_PRECOOL, cancel_precool, schema=CANCEL_PRECOOL_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AbodeHvacConfigEntry) -> bool:
    """Start Memory and the coordinator; run the first cycle before any
    entity exists; then the platforms [R18][D049]."""
    coordinator = AbodeHvacCoordinator(hass, entry)
    try:
        await coordinator.async_start()
    except UnsupportedStorageVersionError as err:
        raise ConfigEntryNotReady(translation_domain=DOMAIN, translation_key="memory_unreadable",
                                  translation_placeholders={"error": str(err)}) from err
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = AbodeHvacRuntime(coordinator)
    register_house_device(hass, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_changed))
    coordinator.start_seeding()
    return True


async def _async_options_changed(hass: HomeAssistant, entry: AbodeHvacConfigEntry) -> None:
    """Rooms added, changed or removed in place, without a reload [R18].
    The house's data changes only through Reconfigure; then the entry is
    reloaded."""
    coordinator = entry.runtime_data.coordinator
    if dict(entry.data) != coordinator.house_data:
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    await coordinator.async_rooms_changed()


async def async_unload_entry(hass: HomeAssistant, entry: AbodeHvacConfigEntry) -> bool:
    """Unload the platforms; the timers and listeners go with the entry;
    Memory writes everything [D064][R18]."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = entry.runtime_data.coordinator
        await coordinator.async_shutdown_rooms()
        coordinator.clear_repairs()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: AbodeHvacConfigEntry) -> None:
    """The integration removed: Memory's file goes too [Prop]."""
    from .memory import Memory

    await Memory(hass, dict).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AbodeHvacConfigEntry, device: dr.DeviceEntry
) -> bool:
    """True only for a room no longer configured, so a leftover device can
    be deleted [R18]."""
    coordinator = entry.runtime_data.coordinator
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        if identifier == entry.entry_id or coordinator.has_room(identifier):
            return False
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: AbodeHvacConfigEntry) -> bool:
    """Convert an older configuration; never refuse to load [Set]."""
    if (entry.version, entry.minor_version) == (ENTRY_VERSION, ENTRY_MINOR_VERSION):
        return True
    data, options = migrate_entry(entry.version, entry.minor_version, entry.data, entry.options)
    hass.config_entries.async_update_entry(entry, data=data, options=options, version=ENTRY_VERSION,
                                           minor_version=ENTRY_MINOR_VERSION)
    return True
