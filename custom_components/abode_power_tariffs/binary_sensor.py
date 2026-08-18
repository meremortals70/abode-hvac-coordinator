"""One binary sensor per declared constraint.

These are what battery, hot water and EV automations trigger on, instead of a
clock comparison that has to be edited whenever the plan moves.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TariffConfigEntry
from .coordinator import TariffCoordinator
from .entity import TariffEntity
from .plan import format_time

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TariffConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one binary sensor for each constraint declared in the plan."""
    coordinator = entry.runtime_data
    async_add_entities(
        ConstraintBinarySensor(coordinator, constraint)
        for constraint in coordinator.plan.constraints
    )


class ConstraintBinarySensor(TariffEntity, BinarySensorEntity):
    """On while a rate carrying this constraint is in force."""

    def __init__(self, coordinator: TariffCoordinator, constraint: str) -> None:
        """Initialise the constraint sensor."""
        super().__init__(coordinator, f"constraint_{_slug(constraint)}")
        self._constraint = constraint
        # Constraints are user-named, so there is no translation for them.
        self._attr_translation_key = None
        self._attr_name = constraint.replace("_", " ").capitalize()

    @property
    def is_on(self) -> bool:
        """Return whether the constraint applies right now."""
        rate = self.coordinator.state.effective_rate
        return rate is not None and self._constraint in rate.constraints

    @property
    def enforceable(self) -> bool:
        """Return whether the rate in force declares this rule enforceable.

        A declaration about what the rate means, not an instruction. Whether
        anything acts on it is the consuming system's decision.
        """
        rate = self.coordinator.state.effective_rate
        return rate is not None and self._constraint in rate.enforceable_constraints

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the period this constraint is currently attached to."""
        resolution = self.coordinator.state.resolution
        if resolution is None or not self.is_on:
            return {"constraint": self._constraint, "enforceable": self.enforceable}
        return {
            "constraint": self._constraint,
            "enforceable": self.enforceable,
            "rate": resolution.rate.qualified_name,
            "period_start": format_time(resolution.period.start),
            "period_end": format_time(resolution.period.end),
        }


def _slug(value: str) -> str:
    """Return a value safe to use in a unique id."""
    return "".join(
        character if character.isalnum() else "_" for character in value.lower()
    )
