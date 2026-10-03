"""Select platform: the room's mode, fan speed and vane positions.

DR-049. Built from the room's stored capability profile (DR-048), so a control
exists only where the room's units offered the choice when the room was set
up, and offers exactly what they offered. Each shows the live state of the
room's units. While Automatic control is on, mode and fan speed refuse changes;
the vanes refuse them while Automatic vane control is on. Otherwise a change
goes to every head in the room.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.climate.const import (
    ATTR_FAN_MODE,
    ATTR_SWING_HORIZONTAL_MODE,
    ATTR_SWING_MODE,
)
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HvacConfigEntry
from .capabilities import RoomCapabilities
from .coordinator import HvacCoordinator
from .entity import HvacRoomControl
from .models import RoomConfig

# A change goes to the unit through the actuator, which awaits each service
# call. One at a time per platform keeps two quick taps from interleaving.
PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class _ControlSpec:
    """One select: what it is called, where its options and value come from."""

    key: str
    translation_key: str
    options: Callable[[RoomCapabilities], tuple[str, ...]]
    value: Callable[[State], str | None]
    command: str
    is_vane: bool = False


def _attribute(name: str) -> Callable[[State], str | None]:
    def read(state: State) -> str | None:
        value = state.attributes.get(name)
        return None if value is None else str(value)

    return read


_SPECS: tuple[_ControlSpec, ...] = (
    _ControlSpec(
        key="unit_mode",
        translation_key="unit_mode",
        options=lambda profile: profile.hvac_modes,
        value=lambda state: state.state,
        command="hvac_mode",
    ),
    _ControlSpec(
        key="fan_speed",
        translation_key="fan_speed",
        options=lambda profile: profile.fan_modes,
        value=_attribute(ATTR_FAN_MODE),
        command="fan_mode",
    ),
    _ControlSpec(
        key="vertical_vane",
        translation_key="vertical_vane",
        options=lambda profile: profile.swing_modes,
        value=_attribute(ATTR_SWING_MODE),
        command="swing_mode",
        is_vane=True,
    ),
    _ControlSpec(
        key="horizontal_vane",
        translation_key="horizontal_vane",
        options=lambda profile: profile.swing_horizontal_modes,
        value=_attribute(ATTR_SWING_HORIZONTAL_MODE),
        command="swing_horizontal_mode",
        is_vane=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HvacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the room controls, and add more as rooms and profiles appear."""
    coordinator = entry.runtime_data
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new_controls() -> None:
        new: list[RoomSelect] = []
        for room_id, room in coordinator.rooms.items():
            profile = room.capabilities
            if profile is None:
                continue
            for spec in _SPECS:
                if (room_id, spec.key) in known or not spec.options(profile):
                    continue
                known.add((room_id, spec.key))
                new.append(RoomSelect(coordinator, room, spec))
        if new:
            async_add_entities(new)

    _add_new_controls()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_controls))


class RoomSelect(HvacRoomControl, SelectEntity):
    """One of the room's mode, fan speed or vane controls."""

    def __init__(
        self, coordinator: HvacCoordinator, room: RoomConfig, spec: _ControlSpec
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator, room)
        self._spec = spec
        self._is_vane = spec.is_vane
        self._attr_translation_key = spec.translation_key
        self._attr_unique_id = f"{room.room_id}_{spec.key}"

    @property
    def options(self) -> list[str]:
        """What the room's units offered when the room was set up."""
        room = self._room
        if room is None or room.capabilities is None:
            return []
        return list(self._spec.options(room.capabilities))

    @property
    def current_option(self) -> str | None:
        """The live state of the room's first unit, where it is one of the options."""
        state = self._head_state
        if state is None:
            return None
        value = self._spec.value(state)
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Send the choice to every unit in the room, if it may be changed now."""
        room = self._require_writable()
        command = self._spec.command
        await self.coordinator.actuator.async_user_command(
            room,
            hvac_mode=option if command == "hvac_mode" else None,
            fan_mode=option if command == "fan_mode" else None,
            swing_mode=option if command == "swing_mode" else None,
            swing_horizontal_mode=(
                option if command == "swing_horizontal_mode" else None
            ),
        )
        await self.coordinator.async_request_refresh()
