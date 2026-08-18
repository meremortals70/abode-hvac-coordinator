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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TariffConfigEntry
from .const import (
    CONF_SUPPLY_CHARGE_ENTITIES,
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

    if coordinator.options.get(CONF_SUPPLY_CHARGE_ENTITIES):
        entities.append(SupplyChargeCostSensor(coordinator, currency))
        entities.append(SupplyChargeEnergySensor(coordinator))

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
    def available(self) -> bool:
        """The supply charge does not depend on a period resolving."""
        return True


class AllowanceRemainingSensor(TariffEntity, RestoreSensor):
    """How much of today's energy allowance is left.

    Restored across a restart: an accumulator that resets on restart reads high
    and says nothing about it.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: TariffCoordinator) -> None:
        """Initialise the allowance sensor."""
        super().__init__(coordinator, "allowance_remaining")

    async def async_added_to_hass(self) -> None:
        """Restore today's usage before subscribing."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is None or last.native_value is None:
            return
        rate = self.coordinator.state.resolution
        if rate is None or rate.rate.daily_allowance_kwh is None:
            return
        try:
            remaining = float(last.native_value)
        except (TypeError, ValueError):
            return
        self.coordinator.state.allowance_used_kwh = max(
            0.0, rate.rate.daily_allowance_kwh - remaining
        )

    @property
    def native_value(self) -> float | None:
        """Return kWh remaining."""
        return self.coordinator.state.allowance_remaining_kwh

    @property
    def available(self) -> bool:
        """Available only while a rate carrying an allowance is in force."""
        return self.coordinator.state.allowance_remaining_kwh is not None


class SupplyChargeCostSensor(TariffEntity, RestoreSensor):
    """An accumulating cost for the daily supply charge.

    The Energy dashboard has no field for a fixed daily charge. Paired with the
    matching energy sensor below and added as a second grid consumption source
    using "use an entity tracking the total costs", this puts the supply charge
    into the dashboard's figures.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: TariffCoordinator, currency: str) -> None:
        """Initialise the accumulating supply charge cost sensor."""
        super().__init__(coordinator, "supply_charge_today")
        self._attr_native_unit_of_measurement = currency

    @property
    def native_value(self) -> float:
        """Return today's supply charge so far."""
        return round(self.coordinator.state.supply_charge_today, 4)

    @property
    def available(self) -> bool:
        """Does not depend on a period resolving."""
        return True


class SupplyChargeEnergySensor(TariffEntity, SensorEntity):
    """A near-zero energy sensor the Energy dashboard can attach a cost to.

    The dashboard prices a consumption source, so a fixed charge needs a source
    to hang from. This one contributes a negligible amount of energy.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 6

    def __init__(self, coordinator: TariffCoordinator) -> None:
        """Initialise the token energy sensor the dashboard needs."""
        super().__init__(coordinator, "supply_charge_energy")
        self._counter = 0.0

    @callback
    def _handle_update(self) -> None:
        self._counter += 0.000001
        super()._handle_update()

    @property
    def native_value(self) -> float:
        """Return the token energy total."""
        return round(self._counter, 6)

    @property
    def available(self) -> bool:
        """Does not depend on a period resolving."""
        return True
