"""Switch platform.

One per room: Automatic control. On is the normal state. Off holds the room's
unit off and keeps the coordinator from starting it, until the switch is turned
back on. DR-047.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HvacConfigEntry
from .coordinator import HvacCoordinator
from .entity import HvacRoomEntity
from .models import RoomConfig

# Turning the switch hands the change to the coordinator, which serialises
# actuation itself.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HvacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the room switches, and add more as rooms are configured."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_rooms() -> None:
        new = [
            RoomAutomaticControlSwitch(coordinator, room)
            for room_id, room in coordinator.rooms.items()
            if room_id not in known
        ]
        known.update(coordinator.rooms)
        if new:
            async_add_entities(new)

    _add_new_rooms()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_rooms))


class RoomAutomaticControlSwitch(HvacRoomEntity, SwitchEntity):
    """Whether this integration controls the room's air conditioning."""

    _attr_translation_key = "automatic_control"

    def __init__(self, coordinator: HvacCoordinator, room: RoomConfig) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, room)
        self._attr_unique_id = f"{room.room_id}_automatic_control"

    @property
    def available(self) -> bool:
        """Usable whether or not the room has been evaluated yet.

        The room's own state is held by the coordinator, so the switch has
        something true to say before the first decision exists.
        """
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        """On while the coordinator is allowed to run this room."""
        return not self.coordinator.is_room_switched_off(self._room_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Hand the room back to the coordinator."""
        await self.coordinator.async_set_room_switched_off(self._room_id, False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hold the room's unit off."""
        await self.coordinator.async_set_room_switched_off(self._room_id, True)
