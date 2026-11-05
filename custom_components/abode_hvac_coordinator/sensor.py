"""The room and house sensors (Section 7) [R18].

Each reads its value from the room's latest cycle or the house's figures;
a value whose reading is missing makes the sensor unavailable rather than
wrong [Prop].
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AbodeHvacConfigEntry
from .coordinator import AbodeHvacCoordinator, HouseView, RoomView, settings_dict
from .entity import HouseEntity, RoomEntity, add_room_entities
from .memory import profile_to_dict
from .models import Controller, RoomModeState

PARALLEL_UPDATES = 0

# The trace's large parts stay out of the recorder: about 2-6 KB every 30 s
_UNRECORDED = frozenset({"plan", "pid", "model", "power", "delivery", "limits", "compressors", "stale"})


@dataclass(frozen=True, kw_only=True)
class RoomSensorDescription(SensorEntityDescription):
    """A room sensor: its value and attributes from the room's view."""

    value: Callable[[RoomView], Any]
    attributes: Callable[[RoomView], Mapping[str, Any]] | None = None
    reading: bool = False  # shows a reading: unavailable while it is missing [Prop]


@dataclass(frozen=True, kw_only=True)
class HouseSensorDescription(SensorEntityDescription):
    """A house sensor: its value and attributes from the house's view."""

    value: Callable[[HouseView], Any]
    attributes: Callable[[HouseView], Mapping[str, Any]] | None = None


def _band_attributes(v: RoomView) -> dict[str, Any]:
    return {"band_low": v.band_low, "band_high": v.band_high, "position": v.position}


def _round(x: float | None, places: int = 2) -> float | None:
    return round(x, places) if x is not None else None


ROOM_SENSORS: tuple[RoomSensorDescription, ...] = (
    RoomSensorDescription(
        key="comfort_index",
        reading=True,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda v: _round(v.comfort_index),
        attributes=_band_attributes,
    ),
    RoomSensorDescription(
        key="target_temperature",
        reading=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        value=lambda v: _round(v.target_c),
    ),
    RoomSensorDescription(
        key="commanded_setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=1,
        value=lambda v: _round(v.commanded_c),
        attributes=lambda v: {"wanted": _round(v.wanted_c)},
    ),
    RoomSensorDescription(
        key="dew_point",
        reading=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda v: _round(v.dew_point_c),
        attributes=lambda v: {
            "outdoor_dew_point": _round(v.outdoor_dew_point_c),
            "outdoor_apparent_temperature": _round(v.outdoor_apparent_c),
            "free_cooling_advised": v.free_cooling_advised,
            "condensation_risk": v.condensation_risk,
        },
    ),
    RoomSensorDescription(
        key="controller",
        device_class=SensorDeviceClass.ENUM,
        options=[c.value for c in Controller],
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda v: v.in_charge,
        attributes=lambda v: {"plan": v.plan, **v.rl_score},
    ),
    RoomSensorDescription(
        key="learning",
        device_class=SensorDeviceClass.TEMPERATURE_DELTA,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value=lambda v: _round(v.learning, 3),
    ),
    RoomSensorDescription(
        key="comfort_in_band",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value=lambda v: _round(v.yesterday.comfort_in_band, 1) if v.yesterday else None,
    ),
    RoomSensorDescription(
        key="near_centre",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value=lambda v: _round(v.yesterday.near_centre, 1) if v.yesterday else None,
    ),
    RoomSensorDescription(
        key="starts_per_hour",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value=lambda v: _round(v.yesterday.starts_per_hour) if v.yesterday else None,
    ),
)


class RoomSensor(RoomEntity, SensorEntity):
    """A room sensor that reads one value from the room's latest cycle."""

    entity_description: RoomSensorDescription
    # the Controller sensor carries the plan: kept out of the recorder like the Mode sensor's
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str, description: RoomSensorDescription) -> None:
        super().__init__(coordinator, room_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return not self.entity_description.reading or self.native_value is not None

    @property
    def native_value(self) -> Any:
        v = self.view
        return self.entity_description.value(v) if v is not None and v.ok else None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        v = self.view
        attrs = self.entity_description.attributes
        return attrs(v) if attrs is not None and v is not None and v.ok else None


class ModeSensor(RoomEntity, SensorEntity):
    """The band rule in force; the room's main feature, named after the
    room. Its attributes are the full decision trace [D006]."""

    _attr_name = None
    _attr_device_class = SensorDeviceClass.ENUM
    _unrecorded_attributes = _UNRECORDED

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "mode")
        self._attr_options = [m.value for m in RoomModeState]

    @property
    def native_value(self) -> str | None:
        v = self.view
        return v.mode.value if v is not None and v.mode is not None else None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        v = self.view
        # the room id, for the precool actions (Section 7, Services)
        return {"room_id": self.room_id, **v.trace} if v is not None and v.ok else None


class SettingsSensor(RoomEntity, SensorEntity):
    """Every setting of the room, unset ones stated as unset, and the
    unit's capability profile [D048]."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _unrecorded_attributes = frozenset({"settings", "profile"})

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "settings")

    @property
    def native_value(self) -> int:
        return len(self.room.config.heads)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        config = self.room.config
        p = config.profile
        return {"settings": settings_dict(config), "profile": profile_to_dict(p) if p is not None else "unread"}


class CostSensor(RoomEntity, SensorEntity):
    """Yesterday's cost of the energy the room used; reset each day."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: AbodeHvacCoordinator, room_id: str) -> None:
        super().__init__(coordinator, room_id, "cost_of_comfort")

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.currency

    @property
    def native_value(self) -> float | None:
        v = self.view
        if v is None or not v.ok or v.yesterday is None or v.yesterday.cost is None:
            return None
        return round(v.yesterday.cost, 4)

    @property
    def last_reset(self) -> datetime:
        local = dt_util.as_local(dt_util.utcnow())
        return local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)


def _room_entities(coordinator: AbodeHvacCoordinator, room_id: str) -> Iterable[SensorEntity]:
    yield ModeSensor(coordinator, room_id)
    for d in ROOM_SENSORS:
        yield RoomSensor(coordinator, room_id, d)
    yield SettingsSensor(coordinator, room_id)
    yield CostSensor(coordinator, room_id)


HOUSE_SENSORS: tuple[HouseSensorDescription, ...] = (
    HouseSensorDescription(
        key="demand_forecast",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value=lambda h: h.demand_kwh,
        attributes=lambda h: {
            "until": h.demand_until.isoformat() if h.demand_until is not None else None,
            "to_tariff_change": h.demand_to_tariff_change,
            "intervals": list(h.demand_per_interval),
        },
    ),
    HouseSensorDescription(
        key="constraints",
        value=lambda h: len(h.constraints),
        attributes=lambda h: {"constraints": list(h.constraints)},
    ),
    HouseSensorDescription(
        key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda h: _round(h.outdoor_c),
    ),
    HouseSensorDescription(
        key="outdoor_apparent_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda h: _round(h.outdoor_apparent_c),
    ),
    HouseSensorDescription(
        key="outdoor_dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=lambda h: _round(h.outdoor_dew_point_c),
    ),
    HouseSensorDescription(
        key="forecast_peak",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value=lambda h: _round(h.forecast_peak_c),
    ),
    HouseSensorDescription(
        key="stale_feeds",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda h: len(h.stale),
        attributes=lambda h: {"feeds": list(h.stale)},
    ),
    HouseSensorDescription(
        key="rooms_configured",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda h: h.rooms,
    ),
)


class HouseSensor(HouseEntity, SensorEntity):
    """A house sensor."""

    entity_description: HouseSensorDescription

    def __init__(self, coordinator: AbodeHvacCoordinator, description: HouseSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        h = self.house
        return self.entity_description.value(h) if h is not None else None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        h = self.house
        attrs = self.entity_description.attributes
        return attrs(h) if attrs is not None and h is not None else None


class TariffRateSensor(HouseEntity, SensorEntity):
    """The tariff's price now, in the house currency per kWh, with the
    interval list."""

    _attr_suggested_display_precision = 4
    _unrecorded_attributes = frozenset({"intervals"})

    def __init__(self, coordinator: AbodeHvacCoordinator) -> None:
        super().__init__(coordinator, "tariff_rate")

    @property
    def native_unit_of_measurement(self) -> str:
        return f"{self.hass.config.currency}/{UnitOfEnergy.KILO_WATT_HOUR}"

    @property
    def native_value(self) -> float | None:
        h = self.house
        return h.tariff_now if h is not None else None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        h = self.house
        return {"rate": h.tariff_rate, "intervals": list(h.intervals)} if h is not None else None


class ProjectedCostSensor(HouseEntity, SensorEntity):
    """What the rooms' plans in force are priced at."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: AbodeHvacCoordinator) -> None:
        super().__init__(coordinator, "projected_cost")

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.currency

    @property
    def native_value(self) -> float | None:
        h = self.house
        return h.projected_cost if h is not None else None


async def async_setup_entry(
    hass: HomeAssistant, entry: AbodeHvacConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    house: list[SensorEntity] = [HouseSensor(coordinator, d) for d in HOUSE_SENSORS]
    house += [TariffRateSensor(coordinator), ProjectedCostSensor(coordinator)]
    async_add_entities(house)
    add_room_entities(hass, coordinator, async_add_entities, _room_entities)
