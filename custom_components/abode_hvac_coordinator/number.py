"""Number platform: the room's setpoint.

DR-049. Shows the live setpoint of the room's units and, while Automatic
control is off, sends a new one to every unit in the room. While it is on, the
coordinator owns the setpoint and the control refuses changes. The range and
step are what the room's units offered at setup (DR-048).
"""

from __future__ import annotations

from homeassistant.components.climate.const import (
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
)
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HvacConfigEntry
from .coordinator import HvacCoordinator
from .entity import HvacRoomControl
from .models import RoomConfig

PARALLEL_UPDATES = 1

#: The step offered where the units advertised none.
_DEFAULT_STEP = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HvacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the setpoint control, and add more as rooms and profiles appear."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_controls() -> None:
        new = [
            RoomSetpoint(coordinator, room)
            for room_id, room in coordinator.rooms.items()
            if room_id not in known
            and room.capabilities is not None
            and room.capabilities.has_setpoint
        ]
        known.update(control.room_id for control in new)
        if new:
            async_add_entities(new)

    _add_new_controls()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_controls))


class RoomSetpoint(HvacRoomControl, NumberEntity):
    """The temperature the room's units are set to."""

    _attr_translation_key = "setpoint"
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: HvacCoordinator, room: RoomConfig) -> None:
        """Initialize the control."""
        super().__init__(coordinator, room)
        self._attr_unique_id = f"{room.room_id}_setpoint"

    @property
    def room_id(self) -> str:
        """The room this control belongs to."""
        return self._room_id

    @property
    def native_min_value(self) -> float:
        """The lowest setpoint the room's units offered."""
        room = self._room
        value = None if room is None or room.capabilities is None else room.capabilities.min_temp
        return DEFAULT_MIN_TEMP if value is None else value

    @property
    def native_max_value(self) -> float:
        """The highest setpoint the room's units offered."""
        room = self._room
        value = None if room is None or room.capabilities is None else room.capabilities.max_temp
        return DEFAULT_MAX_TEMP if value is None else value

    @property
    def native_step(self) -> float:
        """The setpoint step the room's units offered."""
        room = self._room
        value = (
            None
            if room is None or room.capabilities is None
            else room.capabilities.target_temp_step
        )
        return _DEFAULT_STEP if value is None else value

    @property
    def native_value(self) -> float | None:
        """The live setpoint of the room's first unit."""
        state = self._head_state
        if state is None:
            return None
        try:
            value = state.attributes.get(ATTR_TEMPERATURE)
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Send a new setpoint to every unit in the room, if it may be changed now."""
        room = self._require_writable()
        await self.coordinator.actuator.async_user_command(room, temperature=value)
        await self.coordinator.async_request_refresh()
