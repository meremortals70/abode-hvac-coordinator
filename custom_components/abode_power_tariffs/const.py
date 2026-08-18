"""Constants for Abode Power Tariffs."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "abode_power_tariffs"

# Config entry data
CONF_PLAN_NAME: Final = "plan_name"
CONF_PLAN_DESCRIPTION: Final = "plan_description"
CONF_ADD_ANOTHER: Final = "add_another"
CONF_GO_BACK: Final = "go_back"
CONF_ON_SUBMIT: Final = "on_submit"
SUBMIT_ADD: Final = "add"
SUBMIT_CONTINUE: Final = "continue"
SUBMIT_ADD_PATTERN: Final = "add_pattern"
SUBMIT_BACK: Final = "back"

# Config entry options — top level
CONF_RATES: Final = "rates"
CONF_DAY_PATTERNS: Final = "day_patterns"
CONF_SUPPLY_CHARGE_CENTS: Final = "daily_supply_charge_cents"
CONF_MONTHLY_CHARGE: Final = "monthly_charge"
CONF_PRICES_INCLUDE_GST: Final = "prices_include_gst"
CONF_GST_PERCENT: Final = "gst_percent"
CONF_VALID_FROM: Final = "valid_from"
CONF_VALID_TO: Final = "valid_to"
CONF_HOLIDAY_SENSOR: Final = "holiday_sensor"
CONF_IMPORT_ENERGY_SENSOR: Final = "import_energy_sensor"
# Counting usage against a cap is a separate, opt-in thing. The plan always
# declares the cap and what is paid past it; whether this component keeps a
# running total is the user's choice, and it is an estimate either way.
CONF_COUNT_ALLOWANCE: Final = "count_allowance"
CONF_TARIFF_SELECTS: Final = "tariff_selects"
CONF_SOURCE_ENERGY_SENSOR: Final = "source_energy_sensor"
CONF_SUPPLY_CHARGE_ENTITIES: Final = "supply_charge_entities"

# Rate keys
CONF_NAME: Final = "name"
# Which timetable a rate belongs to. Rates are identified by the pair, so
# two timetables can each have a Peak at different prices.
CONF_TIMETABLE: Final = "timetable"
CONF_IMPORT_CENTS: Final = "import_cents"
CONF_EXPORT_CENTS: Final = "export_cents"
CONF_EXPORT_RATES: Final = "export_rates"
CONF_EXPORT_PERIODS: Final = "export_periods"
CONF_EXPORT_SAME_ALL_DAY: Final = "export_same_all_day"
CONF_EXPORT_FLAT_CENTS: Final = "export_flat_cents"
CONF_DEMAND_RATE: Final = "demand_rate_per_kw_month"
CONF_CONSTRAINTS: Final = "constraints"
# The subset of the above the user has declared other systems should treat as
# a rule rather than a hint. This component still enforces nothing.
CONF_ENFORCEABLE_CONSTRAINTS: Final = "enforceable_constraints"
# Form only, never stored. The rate form asks for the two lists separately so
# nothing has to be typed twice; what is stored is the union plus the
# enforceable subset, so anything reading the flat list sees what it always saw.
CONF_INFORMATION_CONSTRAINTS: Final = "information_constraints"

# Seeded so the dropdown is never empty. These are the names Abode HVAC
# Coordinator already acts on; anything else is the user's own and is typed
# straight into the same control.
CONSTRAINT_NO_GRID_IMPORT: Final = "no_grid_import"
CONSTRAINT_PRECOOL_OPPORTUNITY: Final = "precool_opportunity"
CONSTRAINT_GRID_CHARGE_BATTERY: Final = "grid_charge_battery"

KNOWN_CONSTRAINTS: Final = (
    CONSTRAINT_GRID_CHARGE_BATTERY,
    CONSTRAINT_NO_GRID_IMPORT,
    CONSTRAINT_PRECOOL_OPPORTUNITY,
)
CONF_COASTING_PERMITTED: Final = "coasting_permitted"
CONF_DAILY_ALLOWANCE_KWH: Final = "daily_allowance_kwh"
CONF_EXPORT_ALLOWANCE_KWH: Final = "export_allowance_kwh"
CONF_FALLBACK_RATE: Final = "fallback_rate"
CONF_DEMAND_PERIOD: Final = "demand_period"
CONF_COMPONENTS: Final = "price_components"

# Day set keys
CONF_DAYS: Final = "days"
CONF_SEASON_FROM: Final = "season_from"
CONF_SEASON_TO: Final = "season_to"
CONF_PERIODS: Final = "periods"
CONF_SAME_EVERY_DAY: Final = "same_every_day"

# Utility meter creation
UTILITY_METER_DOMAIN: Final = "utility_meter"
UM_CONF_NAME: Final = "name"
UM_CONF_SOURCE: Final = "source"
UM_CONF_CYCLE: Final = "cycle"
UM_CONF_OFFSET: Final = "offset"
UM_CONF_TARIFFS: Final = "tariffs"
UM_CONF_NET_CONSUMPTION: Final = "net_consumption"
UM_CONF_DELTA_VALUES: Final = "delta_values"
UM_CONF_PERIODICALLY_RESETTING: Final = "periodically_resetting"
UM_CONF_ALWAYS_AVAILABLE: Final = "always_available"
UM_CYCLE_MONTHLY: Final = "monthly"

# Period keys
CONF_START: Final = "start"
CONF_END: Final = "end"
CONF_RATE: Final = "rate"

# Service
SERVICE_GET_INTERVALS: Final = "get_intervals"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_HOURS: Final = "hours"
ATTR_RESOLUTION_MINUTES: Final = "resolution_minutes"

DEFAULT_HOURS: Final = 24
DEFAULT_RESOLUTION_MINUTES: Final = 30
DEFAULT_GST_PERCENT: Final = 10.0

# Day tokens. Public holidays are a day type in their own right.
DAY_MON: Final = "mon"
DAY_TUE: Final = "tue"
DAY_WED: Final = "wed"
DAY_THU: Final = "thu"
DAY_FRI: Final = "fri"
DAY_SAT: Final = "sat"
DAY_SUN: Final = "sun"
DAY_HOLIDAY: Final = "holiday"

WEEKDAY_TOKENS: Final = (DAY_MON, DAY_TUE, DAY_WED, DAY_THU, DAY_FRI, DAY_SAT, DAY_SUN)
ALL_DAY_TOKENS: Final = (*WEEKDAY_TOKENS, DAY_HOLIDAY)

MINUTES_PER_DAY: Final = 1440

SIGNAL_UPDATE: Final = f"{DOMAIN}_update"
