"""The base for every entity (Section 8, entity.py) [R18].

A room's entities belong to the room's device and a house's to the house's.
Each is a CoordinatorEntity, so it is unavailable whenever the last cycle
failed; a room's entities are also unavailable while that room's own cycle
failed. Names come from strings.json by translation key.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    SIGNAL_ROOM_ADDED,
    SIGNAL_ROOM_EDITED,
    SIGNAL_ROOM_REMOVED,
    AbodeHvacCoordinator,
    HouseView,
    RoomRun,
    RoomView,
)

HOUSE_NAME = "Abode HVAC Coordinator"
MANUFACTURER = "Abode"
ROOM_MODEL = "Room coordinator"


def register_house_device(hass: HomeAssistant, coordinator: AbodeHvacCoordinator) -> None:
    """The house's service device, registered before any room's, so each
    room's device can name it as the device it is reached through."""
    entry_id = coordinator.config_entry.entry_id
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, entry_id)},
        name=HOUSE_NAME,
        manufacturer=MANUFACTURER,
        entry_type=DeviceEntryType.SERVICE,
        sw_version=coordinator.version,
    )
    coordinator.house_device_id = device.id


class RoomEntity(CoordinatorEntity[AbodeHvacCoordinator]):
    """An entity of one room."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.room_id = room_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{room_id}_{key}"
        run = coordinator.rooms[room_id]
        # The house's device is registered before any platform (__init__.py), so it
        # is always known here. via_device_id is given as a keyword: DeviceInfo is
        # a dataclass from 2026.11 and a TypedDict before it, and keyword
        # construction is the form both take (helpers/device_registry.py,
        # 2026.11.0b0) [HA]
        assert coordinator.house_device_id is not None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room_id)},
            name=run.config.name,
            manufacturer=MANUFACTURER,
            model=ROOM_MODEL,
            sw_version=coordinator.version,
            via_device_id=coordinator.house_device_id,
        )

    @property
    def run(self) -> RoomRun | None:
        """The room, or None once it has been removed."""
        return self.coordinator.rooms.get(self.room_id)

    @property
    def room(self) -> RoomRun:
        """The room. Read only while the entity is available, which needs
        the room, or while it is being added."""
        run = self.run
        assert run is not None
        return run

    @property
    def view(self) -> RoomView | None:
        data = self.coordinator.data
        return data.rooms.get(self.room_id) if data is not None else None

    @property
    def available(self) -> bool:
        view = self.view
        return super().available and self.run is not None and view is not None and view.ok


class RoomControlEntity(RoomEntity):
    """A room control that shows its unit's live value: it follows the
    unit's state changes from async_added_to_hass, and stops on removal
    [R18][D049]."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        heads = [h.climate for h in self.room.config.heads]
        self.async_on_remove(async_track_state_change_event(self.hass, heads, self._unit_changed))

    @callback
    def _unit_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """The coordinator's update and the room's first unit reporting."""
        if not self.coordinator.last_update_success or self.run is None:
            return False
        st = self.hass.states.get(self.room.config.heads[0].climate)
        return st is not None and st.state not in ("unavailable", "unknown")

    @property
    def unit_state(self) -> State:
        """The first head's live state; read only while available, which
        needs it."""
        st = self.hass.states.get(self.room.config.heads[0].climate)
        assert st is not None
        return st


class HouseEntity(CoordinatorEntity[AbodeHvacCoordinator]):
    """An entity of the house, under its service device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AbodeHvacCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=HOUSE_NAME,
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
            sw_version=coordinator.version,
        )

    @property
    def house(self) -> HouseView | None:
        data = self.coordinator.data
        return data.house if data is not None else None


def add_room_entities(
    hass: HomeAssistant,
    coordinator: AbodeHvacCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    make: Callable[[AbodeHvacCoordinator, str], Iterable[Entity]],
) -> None:
    """Add every room's entities now, a new room's when the coordinator
    signals it, and an edited room's again, without a reload [R18].

    An edit can change which entities a room has (a Precool button needs a
    presence sensor; a vane control needs a unit that offers vanes) and
    which units they follow. So an edited room's entities on this platform
    are taken out and made again from the new settings. Their registry
    entries stay, so names, areas and enabled choices are kept; the entry
    of an entity the room no longer has is removed [D098]."""
    domain = entity_platform.async_get_current_platform().domain
    made: dict[str, list[Entity]] = {}

    def make_room(room_id: str) -> list[Entity]:
        entities = list(make(coordinator, room_id))
        made[room_id] = entities
        return entities

    async_add_entities([e for rid in list(coordinator.rooms) for e in make_room(rid)])

    @callback
    def added(room_id: str) -> None:
        if room_id in coordinator.rooms:
            async_add_entities(make_room(room_id))

    async def edited(room_id: str) -> None:
        old = made.pop(room_id, [])
        for e in old:
            if e.platform is not None:  # added; a disabled entity never is
                await e.async_remove(force_remove=True)
        new = make_room(room_id) if room_id in coordinator.rooms else []
        registry = er.async_get(hass)
        for unique_id in {u for e in old if (u := e.unique_id)} - {e.unique_id for e in new}:
            entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
            if entity_id is not None:
                registry.async_remove(entity_id)
        async_add_entities(new)

    @callback
    def removed(room_id: str) -> None:
        made.pop(room_id, None)

    entry = coordinator.config_entry
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_ROOM_ADDED, added))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_ROOM_EDITED, edited))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_ROOM_REMOVED, removed))

