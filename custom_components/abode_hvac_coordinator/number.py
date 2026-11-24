"""Per room: the unit's setpoint (Section 7) [D049].

It shows the unit's live setpoint, and is writable only while Automatic
control is off; its range and step are the unit's [D048].

A head that is resting — fan only, or off — reports no setpoint of its own, and
that left the control blank while the plan was busy holding the room at one.
While the component is in charge, and while a step still holds the room, the
control therefore shows the setpoint last sent; while the user is in charge, a
blank is the truth [D049].
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
from .models import StepKind

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
        to_c = TO_C.get(self.hass.config.units.temperature_unit)
        st = self.unit_state
        value = st.attributes.get("temperature")
        if value is None:
            lo, hi = st.attributes.get("target_temp_low"), st.attributes.get("target_temp_high")
            if isinstance(lo, int | float) and isinstance(hi, int | float):
                value = (lo + hi) / 2.0  # a unit that holds a range: the middle [D081]
        if isinstance(value, int | float):
            return round(to_c(float(value)), 2) if to_c is not None else None
        # The head reports no setpoint while it rests. The room still has one
        # while the component is holding it, so show the setpoint last sent
        # rather than a blank — but only while a step is actually holding the
        # room, because a setpoint once sent outlives the step that sent it
        # (Leave, an empty room, a door left open) [D049]
        run = self.run
        if run is None or run.failed or not run.state.choices.automatic_control:
            return None
        step = run.limits_result or run.step
        if step is None or step.kind in (StepKind.OFF, StepKind.NONE):
            return None
        held = run.state.commanded_c
        if held is None and run.comfort_result is not None:
            # nothing has been sent since this spell began, so the band's own
            # centre is the setpoint the plan is working to
            held = run.comfort_result.centre_c
        return None if held is None else round(held, 2)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_user_command(self.room_id, UserCommand(setpoint=value), self._context)


def _room_entities(coordinator: AbodeHvacCoordinator, room_id: str) -> Iterable[NumberEntity]:
    if coordinator.rooms[room_id].config.profile is not None:
        yield SetpointNumber(coordinator, room_id)


async def async_setup_entry(
    hass: HomeAssistant, entry: AbodeHvacConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    add_room_entities(hass, entry.runtime_data.coordinator, async_add_entities, _room_entities)
