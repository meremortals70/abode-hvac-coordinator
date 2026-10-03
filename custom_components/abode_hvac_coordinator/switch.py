"""Switch platform.

Two per room (DR-049).

**Automatic control.** On is the normal state. Off stops the automation: the
coordinator sends nothing to the room and the unit is left exactly as it is,
running or not, with the room's controls writable.

**Automatic vane control.** On is the normal state: the coordinator positions
the vertical vane as it always has. Off, the coordinator never sends a vane
command to the room and the vane controls are the user's at any time. Whether
people like air flowing over them, or something in the room blocks a vane
direction, is not something an autonomous controller can know.
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
        new: list[SwitchEntity] = []
        for room_id, room in coordinator.rooms.items():
            if room_id in known:
                continue
            new.append(RoomAutomaticControlSwitch(coordinator, room))
            new.append(RoomAutomaticVaneControlSwitch(coordinator, room))
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
        """Stop the automation and leave the unit as it is."""
        await self.coordinator.async_set_room_switched_off(self._room_id, True)


class RoomAutomaticVaneControlSwitch(HvacRoomEntity, SwitchEntity):
    """Whether this integration positions the room's vertical vane."""

    _attr_translation_key = "automatic_vane_control"

    def __init__(self, coordinator: HvacCoordinator, room: RoomConfig) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, room)
        self._attr_unique_id = f"{room.room_id}_automatic_vane_control"

    @property
    def available(self) -> bool:
        """Usable whether or not the room has been evaluated yet."""
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        """On while the coordinator positions this room's vertical vane."""
        return not self.coordinator.is_room_vanes_manual(self._room_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Let the coordinator position the vane."""
        await self.coordinator.async_set_room_vanes_manual(self._room_id, False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Take the vanes into the user's hands."""
        await self.coordinator.async_set_room_vanes_manual(self._room_id, True)
