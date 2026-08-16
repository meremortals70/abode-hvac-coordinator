"""Constants.

No site data lives here. Comfort bands and entity IDs are configuration,
entered at setup. A fresh install starts empty.

The tariff is not configuration here at all: it belongs to Abode Power Tariffs,
and this integration holds only the identifier of the entry to read it from.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Final

DOMAIN: Final = "abode_hvac_coordinator"
LOGGER: Final = logging.getLogger(__package__)

#: Floor on how often rooms are re-evaluated. Evaluation is also driven by
#: state changes on the entities each room reads, so this is a backstop.
EVALUATION_INTERVAL: Final = timedelta(seconds=30)

#: How far ahead the model must show the band holding before a room coasts.
#: Long enough that coasting means something, short enough that the prediction
#: is still worth trusting.
COAST_HORIZON_HOURS: Final = 1.0

#: How much warmer outdoors must be than indoors to count as demand ahead,
#: before the model has learned enough to answer properly.
PRECOOL_DEMAND_MARGIN_C: Final = 2.0

CONF_ROOMS: Final = "rooms"
CONF_ROOM_ID: Final = "room_id"
CONF_CLIMATE_ENTITY: Final = "climate_entity_id"
CONF_TEMPERATURE_ENTITY: Final = "temperature_entity_id"
CONF_HUMIDITY_ENTITY: Final = "humidity_entity_id"
CONF_PRESENCE_ENTITY: Final = "presence_entity_id"
CONF_SLEEP_SCHEDULE_ENTITY: Final = "sleep_schedule_entity_id"
CONF_DIRECT_SUN_ENTITY: Final = "direct_sun_entity_id"
CONF_WINDOW_DIRECTION: Final = "window_direction"
CONF_OVERHANG_PROJECTION: Final = "overhang_projection_m"
CONF_OVERHANG_HEIGHT: Final = "overhang_height_m"

#: Shown in every optional entity picker so an empty field reads as a
#: deliberate "none", not as a form that failed to load.
NOTHING_SELECTED: Final = "Nothing selected"
CONF_OCCUPIED_AFTER: Final = "occupied_after_minutes"
CONF_VACANT_AFTER: Final = "vacant_after_minutes"
CONF_WARNING_GRACE: Final = "warning_grace_minutes"
CONF_ANNOUNCE: Final = "announce_before_shutdown"
CONF_ANNOUNCE_TARGETS: Final = "announce_target_entity_ids"
CONF_HEAT_LOAD_ENTITY: Final = "heat_load_entity_id"
CONF_FAN_ENTITY: Final = "air_movement_entity_id"

# --- tariff, read from Abode Power Tariffs ------------------------------

#: The domain that owns the plan. This integration reads from it and never
#: holds a plan of its own.
TARIFF_DOMAIN: Final = "abode_power_tariffs"
TARIFF_SERVICE_GET_INTERVALS: Final = "get_intervals"

#: Which Abode Power Tariffs entry to read. Optional: with nothing selected the
#: controller runs without a tariff, exactly as it does before one is set up.
CONF_TARIFF_ENTRY_ID: Final = "tariff_config_entry_id"

#: How much of the forward series to ask for. A day covers the demand
#: forecast's eight-hour horizon with room to spare, so a fetch that fails is
#: not immediately a gap.
TARIFF_HORIZON_HOURS: Final = 24

#: Resolution to ask for. Consecutive intervals sharing a rate are collapsed
#: back into periods, so this is the granularity of a boundary, not of a
#: decision.
TARIFF_RESOLUTION_MINUTES: Final = 30

#: How often the series is refetched. Boundaries are resolved from the series
#: itself, so this only has to be short enough that a plan edited in the
#: tariff integration is picked up without a restart.
TARIFF_REFRESH_INTERVAL: Final = timedelta(minutes=15)
CONF_OUTDOOR_TEMPERATURE_ENTITY: Final = "outdoor_temperature_entity_id"

#: Outdoor humidity. Optional, and only used for the free-cooling advisory:
#: outdoor air that is cooler but wetter makes a subtropical room worse, and
#: dry bulb alone cannot tell you which case you are in.
CONF_OUTDOOR_HUMIDITY_ENTITY: Final = "outdoor_humidity_entity_id"

#: Outdoor wind speed. Optional. Whatever unit the entity reports is converted;
#: the Bureau's apparent temperature formula wants metres per second, and most
#: Australian weather feeds publish km/h, so assuming the unit would make the
#: figure wrong by a factor of 3.6.
CONF_OUTDOOR_WIND_ENTITY: Final = "outdoor_wind_entity_id"

#: The weather entity supplying the hourly forecast. Optional. Without it
#: precool falls back to comparing current conditions, which is what the
#: controller did before the forecast existed and is stated as such.
CONF_WEATHER_ENTITY: Final = "weather_entity_id"

#: How often the forecast is refetched. Hourly forecasts do not change faster
#: than this, and precool decisions turn on the shape of the day rather than
#: on any one hour.
WEATHER_REFRESH_INTERVAL: Final = timedelta(minutes=30)

WEATHER_DOMAIN: Final = "weather"
WEATHER_SERVICE_GET_FORECASTS: Final = "get_forecasts"
CONF_HORIZON_HOURS: Final = "horizon_hours"
CONF_OPENING_ENTITIES: Final = "opening_entity_ids"
CONF_COVER_ENTITIES: Final = "cover_entity_ids"
CONF_LOCKOUT_REASON: Final = "lockout_reason"
#: Custom lockout reasons the user has typed. Stored once for the whole entry,
#: so a reason invented for one room is offered for every room afterwards.
CONF_LOCKOUT_REASONS: Final = "lockout_reasons"

#: First option in the lockout dropdown. Selecting it means the room is not
#: locked out, which is why lockout needs no separate toggle and no second
#: screen: one field answers both questions.
NOT_LOCKED_OUT: Final = "Not locked out"

#: Offered in the lockout dropdown before the user has added any of their own.
DEFAULT_LOCKOUT_REASONS: Final = (
    "Under renovation",
    "Unit disconnected",
    "Awaiting commissioning",
    "Faulty, awaiting repair",
    "Seasonal shutdown",
    "Not in use",
)

#: Seeded into every new room, identically, so a fresh install is sensible with
#: zero configuration. Derived from the ASHRAE 55 sedentary comfort zone
#: converted onto the comfort index scale, not from any particular house.
#: Editable in the form; change them freely.
#:
#: Unoccupied has no band: an unoccupied room is off. Precondition uses the
#: occupied band. Coast inherits the band it displaced.
DEFAULT_BANDS: Final = {
    "occupied": {"low": 24.0, "high": 27.0},
    "sleep": {"low": 21.0, "high": 24.0},
    "precool": {"low": 24.0, "high": 27.0},
}

#: Used when a room is locked out but no reason was given. Should not normally
#: happen, but a lockout without an explanation is worse than a generic one.
FALLBACK_LOCKOUT_REASON: Final = "Locked out"
CONF_BANDS: Final = "bands"
CONF_BAND_LOW: Final = "low"
CONF_BAND_HIGH: Final = "high"

SERVICE_HEADING_HOME: Final = "heading_home"
SERVICE_CLEAR_OVERRIDE: Final = "clear_override"
ATTR_ROOM_ID: Final = "room_id"
ATTR_DEADLINE: Final = "deadline"

ISSUE_UNRECOGNISED_CONSTRAINT: Final = "unrecognised_constraint"
ISSUE_TARIFF_UNAVAILABLE: Final = "tariff_unavailable"
ISSUE_FORECAST_UNAVAILABLE: Final = "forecast_unavailable"
ISSUE_NO_BANDS: Final = "room_without_bands"
