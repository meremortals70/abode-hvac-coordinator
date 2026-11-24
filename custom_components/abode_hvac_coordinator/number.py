"""Per room: the unit's setpoint (Section 7) [D049].

It shows the unit's live setpoint, and is writable only while Automatic
control is off; its range and step are the unit's [D048].
"""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AbodeHvacConfigEntry
from .act import UserCommand
from .capabilities import TO_C
from .const import NO_STEP_ADVERTISED
from .coordinator import AbodeHvacCoordinator
from .entity import RoomControlEntity, add_room_entities

PARALLEL_UPDATES = 1


class SetpointNumber(RoomControlEntity, NumberEntity):
    """The unit's setpoint, in C; Home Assistant shows it in the house's
    unit."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "setpoint")
        profile = coordinator.rooms[room_id].config.profile
        assert profile is not None
        self._attr_native_min_value = profile.min_setpoint
        self._attr_native_max_value = profile.max_setpoint
        self._attr_native_step = profile.setpoint_step or NO_STEP_ADVERTISED

    @property
    def native_value(self) -> float | None:
        st = self.unit_state
        to_c = TO_C.get(self.hass.config.units.temperature_unit)
        value = st.attributes.get("temperature")
        if value is None:
            lo, hi = st.attributes.get("target_temp_low"), st.attributes.get("target_temp_high")
            if not isinstance(lo, int | float) or not isinstance(hi, int | float):
                return None
            value = (lo + hi) / 2.0  # a unit that holds a range: the middle [D081]
        if not isinstance(value, int | float) or to_c is None:
            return None
        return round(to_c(float(value)), 2)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_user_command(self.room_id, UserCommand(setpoint=value), self._context)


def _room_entities(coordinator: AbodeHvacCoordinator, room_id: str) -> Iterable[NumberEntity]:
    if coordinator.rooms[room_id].config.profile is not None:
        yield SetpointNumber(coordinator, room_id)


async def async_setup_entry(
    hass: HomeAssistant, entry: AbodeHvacConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_room_entities(hass, entry.runtime_data.coordinator, async_add_entities, _room_entities)
