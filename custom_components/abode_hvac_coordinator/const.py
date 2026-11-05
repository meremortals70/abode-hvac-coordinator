"""Names and fixed values for the Abode HVAC Coordinator.

This file holds names and fixed values only, and no logic. Every design
value of the specification's Section 10 that a module runs with is here,
with the tag that says what it rests on:

    [Set]  a setting chosen by judgement (specification Appendix B2)
    [Prop] a design choice of the specification (Appendix B1)
    [Der]  derived; the working is in the specification where cited
    [Sim]  shown by the specification's simulations (Section 11)
    [NYE]  not yet established (Appendix B3)
    [Dnnn] a design rule (Appendix A)
    [HA]   Home Assistant core source

Times are in seconds unless the name says otherwise.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "abode_hvac_coordinator"

# --------------------------------------------------------------------------
# Services (Section 7)
# --------------------------------------------------------------------------
SERVICE_PRECOOL: Final = "precool"
SERVICE_CANCEL_PRECOOL: Final = "cancel_precool"
ATTR_ROOM_ID: Final = "room_id"
ATTR_READY_BY: Final = "ready_by"

# --------------------------------------------------------------------------
# House settings: the config entry's data (Section 7, House screens)
# --------------------------------------------------------------------------
CONF_TARIFF_ENTRY: Final = "tariff_entry"
CONF_OUTDOOR_TEMPERATURE: Final = "outdoor_temperature"
CONF_OUTDOOR_HUMIDITY: Final = "outdoor_humidity"
CONF_OUTDOOR_WIND: Final = "outdoor_wind"
CONF_WEATHER: Final = "weather"
CONF_BATTERY_CHARGE: Final = "battery_charge"
CONF_BATTERY_SIZE: Final = "battery_size"
CONF_BATTERY_MAX_DISCHARGE: Final = "battery_max_discharge"
CONF_SOLAR_POWER: Final = "solar_power"
CONF_HOUSE_LOAD: Final = "house_load"
CONF_RESERVE_MARGIN: Final = "reserve_margin"
CONF_GRID_POWER: Final = "grid_power"
CONF_GRID_POSITIVE_IS_IMPORT: Final = "grid_positive_is_import"
CONF_SOLAR_FORECAST_SOURCES: Final = "solar_forecast_sources"
CONF_TTS_ENGINE: Final = "tts_engine"

# --------------------------------------------------------------------------
# Room settings: the config entry's options (Section 7, Room screens 1-3)
# --------------------------------------------------------------------------
CONF_ROOMS: Final = "rooms"
CONF_ROOM_ID: Final = "room_id"
CONF_NAME: Final = "name"
CONF_HEADS: Final = "heads"
CONF_CLIMATE: Final = "climate"
CONF_OUTDOOR_UNIT: Final = "outdoor_unit"
CONF_FREQUENCY_SENSOR: Final = "frequency_sensor"
CONF_TEMPERATURE_SENSOR: Final = "temperature_sensor"
CONF_HUMIDITY_SENSOR: Final = "humidity_sensor"
CONF_PRESENCE_SENSOR: Final = "presence_sensor"
CONF_SLEEP_SCHEDULE: Final = "sleep_schedule"
CONF_SUN_SENSOR: Final = "sun_sensor"
CONF_OPENINGS: Final = "openings"
CONF_BLINDS: Final = "blinds"
CONF_ALLOW_BLIND_CONTROL: Final = "allow_blind_control"
CONF_WINDOW_FACING: Final = "window_facing"
CONF_OVERHANG_PROJECTION: Final = "overhang_projection"
CONF_OVERHANG_HEIGHT: Final = "overhang_height"
CONF_OCCUPIED_AFTER: Final = "occupied_after"
CONF_EMPTY_AFTER: Final = "empty_after"
CONF_WARNING_GRACE: Final = "warning_grace"
CONF_ANNOUNCE: Final = "announce"
CONF_ANNOUNCE_THROUGH: Final = "announce_through"
CONF_OPENING_GRACE: Final = "opening_grace"
CONF_LOCKOUT_REASON: Final = "lockout_reason"
CONF_OCCUPIED_LOW: Final = "occupied_low"
CONF_OCCUPIED_HIGH: Final = "occupied_high"
CONF_SLEEP_LOW: Final = "sleep_low"
CONF_SLEEP_HIGH: Final = "sleep_high"
CONF_PRECOOL_LOW: Final = "precool_low"
CONF_PRECOOL_HIGH: Final = "precool_high"
CONF_POWER_MANAGEMENT: Final = "power_management"
CONF_PROFILE: Final = "profile"

# Room setting defaults (Section 7)
DEFAULT_ALLOW_BLIND_CONTROL: Final = True  # [Set]
DEFAULT_OCCUPIED_AFTER_MIN: Final = 2.0  # [Set]
DEFAULT_EMPTY_AFTER_MIN: Final = 10.0  # [Set]
DEFAULT_WARNING_GRACE_MIN: Final = 3.0  # [D050]
DEFAULT_ANNOUNCE: Final = False  # [Set]
DEFAULT_OPENING_GRACE_MIN: Final = 5.0  # [D050]
# 22-24 C, 19.5-22 C and 22-24 C at 60 % RH; set before the still-air term
# was removed (27 October 2026) at 24/27, 21/24, 24/27, lowered by that
# term's 1.0 so each band still means the same air [Set]
DEFAULT_OCCUPIED_BAND: Final = (23.0, 26.0)  # [Set]
DEFAULT_SLEEP_BAND: Final = (20.0, 23.0)  # [Set]
DEFAULT_PRECOOL_BAND: Final = (23.0, 26.0)  # [Set]
BAND_FIELD_MIN: Final = 0.0  # comfort index field range [Set]
BAND_FIELD_MAX: Final = 50.0
BAND_FIELD_STEP: Final = 0.5
OVERHANG_FIELD_MAX_M: Final = 10.0  # [Set]
OVERHANG_FIELD_STEP_M: Final = 0.05

# Default lockout reasons, offered in the dropdown [Set]
LOCKOUT_NOT_LOCKED_OUT: Final = "Not locked out"
DEFAULT_LOCKOUT_REASONS: Final = (
    "Under renovation",
    "Unit disconnected",
    "Awaiting commissioning",
    "Faulty, awaiting repair",
    "Seasonal shutdown",
    "Not in use",
)

# Window directions in compass degrees, clockwise from north [D023]
WINDOW_FACINGS: Final[dict[str, float]] = {
    "N": 0.0,
    "NE": 45.0,
    "E": 90.0,
    "SE": 135.0,
    "S": 180.0,
    "SW": 225.0,
    "W": 270.0,
    "NW": 315.0,
}

# --------------------------------------------------------------------------
# Timing (Section 10)
# --------------------------------------------------------------------------
CYCLE_S: Final = 30.0  # [Set]
MODEL_STEP_S: Final = 300.0  # filter and plan step [Prop][R1]
TARIFF_READ_S: Final = 900.0  # [Set]
SOLAR_FORECAST_READ_S: Final = 900.0  # [D061]
WEATHER_READ_S: Final = 1800.0  # [Set]
LEARNED_SAVE_S: Final = 300.0  # [D031][D064]
CHOICE_SAVE_S: Final = 5.0  # [D064]

# --------------------------------------------------------------------------
# Comfort index, helpers (Section 4.1, Section 9)
# --------------------------------------------------------------------------
BLIND_CLOSED_TRANSMISSION: Final = 0.15  # [Set]
BLIND_FULLY_BLOCKING_AT: Final = 0.05  # open fraction [Set]
INDEX_SOLVE_LOW_C: Final = 5.0  # [Set]
INDEX_SOLVE_HIGH_C: Final = 40.0  # [Set]
INDEX_SOLVE_TOLERANCE: Final = 0.001  # [Set]
INDEX_SOLVE_MAX_HALVINGS: Final = 60  # [Set]; 17 always suffice [Der]
DEW_POINT_MIN_RH: Final = 0.5  # % [Set]
FREE_COOLING_INDEX_MARGIN: Final = 2.0  # [Set]
FREE_COOLING_DEW_POINT_MARGIN_C: Final = 1.0  # [Set]
FREE_COOLING_WIND_SHARE: Final = 0.3  # [Set]
FREE_COOLING_WAIT_S: Final = 600.0  # [D073]
CONDENSATION_MARGIN_C: Final = 2.5  # [Set]
SUN_MIN_ELEVATION_DEG: Final = 5.0  # [Set]
SUN_MAX_OFF_SQUARE_DEG: Final = 90.0  # [Der]
CLEAR_SKY_FLOOR: Final = 0.2  # [Set]
CLEAR_SKY_CLOUD_LIMIT: Final = 90.0  # % [Set]
CLEAR_SKY_UV_DIVISOR: Final = 8.0  # [Set]
NO_STEP_ADVERTISED: Final = 0.1  # a unit that advertises no step: tenths [D051]
RANGE_ONLY_HALF_SPAN_C: Final = 1.0  # range-only unit gets setpoint +-1.0 [D024]

# --------------------------------------------------------------------------
# Observe (Module 1)
# --------------------------------------------------------------------------
OUTDOOR_TOO_OLD_S: Final = 3 * 3600.0  # [D030]
POWER_TOO_OLD_S: Final = 15 * 60.0  # [D030]
LOAD_STEP_WINDOW_S: Final = 60.0  # [Prop]
LOAD_STEP_SHARE_OF_DRAW: Final = 0.5  # [Prop]

# --------------------------------------------------------------------------
# Model (Module 2, Section 4.2-4.3)
# --------------------------------------------------------------------------
FILTER_STATE_SIZE: Final = 27  # 25 [Prop], + delay and lag (decision of 27 October 2026)
FILTER_START_FIGURES: Final[dict[str, float]] = {  # [Prop][Sim]
    "a": 0.2,
    "b": 0.2,
    "c": 0.3,
    "k": 0.1,
    "s": 0.4,
    "m": 0.1,
}
FILTER_START_VAR_T: Final = 0.01  # [Prop]
FILTER_START_VAR_M: Final = 4.0  # [Prop]
FILTER_START_VAR_LOG_FIGURE: Final = 1.0  # [Prop]
FILTER_START_VAR_HEAT_PATTERN: Final = 0.25  # [Sim]
FILTER_START_VAR_CURVE_STEP: Final = 0.5  # [NYE]
FILTER_START_CURVE_STEP_LOG: Final = 0.0  # ln 1 [NYE]
FILTER_NOISE_T_PER_H: Final = 1e-3  # C^2 per hour [Prop][NYE]
FILTER_NOISE_M_PER_H: Final = 1e-4  # C^2 per hour [Prop][NYE]
FILTER_NOISE_FIGURE: Final = 1e-6  # per step, in the logarithm [Prop][NYE]
FILTER_RUNNING_RATE_NOISE_C_PER_H: Final = 0.5  # [Prop][NYE]
FILTER_JACOBIAN_DELTA: Final = 1e-5  # [Prop]
SENSOR_NOISE_WEIGHT: Final = 1.0 / 288.0  # about one day [Prop]
SENSOR_NOISE_SETTLE_CORRECTIONS: Final = 288  # the first day [Prop]
HEAT_PATTERN_HARMONICS: Final = 2  # [Prop][Sim]
UNIT_CURVE_EFFORTS_C: Final = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)  # [Prop]
# The unit's delay and lag are states of the heat filter (decision of 27
# October 2026); the range is the specification's search range [Prop].
UNIT_DELAY_RANGE_MIN: Final = (0.0, 30.0)  # [Prop]
UNIT_LAG_MIN_MIN: Final = 1.0  # [Prop]
FILTER_START_VAR_LN_LAG: Final = 1.0  # [Sim][NYE]
FILTER_START_VAR_DELAY: Final = 25.0  # min^2 [Sim][NYE]
FILTER_NOISE_LN_LAG: Final = 1e-5  # per step [Sim][NYE]
FILTER_NOISE_DELAY: Final = 1e-3  # min^2 per step [Sim][NYE]
UNIT_MIN_RATE_HOLD_MIN: Final = 30.0  # [Prop]
FILTER_START_DELAY_MIN: Final = 6.0  # the simulated unit's delay (Section 11.1) [NYE]
FILTER_START_LAG_MIN: Final = 15.0  # the simulated unit's lag (Section 11.1) [NYE]
DRAW_WITHOUT_LOAD_SENSOR_KW: Final = 1.2  # [D028]
# The humidity filter (model/humidity.py). Not simulated [NYE].
HUMIDITY_START_H_PER_H: Final = 0.5  # exchange with outdoor air, per hour [NYE]
HUMIDITY_START_Q_HPA_PER_H: Final = 0.0  # steady indoor source [NYE]
HUMIDITY_START_DRYING_HPA_PER_H: Final = 1.0  # [NYE]
HUMIDITY_START_COOLING_HPA_PER_C: Final = 0.5  # hPa removed per C of cooling [NYE]
HUMIDITY_START_REBOUND_HPA_PER_H: Final = 1.0  # [NYE]
HUMIDITY_START_VAR_E: Final = 1.0  # hPa^2 [NYE]
HUMIDITY_START_VAR_LOG_FIGURE: Final = 1.0  # [NYE]
HUMIDITY_START_VAR_Q: Final = 0.25  # [NYE]
HUMIDITY_NOISE_E_PER_H: Final = 1e-2  # hPa^2 per hour [NYE]
HUMIDITY_NOISE_FIGURE: Final = 1e-6  # per step [NYE]
COIL_REBOUND_S: Final = 15 * 60.0  # [D060]
TRUST_ONE_HOUR_C: Final = 0.3  # [Prop][NYE]
TRUST_HOURS_KEPT: Final = 48  # [Prop][NYE]
TRUST_HOURS_NEEDED: Final = 24  # [Prop][NYE]
TRUST_SIX_HOUR_DAYS: Final = 14  # [Prop][NYE]
SILENT_FILTER_REPAIR_S: Final = 24 * 3600.0  # [Prop]
ROOM_SENSOR_GONE_REPAIR_S: Final = 15 * 60.0  # a sensor gone this long raises its repair [Set]

# --------------------------------------------------------------------------
# Comfort (Module 3)
# --------------------------------------------------------------------------
BAND_RAMP_S: Final = 3600.0  # [D014]
PRECOOL_ENDS_AFTER_READY_S: Final = 3600.0  # [D080]

# --------------------------------------------------------------------------
# Economics (Module 4, Section 4.7)
# --------------------------------------------------------------------------
LOAD_AVERAGE_S: Final = 15 * 60.0  # [D067]
CEILING_RISE_HOLD_S: Final = 10 * 60.0  # [D062]
CEILING_RISE_MARGIN_KW: Final = 0.3  # [D062]
IMPORT_REPORTED_ABOVE_KWH: Final = 0.05  # [D043]
GRID_ZERO_FLOW_W: Final = 5.0  # [D038]
GRID_SIGN_CONTRADICTIONS: Final = 10  # [D038]
GRID_EVIDENCE_NET_LOAD_KW: Final = 0.2  # [Set]
GRID_EVIDENCE_BATTERY_WINDOW_S: Final = 5 * 60.0  # [Set]
ALLOWANCE_PRECISION_KW: Final = 0.005  # [Set]
# long enough to find the next tariff change, which ends the demand
# forecast (decision of 29 October 2026) [Prop]
TARIFF_REQUEST_HOURS: Final = 24
TARIFF_RESOLUTION_MIN: Final = 30  # the plan's half-hour [Prop]
CEILING_RATE_STEP: Final = 0.05  # C/h; the ceiling is found to this [Prop]
# Constraints Abode Power Tariffs declares (its const.py KNOWN_CONSTRAINTS,
# commit 34d71d5); only no_grid_import bears on the air conditioners.
CONSTRAINT_NO_GRID_IMPORT: Final = "no_grid_import"
KNOWN_CONSTRAINTS: Final = frozenset(
    {"no_grid_import", "precool_opportunity", "grid_charge_battery", "coasting_permitted"}
)

# --------------------------------------------------------------------------
# Control (Module 5, Section 4.4-4.5, 4.9)
# --------------------------------------------------------------------------
PLAN_HORIZON_H: Final = 6.0  # [Prop][Sim]
PLAN_STEP_H: Final = 0.5  # [Prop][Sim]
PLAN_STEPS: Final = 12  # [Prop][Sim]
PLAN_POINTS_PER_STEP: Final = 6  # 5-minute points [Prop]
PLAN_BEAM_WIDTH: Final = 50  # [Prop][Sim][NYE]
PLAN_MARGIN_SIGMA: Final = 1.0  # margin inside the band, in forecast sigmas [Prop][Sim]
PLAN_START_COST_SHARE: Final = 0.5  # c_start = 0.5 p_max D1 x 1 h [Prop][Sim][NYE]
ARBITER_HAND_UP_TRUST_DAYS: Final = 14  # [Prop][NYE]
ARBITER_HAND_UP_WINS: Final = 11  # of the last 14 [Prop][Der]
ARBITER_WINDOW_DAYS: Final = 14  # [Prop]
ARBITER_HAND_DOWN_DAYS: Final = 2  # [Prop]
ARBITER_FIDELITY_C: Final = 0.3  # [Prop][NYE]
# The PID's delay is the learned delay plus half the interval at which the
# setpoint can actually change: a sampled controller acts on average half
# an interval late (decision of 27 October 2026).
PID_FINE_STEP_C: Final = 0.1  # steps at or below this follow every cycle [Prop]

# --------------------------------------------------------------------------
# Delivery and Limits (Modules 6-7, Section 4.6)
# --------------------------------------------------------------------------
WHOLE_DEGREE_CYCLE_S: Final = 30 * 60.0  # [Prop]
SETPOINT_MIN_HOLD_S: Final = 10 * 60.0  # [Prop]
BLIND_MOVE_POINTS: Final = 5.0  # [Set]
FAN_WORKING_ORDER: Final = ("auto", "medium", "mid", "level 3", "3", "high")  # [Set]
FAN_RESTING_ORDER: Final = ("quiet", "silent", "low", "min", "level 1", "1")  # [Set]
# "the settled vertical position" (delivery.py): the specification names no
# positions; these are 0.9.7's settled list (decision of 28 October 2026) [Set]
VANE_SETTLED_ORDER: Final = ("off", "hold", "fixed", "middle", "center")  # [Set]
RANGE_HALF_WIDTH_C: Final = 1.0  # a range-only unit gets setpoint -1.0 to +1.0 C [D024]
MIN_RUN_S: Final = 10 * 60.0  # [D016]
MIN_REST_S: Final = 5 * 60.0  # [D016]

# --------------------------------------------------------------------------
# Act and Evidence (Modules 8-9)
# --------------------------------------------------------------------------
COMMANDS_KEPT: Final = 200  # [D052]
NEAR_CENTRE: Final = 0.3  # [Prop]

# --------------------------------------------------------------------------
# Memory (Module 10)
# --------------------------------------------------------------------------
STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1
RECORDER_KEEP_DAYS_DEFAULT: Final = 10  # [HA]
