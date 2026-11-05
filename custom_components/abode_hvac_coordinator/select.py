"""Per room: Comfort mode, and the unit's mode, fan speed and vanes
(Section 7) [D058][D049].

Comfort mode is always writable and stands until the user changes it. The
unit's controls show its live value; they are writable only while
Automatic control (for a vane, Automatic vane control) is off, and offered
only where the unit advertised the choice [D048].
"""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AbodeHvacConfigEntry
from .act import UserCommand
from .comfort import UserAction
from .coordinator import AbodeHvacCoordinator
from .entity import RoomControlEntity, RoomEntity, add_room_entities
from .models import ComfortMode

PARALLEL_UPDATES = 1

# the unit's attribute each control shows (climate/const.py, 2026.10.3) [HA]
_LIVE = {
    "fan_speed": "fan_mode",
    "vertical_vane": "swing_mode",
    "horizontal_vane": "swing_horizontal_mode",
}


class ComfortModeSelect(RoomEntity, SelectEntity):
    """Schedule, Sleep or Awake [D058][D083]."""

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "comfort_mode")
        self._attr_options = [m.value for m in ComfortMode]

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.run is not None

    @property
    def current_option(self) -> str:
        return self.room.state.choices.comfort_mode.value

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_choices(self.room_id, UserAction.COMFORT_MODE,
                                                 comfort_mode=ComfortMode(option))


class UnitSelect(RoomControlEntity, SelectEntity):
    """One of the unit's own controls, as the first head reports it."""

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str, key: str, options: tuple[str, ...]) -> None:
        super().__init__(coordinator, room_id, key)
        self._key = key
        self._attr_options = list(options)

    @property
    def current_option(self) -> str | None:
        st = self.unit_state
        value = st.state if self._key == "unit_mode" else st.attributes.get(_LIVE[self._key])
        return str(value) if value is not None and str(value) in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        cmd = {
            "unit_mode": UserCommand(hvac_mode=option),
            "fan_speed": UserCommand(fan_mode=option),
            "vertical_vane": UserCommand(vertical_vane=option),
            "horizontal_vane": UserCommand(horizontal_vane=option),
        }[self._key]
        await self.coordinator.async_user_command(self.room_id, cmd, self._context)


def _room_entities(coordinator: AbodeHvacCoordinator, room_id: str) -> Iterable[SelectEntity]:
    yield ComfortModeSelect(coordinator, room_id)
    profile = coordinator.rooms[room_id].config.profile
    if profile is None:
        return
    for key, options in (
        ("unit_mode", profile.hvac_modes),
        ("fan_speed", profile.fan_modes),
        ("vertical_vane", profile.vertical_vanes),
        ("horizontal_vane", profile.horizontal_vanes),
    ):
        if options:
            yield UnitSelect(coordinator, room_id, key, options)


async def async_setup_entry(
    hass: HomeAssistant, entry: AbodeHvacConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_room_entities(hass, entry.runtime_data.coordinator, async_add_entities, _room_entities)
