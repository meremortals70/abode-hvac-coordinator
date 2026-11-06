"""Per room: Leave, and Precool in rooms with a presence sensor (Section 7)
[D088][I8]."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AbodeHvacConfigEntry
from .comfort import UserAction
from .coordinator import AbodeHvacCoordinator
from .entity import RoomEntity, add_room_entities

PARALLEL_UPDATES = 1


class LeaveButton(RoomEntity, ButtonEntity):
    """Turns the room off at once until someone comes back, or in a room
    without a presence sensor until the user changes a choice [D088]."""

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "leave")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.run is not None

    async def async_press(self) -> None:
        await self.coordinator.async_set_choices(self.room_id, UserAction.OTHER, leave_pressed_at=dt_util.utcnow())


class PrecoolButton(RoomEntity, ButtonEntity):
    """Starts Precool now: the room runs to the centre of its precool band
    until someone comes in or it is ended [I8]."""

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "precool")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.run is not None

    async def async_press(self) -> None:
        await self.coordinator.async_set_choices(self.room_id, UserAction.PRECOOL, precool_ready_by=dt_util.utcnow())


def _room_entities(coordinator: AbodeHvacCoordinator, room_id: str) -> Iterable[ButtonEntity]:
    yield LeaveButton(coordinator, room_id)
    if coordinator.rooms[room_id].config.presence_sensor is not None:
        yield PrecoolButton(coordinator, room_id)


async def async_setup_entry(
    hass: HomeAssistant, entry: AbodeHvacConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_room_entities(hass, entry.runtime_data.coordinator, async_add_entities, _room_entities)
