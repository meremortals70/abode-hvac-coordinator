"""Per room: Automatic control and Automatic vane control (Section 7) [D049]."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AbodeHvacConfigEntry
from .comfort import UserAction
from .coordinator import AbodeHvacCoordinator
from .entity import RoomEntity, add_room_entities

PARALLEL_UPDATES = 1


class AutomaticControlSwitch(RoomEntity, SwitchEntity):
    """On: the component runs the room. Off: the unit is sent off once,
    then the room's controls are the user's [D049][D089]."""

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "automatic_control")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.run is not None

    @property
    def is_on(self) -> bool:
        return self.room.state.choices.automatic_control

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_choices(self.room_id, UserAction.AUTOMATIC_CONTROL, automatic_control=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_choices(self.room_id, UserAction.AUTOMATIC_CONTROL, automatic_control=False)


class AutomaticVaneSwitch(RoomEntity, SwitchEntity):
    """On: the component positions the vertical vane. Off: the vanes are
    the user's at any time [D049]."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "automatic_vane_control")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.run is not None

    @property
    def is_on(self) -> bool:
        return self.room.state.choices.automatic_vane_control

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_choices(self.room_id, UserAction.OTHER, automatic_vane_control=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_choices(self.room_id, UserAction.OTHER, automatic_vane_control=False)


def _room_entities(coordinator: AbodeHvacCoordinator, room_id: str) -> Iterable[SwitchEntity]:
    yield AutomaticControlSwitch(coordinator, room_id)
    yield AutomaticVaneSwitch(coordinator, room_id)


async def async_setup_entry(
    hass: HomeAssistant, entry: AbodeHvacConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_room_entities(hass, entry.runtime_data.coordinator, async_add_entities, _room_entities)
