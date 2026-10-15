"""Button platform.

One per room: **Leave** (0.9.7, DR-088).

Pressing it says the owner has left the room. The room is empty from that
moment, its air conditioning is turned off at once, and it stays off - no
precool, no warnings - until someone comes back: the presence sensor sees the
room clear and then sees a person again. A room with no presence sensor stays
off until the owner turns it back on, by switching Automatic control, choosing
a Comfort mode or asking for it with a heading-home request.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HvacConfigEntry
from .coordinator import HvacCoordinator
from .entity import HvacRoomEntity
from .models import RoomConfig

# Pressing hands the request to the coordinator, which serialises actuation.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HvacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up each room's Leave button, and add more as rooms are configured."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_rooms() -> None:
        new = [
            RoomLeaveButton(coordinator, room)
            for room_id, room in coordinator.rooms.items()
            if room_id not in known
        ]
        known.update(coordinator.rooms)
        if new:
            async_add_entities(new)

    _add_new_rooms()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_rooms))


class RoomLeaveButton(HvacRoomEntity, ButtonEntity):
    """The owner has left the room: turn it off until someone comes back."""

    _attr_translation_key = "leave"

    def __init__(self, coordinator: HvacCoordinator, room: RoomConfig) -> None:
        """Initialize the button."""
        super().__init__(coordinator, room)
        self._attr_unique_id = f"{room.room_id}_leave"

    @property
    def available(self) -> bool:
        """Usable whether or not the room has been evaluated yet."""
        return self.coordinator.last_update_success

    async def async_press(self) -> None:
        """Leave the room."""
        await self.coordinator.async_leave_room(self._room_id)
