"""Base entity.

Every entity this integration creates belongs to a room, and every room is a
device in the registry. Grouping matters here: a user thinks in rooms, not in
individual sensors.
"""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
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


class HvacRoomControl(HvacRoomEntity):
    """Base for the controls built from a room's stored capability profile.

    DR-049. Every control shows the live state of the room's units, and a
    change made from it goes to every head in the room. Whether a change is
    accepted depends on the room's two switches: Automatic control, and for
    the vanes Automatic vane control. A change that is not accepted is refused
    with a translated reason, and the control goes on showing the unit's real
    state. A native entity cannot be greyed out and still show its value, so
    the grey appearance is a dashboard card keyed to the switches.
    """

    #: Whether this control is a vane. Vanes have a switch of their own.
    _is_vane = False

    @property
    def _room(self) -> RoomConfig | None:
        return self.coordinator.rooms.get(self._room_id)

    @property
    def _head_state(self) -> State | None:
        """The first head's live state, or None while it is not reporting."""
        room = self._room
        if room is None or not room.climate_entity_ids:
            return None
        state = self.hass.states.get(room.climate_entity_ids[0])
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state

    async def async_added_to_hass(self) -> None:
        """Follow the room's units directly.

        The coordinator re-evaluates on its own schedule and a change at the
        wall must show at once, not on the next cycle. A control that lags the
        unit it claims to show is worse than no control.
        """
        await super().async_added_to_hass()
        room = self._room
        if room is not None and room.climate_entity_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, list(room.climate_entity_ids), self._unit_changed
                )
            )

    @callback
    def _unit_changed(self, event: Event[EventStateChangedData]) -> None:
        """One of the room's units reported a new state."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available while the unit is reporting, whatever the room is doing."""
        return self._head_state is not None

    def _require_writable(self) -> RoomConfig:
        """The room, or a translated refusal if this control cannot be changed now."""
        room = self._room
        if room is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_room",
                translation_placeholders={"room_id": self._room_id},
            )
        if not self.coordinator.controls_writable(self._room_id, vane=self._is_vane):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=(
                    "vane_not_writable" if self._is_vane else "control_not_writable"
                ),
                translation_placeholders={"room": room.name},
            )
        return room
