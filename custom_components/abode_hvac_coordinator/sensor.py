"""Sensor platform.

Three per room: the mode with its full decision trace, the comfort index, and
the derived dry-bulb target. The trace is not a debug aid — without it the
system is unmaintainable, so it is published as entity attributes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HvacConfigEntry
from .const import DOMAIN
from .coordinator import HvacCoordinator
from .entity import HvacRoomEntity
from .models import DecisionTrace, RoomConfig

# The coordinator centralises evaluation and this platform is read-only.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class HvacSensorDescription(SensorEntityDescription):
    """Describes an HVAC coordinator sensor."""

    value_fn: Callable[[DecisionTrace], str | float | None]
    attributes_fn: Callable[[DecisionTrace], dict[str, Any]] | None = None
    #: True where a missing value means the sensor is unavailable rather than
    #: unknown. The comfort index has no value when its source sensors are
    #: dead; the mode always has one.
    unavailable_when_none: bool = False


def _configured(value: str | None) -> str:
    """An entity id, or a plain statement that nothing is selected.

    An empty field is ambiguous: it reads as "not loaded" as easily as "none".
    Saying so removes the ambiguity.
    """
    return value or "Nothing selected"


SENSORS: tuple[HvacSensorDescription, ...] = (
    HvacSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=["lockout", "unoccupied", "occupied", "sleep", "precondition", "precool", "coast"],
        value_fn=lambda trace: str(trace.mode),
        attributes_fn=lambda trace: trace.as_attributes(),
    ),
    HvacSensorDescription(
        key="comfort_index",
        translation_key="comfort_index",
        native_unit_of_measurement="HCI",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda trace: trace.hci,
        unavailable_when_none=True,
        attributes_fn=lambda trace: {
            "band_low": trace.band_low,
            "band_high": trace.band_high,
            "band_position": trace.band_position,
        },
    ),
    HvacSensorDescription(
        key="commanded_dry_bulb",
        translation_key="commanded_dry_bulb",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda trace: trace.commanded_dry_bulb_c,
        unavailable_when_none=True,
        attributes_fn=lambda trace: {
            "solved_target_c": trace.target_dry_bulb_c,
            "regulation_trim_c": trace.regulation_trim_c,
        },
    ),
    HvacSensorDescription(
        key="dew_point",
        translation_key="dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda trace: trace.dew_point_c,
        unavailable_when_none=True,
        attributes_fn=lambda trace: {
            "outdoor_dew_point_c": trace.outdoor_dew_point_c,
            "outdoor_apparent_c": trace.outdoor_apparent_c,
            "free_cooling_advised": trace.free_cooling_advised,
            "condensation_risk": trace.condensation_risk,
        },
    ),
    HvacSensorDescription(
        key="target_dry_bulb",
        translation_key="target_dry_bulb",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda trace: trace.target_dry_bulb_c,
        unavailable_when_none=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HvacConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors, and add more as rooms are configured."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_rooms() -> None:
        new: list[SensorEntity] = []
        for room_id, room in coordinator.rooms.items():
            if room_id in known:
                continue
            new.extend(
                HvacRoomSensor(coordinator, room, description)
                for description in SENSORS
            )
            new.append(RoomSettingsSensor(coordinator, room))
        known.update(coordinator.rooms)
        if new:
            async_add_entities(new)

    _add_new_rooms()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_rooms))
    async_add_entities(
        [
            DemandForecastSensor(coordinator, entry),
            *[
                HubSensor(coordinator, entry, description)
                for description in HUB_SENSORS
            ],
        ]
    )



class HvacRoomSensor(HvacRoomEntity, SensorEntity):
    """A sensor reporting one aspect of a room's decision."""

    entity_description: HvacSensorDescription

    def __init__(
        self,
        coordinator: HvacCoordinator,
        room: RoomConfig,
        description: HvacSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, room)
        self.entity_description = description
        self._attr_unique_id = f"{room.room_id}_{description.key}"

    @property
    def available(self) -> bool:
        """Unavailable when the reading behind this sensor is missing."""
        if not super().available:
            return False
        if not self.entity_description.unavailable_when_none:
            return True
        trace = self.trace
        return trace is not None and self.entity_description.value_fn(trace) is not None

    @property
    def native_value(self) -> str | float | None:
        """Return the current value."""
        if (trace := self.trace) is None:
            return None
        return self.entity_description.value_fn(trace)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the supporting detail, including the decision trace."""
        if (trace := self.trace) is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(trace)


@dataclass(frozen=True, kw_only=True)
class HubSensorDescription(SensorEntityDescription):
    """Describes a house-wide sensor on the coordinator device."""

    value_fn: Callable[[HvacCoordinator], str | float | None]
    attributes_fn: Callable[[HvacCoordinator], dict[str, Any]] | None = None


def _tariff_rate(coordinator: HvacCoordinator) -> str | None:
    """The rate label in force right now, as named by the tariff."""
    interval = coordinator.interval_now()
    return None if interval is None else interval.rate


def _constraints(coordinator: HvacCoordinator) -> str | None:
    """Which constraints are in force, as a readable string."""
    interval = coordinator.interval_now()
    if interval is None:
        return None
    return ", ".join(sorted(interval.constraints)) if interval.constraints else "none"


def _projected_cost(coordinator: HvacCoordinator) -> float | None:
    """What the forecast energy would cost at the published prices.

    Per-window, because the whole point of a time-of-use plan is that the same
    kWh costs different amounts depending on when it is used.

    Prices arrive from Abode Power Tariffs in dollars per kWh, so the product
    is already dollars. Nothing here divides by a hundred.
    """
    forecast = coordinator.forecast
    series = coordinator.tariff
    if forecast is None or series is None:
        return None

    prices = {
        window.start: window.per_kwh
        for window in series.windows()
        if window.per_kwh is not None
    }
    total = 0.0
    priced = False
    for window in forecast.windows:
        price = prices.get(window.start)
        if price is None:
            continue
        priced = True
        total += window.kwh * price
    return round(total, 2) if priced else None


HUB_SENSORS: tuple[HubSensorDescription, ...] = (
    HubSensorDescription(
        key="tariff_rate",
        translation_key="tariff_rate",
        value_fn=_tariff_rate,
        attributes_fn=lambda c: {
            "source": c.tariff_entry_id or "Nothing selected",
            "fetched_at": (
                c.tariff.fetched_at.isoformat() if c.tariff else None
            ),
            "covers_until": (
                c.tariff.covers_until.isoformat()
                if c.tariff and c.tariff.covers_until
                else None
            ),
            "windows": [
                {
                    "start": w.start.isoformat(),
                    "end": w.end.isoformat(),
                    "rate": w.rate,
                    "per_kwh": w.per_kwh,
                    "constraints": sorted(w.constraints),
                    "coasting_permitted": w.coasting_permitted,
                }
                for w in c.tariff.windows()
            ]
            if c.tariff
            else [],
        },
    ),
    HubSensorDescription(
        key="active_constraints",
        translation_key="active_constraints",
        value_fn=_constraints,
    ),
    HubSensorDescription(
        key="projected_cost",
        translation_key="projected_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="AUD",
        suggested_display_precision=2,
        value_fn=_projected_cost,
    ),
    HubSensorDescription(
        key="outdoor_apparent_temperature",
        translation_key="outdoor_apparent_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: c.outdoor_apparent_temperature(),
        attributes_fn=lambda c: {
            "wind_ms": (
                None if (w := c.outdoor_wind_ms()) is None else round(w, 1)
            ),
            "wind_included": c.outdoor_wind_entity_id is not None,
        },
    ),
    HubSensorDescription(
        key="outdoor_dew_point",
        translation_key="outdoor_dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.outdoor_dew_point(),
    ),
    HubSensorDescription(
        key="forecast_peak",
        translation_key="forecast_peak",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: c.forecast_peak(),
        attributes_fn=lambda c: {
            "source": c.weather_entity_id or "Nothing selected",
            "peak_at": c.forecast_peak_at(),
            "fetched_at": (
                c.trajectory.fetched_at.isoformat() if c.trajectory else None
            ),
            "covers_until": (
                c.trajectory.covers_until.isoformat()
                if c.trajectory and c.trajectory.covers_until
                else None
            ),
            "hours": len(c.trajectory.points) if c.trajectory else 0,
        },
    ),
    HubSensorDescription(
        key="stale_feeds",
        translation_key="stale_feeds",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: sum(
            len(trace.stale_feeds) for trace in (c.data or {}).values()
        ),
        attributes_fn=lambda c: {
            "feeds": [
                feed
                for trace in (c.data or {}).values()
                for feed in trace.stale_feeds
            ]
        },
    ),
    HubSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.outdoor_reading(),
    ),
    HubSensorDescription(
        key="rooms_configured",
        translation_key="rooms_configured",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: len(c.rooms),
        attributes_fn=lambda c: {
            "rooms": sorted(room.name for room in c.rooms.values())
        },
    ),
)


def hub_device_info(entry: HvacConfigEntry) -> DeviceInfo:
    """The house-wide device every global setting appears under."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Abode HVAC Coordinator",
        manufacturer="Abode",
        model="Coordinator",
        entry_type=DeviceEntryType.SERVICE,
    )


class HubSensor(CoordinatorEntity[HvacCoordinator], SensorEntity):
    """A house-wide value, visible rather than buried in a config form."""

    _attr_has_entity_name = True
    entity_description: HubSensorDescription

    def __init__(
        self,
        coordinator: HvacCoordinator,
        entry: HvacConfigEntry,
        description: HubSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = hub_device_info(entry)

    @property
    def native_value(self) -> str | float | None:
        """Return the current value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the supporting detail."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator)


class DemandForecastSensor(CoordinatorEntity[HvacCoordinator], SensorEntity):
    """Projected HVAC energy over the horizon.

    This is the published contract with whatever owns the battery. It carries
    **no vendor concepts**: projected kWh, a per-window breakdown, and the
    constraints in force. A Powerwall automation and a Sungrow automation each
    translate it into their own primitives.

    The controller never writes battery actuators itself. Two writers on one
    actuator fail silently, and battery control is vendor-specific — coding one
    in would tie this project to a single manufacturer.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "demand_forecast"
    #: No device class. `energy` means metered consumption and Home Assistant
    #: requires `total` or `total_increasing` with it, which this is not: it is
    #: a projection of energy not yet used, and it goes down as often as up.
    #: Declaring the device class produced a warning at every startup, and the
    #: warning was right. The unit still says kWh.
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, coordinator: HvacCoordinator, entry: HvacConfigEntry) -> None:
        """Initialize the forecast sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_demand_forecast"
        self._attr_device_info = hub_device_info(entry)

    @property
    def available(self) -> bool:
        """Available once a forecast has been produced."""
        return super().available and self.coordinator.forecast is not None

    @property
    def native_value(self) -> float | None:
        """Projected energy over the horizon, in kWh."""
        forecast = self.coordinator.forecast
        return None if forecast is None else forecast.total_kwh

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The per-window breakdown, so automations can read either."""
        forecast = self.coordinator.forecast
        return None if forecast is None else forecast.as_attributes()


class RoomSettingsSensor(HvacRoomEntity, SensorEntity):
    """What this room is configured with, readable without opening a form.

    The state is a one-line summary; the attributes are every setting. A
    configuration you have to edit in order to inspect is a configuration
    nobody ever checks, and a wrong entity sitting in a form is invisible
    until something misbehaves.
    """

    _attr_translation_key = "settings"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clipboard-list-outline"

    def __init__(self, coordinator: HvacCoordinator, room: RoomConfig) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, room)
        self._attr_unique_id = f"{room.room_id}_settings"

    @property
    def available(self) -> bool:
        """Always available: configuration exists whether or not it has run."""
        return True

    @property
    def _config(self) -> RoomConfig | None:
        return self.coordinator.rooms.get(self._room_id)

    @property
    def native_value(self) -> str | None:
        """A one-line summary, so the state itself says something useful."""
        room = self._config
        if room is None:
            return None
        if room.lockout_reason:
            return f"Locked out — {room.lockout_reason}"
        bands = ", ".join(sorted(str(mode) for mode in room.bands))
        return f"{bands} bands" if bands else "No bands configured"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Every setting for this room, with unset ones stated as unset."""
        room = self._config
        if room is None:
            return None
        return {
            "room_id": room.room_id,
            "air_conditioner": _configured(room.climate_entity_id),
            "temperature_sensor": _configured(room.temperature_entity_id),
            "humidity_sensor": _configured(room.humidity_entity_id),
            "presence_sensor": _configured(room.presence_entity_id),
            "sleep_schedule": _configured(room.sleep_schedule_entity_id),
            "heat_source": _configured(room.heat_load_entity_id),
            "air_movement": _configured(room.air_movement_entity_id),
            "sun_on_window_sensor": _configured(room.direct_sun_entity_id),
            "windows_face": _configured(room.window_direction),
            "overhang_projection_m": room.overhang_projection_m or "None",
            "overhang_height_m": room.overhang_height_m or "None",
            "windows_and_doors": list(room.opening_entity_ids) or "Nothing selected",
            "blinds": list(room.cover_entity_ids) or "Nothing selected",
            "comfort_bands": {
                str(mode): {"low": band.low, "high": band.high}
                for mode, band in room.bands.items()
            }
            or "None configured — this room will never be actuated",
            "wait_before_starting_minutes": (
                room.grace.occupied_after.total_seconds() / 60
            ),
            "wait_before_stopping_minutes": (
                room.grace.vacant_after.total_seconds() / 60
            ),
            "warning_grace_minutes": room.grace.warning_grace.total_seconds() / 60,
            "announces_before_shutdown": room.grace.announce,
            "announce_through": list(room.announce_target_entity_ids)
            or "Nothing selected",
            "lockout_reason": room.lockout_reason or "Not locked out",
        }
