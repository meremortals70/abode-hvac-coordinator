"""Sensors for Abode Power Tariffs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TariffConfigEntry
from .const import (
    ATTR_ALLOWANCE_SLOT,
    DEFAULT_HOURS,
    DEFAULT_RESOLUTION_MINUTES,
)
from .coordinator import TariffCoordinator
from .entity import TariffEntity
from .plan import format_time

# Nothing is polled and nothing is written on update, so there is no reason to
# serialise entity work.
PARALLEL_UPDATES = 0

# The Energy dashboard matches a price entity by its unit, which must be the
# configured currency over kWh. There is no device class for a unit price.
ENERGY_PRICE_SUFFIX = "/kWh"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TariffConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors for one tariff channel."""
    coordinator = entry.runtime_data
    currency = hass.config.currency

    entities: list[SensorEntity] = [
        ImportPriceSensor(coordinator, currency),
        ExportPriceSensor(coordinator, currency),
        RateSensor(coordinator),
        NextRateChangeSensor(coordinator),
        SupplyChargeSensor(coordinator, currency),
    ]

    # Only when the feed-in price actually moves. A plan on one export price
    # all day would get an entity that never has anything to say.
    if coordinator.plan.has_export_periods:
        entities.append(NextExportChangeSensor(coordinator))

    # Only when the user asked for it. Without counting there is nothing to
    # report, and an entity that never has a value is clutter.
    if coordinator.counting_allowance and any(
        rate.has_allowance for rate in coordinator.plan.rates
    ):
        entities.append(AllowanceRemainingSensor(coordinator))

    async_add_entities(entities)


class _PriceSensor(TariffEntity, SensorEntity):
    """Common behaviour for the two price sensors."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    # The forecast is a 24-hour prediction, roughly 4 KB of it, rebuilt on the
    # half hour. Keeping a history of predictions has no value and writing one
    # every time the entity updates is measured in tens of megabytes a day.
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(self, coordinator: TariffCoordinator, key: str, currency: str) -> None:
        """Initialise a price sensor with the configured currency."""
        super().__init__(coordinator, key)
        self._attr_native_unit_of_measurement = f"{currency}{ENERGY_PRICE_SUFFIX}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the period, the rules in force, and the forward series."""
        state = self.coordinator.state
        resolution = state.resolution
        rate = state.effective_rate

        attributes: dict[str, Any] = {
            "rate": rate.qualified_name if rate else None,
            "rate_name": rate.name if rate else None,
            "day_pattern": resolution.day_pattern.name if resolution else None,
            "season": (
                resolution.day_pattern.name
                if resolution and resolution.day_pattern.is_seasonal
                else None
            ),
            "period_start": format_time(resolution.period.start)
            if resolution
            else None,
            "period_end": format_time(resolution.period.end) if resolution else None,
            "constraints": sorted(rate.constraints) if rate else [],
            "enforceable_constraints": (
                sorted(rate.enforceable_constraints) if rate else []
            ),
            "coasting_permitted": rate.coasting_permitted if rate else None,
            "allowance_exhausted": state.allowance_exhausted,
            # Says plainly whether this price accounts for a cap being spent.
            "allowance_counted": self.coordinator.counting_allowance,
            "plan_expired": state.plan_expired,
            "trace": list(state.trace),
        }

        # evcc reads a list of {start, end, value} from a named attribute.
        series = self.coordinator.forward_intervals(
            DEFAULT_HOURS, DEFAULT_RESOLUTION_MINUTES
        )
        attributes["forecast"] = [interval.as_evcc_entry() for interval in series]
        return attributes


class ImportPriceSensor(_PriceSensor):
    """The price of imported energy right now."""

    def __init__(self, coordinator: TariffCoordinator, currency: str) -> None:
        """Initialise the import price sensor."""
        super().__init__(coordinator, "import_price", currency)

    @property
    def native_value(self) -> float | None:
        """Return dollars per kWh."""
        rate = self.coordinator.state.effective_rate
        return None if rate is None else round(rate.import_price, 6)


class ExportPriceSensor(_PriceSensor):
    """The price paid for exported energy right now."""

    def __init__(self, coordinator: TariffCoordinator, currency: str) -> None:
        """Initialise the export price sensor."""
        super().__init__(coordinator, "export_price", currency)

    @property
    def native_value(self) -> float | None:
        """Return dollars per kWh."""
        return round(self.coordinator.export_price_now(), 6)

    @property
    def available(self) -> bool:
        """A flat export price does not depend on an import period resolving."""
        return True


class RateSensor(TariffEntity, SensorEntity):
    """The name of the rate in force."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: TariffCoordinator) -> None:
        """Initialise the rate sensor with the plan's rate names as options."""
        super().__init__(coordinator, "rate")
        self._attr_options = list(coordinator.plan.qualified_rate_names)

    @property
    def native_value(self) -> str | None:
        """Return the rate name."""
        rate = self.coordinator.state.effective_rate
        return None if rate is None else rate.qualified_name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the scheduled rate, which differs when an allowance is spent."""
        resolution = self.coordinator.state.resolution
        return {
            "scheduled_rate": (resolution.rate.qualified_name if resolution else None),
            "rate_name": resolution.rate.name if resolution else None,
            "timetable": resolution.rate.timetable if resolution else None,
            "allowance_exhausted": self.coordinator.state.allowance_exhausted,
        }


class NextRateChangeSensor(TariffEntity, SensorEntity):
    """When the rate next changes."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: TariffCoordinator) -> None:
        """Initialise the next rate change sensor."""
        super().__init__(coordinator, "next_rate_change")

    @property
    def native_value(self) -> datetime | None:
        """Return the next boundary."""
        return self.coordinator.state.next_change


class NextExportChangeSensor(TariffEntity, SensorEntity):
    """When the feed-in price next changes.

    Separate from the rate change, because they are separate facts. A battery
    weighing a good export now against a cheaper import later needs both, and
    an automation should be able to trigger on either without the other.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: TariffCoordinator) -> None:
        """Initialise the next feed-in change sensor."""
        super().__init__(coordinator, "next_export_change")

    @property
    def native_value(self) -> datetime | None:
        """Return the next feed-in boundary."""
        return self.coordinator.state.next_export_change

    @property
    def available(self) -> bool:
        """The feed-in schedule does not depend on an import period resolving."""
        return True


class SupplyChargeSensor(TariffEntity, SensorEntity):
    """The daily supply charge, as configured."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    def __init__(self, coordinator: TariffCoordinator, currency: str) -> None:
        """Initialise the daily supply charge sensor."""
        super().__init__(coordinator, "daily_supply_charge")
        self._attr_native_unit_of_measurement = f"{currency}/d"

    @property
    def native_value(self) -> float:
        """Return dollars per day."""
        return round(self.coordinator.plan.daily_supply_charge, 6)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the fixed charges the plan declares.

        Declared figures, published as entered. Nothing here is accumulated and
        no cycle is computed from the billing day: where a cycle begins and
        ends, and how much of it is left, is the consumer's arithmetic.
        """
        plan = self.coordinator.plan
        return {
            "monthly_charge": round(plan.monthly_charge, 6),
            "billing_cycle_day": plan.billing_cycle_day,
        }

    @property
    def available(self) -> bool:
        """The supply charge does not depend on a period resolving."""
        return True


class AllowanceRemainingSensor(TariffEntity, RestoreSensor):
    """How much of this slot's energy allowance is left.

    Restored across a restart: an accumulator that resets on restart reads high
    and says nothing about it. The restore is qualified by the slot occurrence
    the figure was recorded in — a count is only still true if the component
    comes back inside the same one. Coming back into a different slot, or the
    same slot on another day, starts from zero.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: TariffCoordinator) -> None:
        """Initialise the allowance sensor."""
        super().__init__(coordinator, "allowance_remaining")

    async def async_added_to_hass(self) -> None:
        """Restore this slot's usage before subscribing."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is None or last.native_value is None:
            return
        resolution = self.coordinator.state.resolution
        if resolution is None or resolution.rate.rate_allowance_kwh is None:
            return
        restored_slot = None
        if (state := await self.async_get_last_state()) is not None:
            restored_slot = state.attributes.get(ATTR_ALLOWANCE_SLOT)
        if restored_slot != self.coordinator.state.allowance_slot:
            # A different slot occurrence. The figure belonged to that one and
            # says nothing about this; the allowance is the slot's, not the day's.
            return
        try:
            remaining = float(last.native_value)
        except (TypeError, ValueError):
            return
        self.coordinator.state.allowance_used_kwh = max(
            0.0, resolution.rate.rate_allowance_kwh - remaining
        )

    @property
    def native_value(self) -> float | None:
        """Return kWh remaining."""
        return self.coordinator.state.allowance_remaining_kwh

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Publish the slot the count belongs to, so a restore can check it."""
        return {ATTR_ALLOWANCE_SLOT: self.coordinator.state.allowance_slot}

    @property
    def available(self) -> bool:
        """Available only while a rate carrying an allowance is in force."""
        return self.coordinator.state.allowance_remaining_kwh is not None
