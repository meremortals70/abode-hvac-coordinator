"""Base entity.

Every entity this integration creates belongs to a room, and every room is a
device in the registry. Grouping matters here: a user thinks in rooms, not in
individual sensors.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HvacCoordinator
from .models import DecisionTrace, RoomConfig


class HvacRoomEntity(CoordinatorEntity[HvacCoordinator]):
    """Base for every entity belonging to a room."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HvacCoordinator, room: RoomConfig) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._room_id = room.room_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room.room_id)},
            name=room.name,
            manufacturer="Abode",
            model="Room",
            # A room is a logical grouping, not a physical device.
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def trace(self) -> DecisionTrace | None:
        """The most recent decision for this room."""
        return self.coordinator.data.get(self._room_id) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        """A room with no evaluation yet has nothing to report."""
        return super().available and self.trace is not None
