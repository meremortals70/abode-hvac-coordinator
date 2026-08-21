"""Configuration and options flow.

Setup collects the plan. Nothing is invented and no entity publishes a price
that the user did not enter.

The 24-hour strip is shown before anything is chosen and again on every screen
that touches time periods, so a gap or an overlap is visible the moment it is
made.
"""

from __future__ import annotations

import copy
import functools
import logging
import traceback
from collections.abc import Awaitable, Callable
from typing import Any, Final

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    ALL_DAY_TOKENS,
    CONF_AFTER_ALLOWANCE_CENTS,
    CONF_BILLING_CYCLE_DAY,
    CONF_CONSTRAINTS,
    CONF_COUNT_ALLOWANCE,
    CONF_DAY_PATTERNS,
    CONF_DAYS,
    CONF_DEMAND_PERIOD,
    CONF_DEMAND_RATE,
    CONF_END,
    CONF_ENFORCEABLE_CONSTRAINTS,
    CONF_EXPORT_ALLOWANCE_KWH,
    CONF_EXPORT_CENTS,
    CONF_EXPORT_FALLBACK_CENTS,
    CONF_EXPORT_FLAT_CENTS,
    CONF_EXPORT_PERIODS,
    CONF_EXPORT_RATES,
    CONF_EXPORT_SAME_ALL_DAY,
    CONF_FALLBACK_RATE,
    CONF_GST_PERCENT,
    CONF_HAS_EXPORT,
    CONF_HOLIDAY_SENSOR,
    CONF_IMPORT_CENTS,
    CONF_IMPORT_ENERGY_SENSOR,
    CONF_INFORMATION_CONSTRAINTS,
    CONF_MONTHLY_CHARGE,
    CONF_NAME,
    CONF_ON_SUBMIT,
    CONF_PERIODS,
    CONF_PLAN_DESCRIPTION,
    CONF_PLAN_NAME,
    CONF_PRICES_INCLUDE_GST,
    CONF_RATE,
    CONF_RATE_ALLOWANCE_KWH,
    CONF_RATES,
    CONF_SEASON_FROM,
    CONF_SEASON_TO,
    CONF_SINGLE_RATE,
    CONF_SOURCE_ENERGY_SENSOR,
    CONF_START,
    CONF_SUPPLY_CHARGE_CENTS,
    CONF_TARIFF_SELECTS,
    CONF_TIMETABLE,
    CONF_VALID_FROM,
    CONF_VALID_TO,
    DEFAULT_GST_PERCENT,
    DOMAIN,
    KNOWN_CONSTRAINTS,
    MAX_BILLING_CYCLE_DAY,
    MINUTES_PER_DAY,
    SECTION_ALLOWANCE,
    SECTION_CONSTRAINTS,
    SECTION_DEMAND,
    SUBMIT_ADD,
    SUBMIT_CONTINUE,
    UM_CONF_ALWAYS_AVAILABLE,
    UM_CONF_CYCLE,
    UM_CONF_DELTA_VALUES,
    UM_CONF_NAME,
    UM_CONF_NET_CONSUMPTION,
    UM_CONF_OFFSET,
    UM_CONF_PERIODICALLY_RESETTING,
    UM_CONF_SOURCE,
    UM_CONF_TARIFFS,
    UM_CYCLE_MONTHLY,
    UTILITY_METER_DOMAIN,
    WEEKDAY_TOKENS,
)
from .plan import (
    DayPattern,
    ExportRate,
    Period,
    Plan,
    PlanError,
    Rate,
    format_time,
    merged,
    parse_time,
    slug,
)
from .strip import render_day_pattern, render_plan, render_rate_plan_card
from .validate import plan_warnings, validate_plan

_LOGGER = logging.getLogger(__name__)

EVERY_DAY = "Every day"


def _time_to_minutes(value: str, *, is_end: bool) -> int:
    """Convert a time selector value to minutes since midnight.

    A period ending at midnight is the end of the day, not the start of it, so
    an end of 00:00 becomes 24:00. There is no other way to say it with a time
    picker.
    """
    trimmed = ":".join(str(value).split(":")[:2])
    minutes = parse_time(trimmed)
    if is_end and minutes == 0:
        return MINUTES_PER_DAY
    return minutes


def _minutes_to_selector(minutes: int) -> str:
    """Convert minutes since midnight to a time selector value."""
    if minutes >= MINUTES_PER_DAY:
        return "00:00:00"
    return f"{format_time(minutes)}:00"


def _covered_minutes(periods: list[dict[str, Any]]) -> int:
    """Return how many minutes of the day these periods account for."""
    return sum(
        parse_time(str(period[CONF_END])) - parse_time(str(period[CONF_START]))
        for period in periods
    )


def _rate_record(user_input: dict[str, Any]) -> dict[str, Any]:
    """Return the rate fields this form asked about, and only those.

    A screen writes what it asked. What it did not ask about is not its to
    decide: it belongs to the record already stored, and `plan.merged` carries
    it through. Writing a key the form never showed is how editing a rate's
    price came to blank the fields beside it.

    The form asks for the two rule lists separately so nothing has to be typed
    twice. What is stored is the union plus the enforceable subset, so anything
    reading the flat list sees exactly what it always saw.
    """
    record: dict[str, Any] = {
        CONF_NAME: str(user_input.get(CONF_NAME) or "").strip(),
        CONF_IMPORT_CENTS: user_input[CONF_IMPORT_CENTS],
    }
    # Setup does not show the timetable: it is entering one timetable at a
    # time and sets the field itself, afterwards.
    if CONF_TIMETABLE in user_input:
        record[CONF_TIMETABLE] = _timetable_from(user_input)
    if {CONF_INFORMATION_CONSTRAINTS, CONF_ENFORCEABLE_CONSTRAINTS} & set(user_input):
        informational = _rules_from(user_input, CONF_INFORMATION_CONSTRAINTS)
        enforceable = _rules_from(user_input, CONF_ENFORCEABLE_CONSTRAINTS)
        record[CONF_CONSTRAINTS] = sorted({*informational, *enforceable})
        record[CONF_ENFORCEABLE_CONSTRAINTS] = sorted(enforceable)
    if CONF_DEMAND_PERIOD in user_input:
        record[CONF_DEMAND_PERIOD] = bool(user_input.get(CONF_DEMAND_PERIOD, False))
        record[CONF_DEMAND_RATE] = user_input.get(CONF_DEMAND_RATE) or 0.0
    if CONF_RATE_ALLOWANCE_KWH in user_input:
        allowance = user_input.get(CONF_RATE_ALLOWANCE_KWH) or None
        record[CONF_RATE_ALLOWANCE_KWH] = allowance
        # A fallback only means something with a cap to fall past it. The
        # select always carries a value, so storing it either way would put a
        # rate the user never chose onto every uncapped rate.
        record[CONF_FALLBACK_RATE] = (
            user_input.get(CONF_FALLBACK_RATE) or None if allowance else None
        )
    return record


UNSCOPED_TIMETABLE: Final = "(not set - older plan)"


def _timetable_from(user_input: dict[str, Any]) -> str | None:
    """Read the timetable off the form, if the form asked for one.

    The setup form does not: it is entering one timetable at a time and sets
    the field itself. The sentinel means a rate stored before the scoping
    existed, which keeps the unique name it was given.
    """
    chosen = str(user_input.get(CONF_TIMETABLE) or "").strip()
    if not chosen or chosen == UNSCOPED_TIMETABLE:
        return None
    return chosen


def rate_id(rate: dict[str, Any]) -> str:
    """Return the identifier a stored rate will be published under."""
    timetable = rate.get(CONF_TIMETABLE)
    name = str(rate.get(CONF_NAME, ""))
    return f"{slug(str(timetable))}.{slug(name)}" if timetable else name


def _rules_from(user_input: dict[str, Any], key: str) -> list[str]:
    """Read one rules multi-select, which also accepts a typed value."""
    seen: list[str] = []
    for item in user_input.get(key) or []:
        rule = str(item).strip()
        if rule and rule not in seen:
            seen.append(rule)
    return seen


def _counting_without_meter(user_input: dict[str, Any]) -> bool:
    """Return whether counting was asked for with no meter to count.

    Nothing on the rate form is required until the box is ticked. Ticking it is
    a choice the component cannot honour without a sensor, so at that point the
    sensor becomes required — which is what asking the minimum means here,
    rather than the field not being offered at all.
    """
    if not user_input.get(CONF_COUNT_ALLOWANCE):
        return False
    return not user_input.get(CONF_IMPORT_ENERGY_SENSOR)


def _demand_without_rate(user_input: dict[str, Any]) -> bool:
    """Return whether a demand period was declared with no rate attached.

    The same shape as counting without a meter: nothing about the demand rate
    is required until the demand period box is ticked, and ticking it is a
    choice this component cannot honour without a number to publish, so at
    that point the rate becomes required.
    """
    if not user_input.get(CONF_DEMAND_PERIOD):
        return False
    return not user_input.get(CONF_DEMAND_RATE)


def _rules_in_both_lists(user_input: dict[str, Any]) -> set[str]:
    """Return rules put in both lists, which is a contradiction to resolve."""
    return set(_rules_from(user_input, CONF_INFORMATION_CONSTRAINTS)) & set(
        _rules_from(user_input, CONF_ENFORCEABLE_CONSTRAINTS)
    )


def known_constraints(rates: list[dict[str, Any]]) -> list[str]:
    """Every rule already used by these rates, so it can be picked not retyped.

    Shared by the setup form and the edit form. Two copies of this is how the
    same rule ends up spelled two different ways in one plan.
    """
    found: list[str] = []
    for rate in rates:
        for key in (CONF_CONSTRAINTS, CONF_ENFORCEABLE_CONSTRAINTS):
            for rule in rate.get(key) or []:
                if str(rule) not in found:
                    found.append(str(rule))
    return found


def _require_name(schema: vol.Schema) -> vol.Schema:
    """Make the name required again, for forms that have no Go back."""
    rebuilt: dict[Any, Any] = {}
    for key, value in schema.schema.items():
        if getattr(key, "schema", None) == CONF_NAME:
            rebuilt[vol.Required(CONF_NAME, default=key.default())] = value
        else:
            rebuilt[key] = value
    return vol.Schema(rebuilt)


# What the first-run form offers. Asking the minimum means little is required,
# not that a field is withheld: an allowance declared during setup should not
# have to be declared again in Configure. The two rule lists are here because
# they are the only rate fields that create entities, and the allowance fields
# because a cap is part of what the rate is.
SETUP_RATE_FIELDS: Final = (
    CONF_NAME,
    CONF_IMPORT_CENTS,
    CONF_INFORMATION_CONSTRAINTS,
    CONF_ENFORCEABLE_CONSTRAINTS,
    CONF_DEMAND_PERIOD,
    CONF_DEMAND_RATE,
    CONF_RATE_ALLOWANCE_KWH,
    CONF_FALLBACK_RATE,
    CONF_COUNT_ALLOWANCE,
    CONF_IMPORT_ENERGY_SENSOR,
)


def _field_name(key: Any) -> str:
    """Return the plain key behind a voluptuous marker."""
    return str(getattr(key, "schema", key))


def _rules_selector(offered: list[str]) -> selector.SelectSelector:
    """A multi-select seeded with the known rules that also takes a typed one."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=offered,
            multiple=True,
            custom_value=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _rate_schema(
    existing: dict[str, Any],
    fallback_options: list[str],
    *,
    fields: tuple[str, ...] | None = None,
    known_constraints: list[str] | None = None,
    timetables: list[str] | None = None,
    count_allowance: bool = False,
    energy_sensor: str | None = None,
) -> vol.Schema:
    """Return the rate form.

    Every field is defined once, here. ``fields`` chooses which of them a
    screen shows; it never changes how one behaves. The setup form and the edit
    form therefore cannot drift apart — asking for less is the only difference
    between them. They used to be two schemas, and the setup one quietly wrote
    a default over everything it had not asked about.
    """
    stored = [str(item) for item in existing.get(CONF_CONSTRAINTS) or []]
    enforceable = [
        str(item) for item in existing.get(CONF_ENFORCEABLE_CONSTRAINTS) or []
    ]
    # A plan written before the distinction existed has rules but nothing
    # marked enforceable, so they all load as information only.
    informational = [rule for rule in stored if rule not in enforceable]
    offered = sorted(
        dict.fromkeys([*KNOWN_CONSTRAINTS, *(known_constraints or []), *stored])
    )

    schema: dict[Any, Any] = {
        vol.Optional(
            CONF_NAME, default=existing.get(CONF_NAME, "")
        ): selector.TextSelector(),
    }
    if timetables:
        current = existing.get(CONF_TIMETABLE)
        offered_timetables = list(timetables)
        if not current and existing:
            # Stored before the scoping existed. Offer leaving it alone rather
            # than forcing a change that would rename its entity.
            offered_timetables = [UNSCOPED_TIMETABLE, *offered_timetables]
        schema[
            vol.Required(
                CONF_TIMETABLE,
                default=current or offered_timetables[0],
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=offered_timetables)
        )
    schema |= {
        vol.Required(
            CONF_IMPORT_CENTS, default=float(existing.get(CONF_IMPORT_CENTS) or 0.0)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=1000, step=0.01, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Optional(
            CONF_INFORMATION_CONSTRAINTS, default=informational
        ): _rules_selector(offered),
        vol.Optional(
            CONF_ENFORCEABLE_CONSTRAINTS, default=enforceable
        ): _rules_selector(offered),
    }
    schema[
        vol.Required(
            CONF_DEMAND_PERIOD, default=bool(existing.get(CONF_DEMAND_PERIOD, False))
        )
    ] = selector.BooleanSelector()
    schema[
        vol.Optional(
            CONF_DEMAND_RATE,
            default=float(existing.get(CONF_DEMAND_RATE) or 0.0),
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=1000, step="any", mode=selector.NumberSelectorMode.BOX
        )
    )
    schema[
        vol.Required(
            CONF_RATE_ALLOWANCE_KWH,
            default=float(existing.get(CONF_RATE_ALLOWANCE_KWH) or 0.0),
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=1000, step=0.1, mode=selector.NumberSelectorMode.BOX
        )
    )
    # No export fields here. An export allowance belongs beside the export
    # price it caps, which is on the feed-in screens; import and export are
    # separate flows and this is an import rate.
    if fallback_options:
        schema[
            vol.Required(
                CONF_FALLBACK_RATE,
                default=existing.get(CONF_FALLBACK_RATE) or fallback_options[0],
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=fallback_options)
        )
    # Counting sits with the cap, because it is the same decision: the user
    # declares an allowance and says in the same breath whether this component
    # should watch a meter against it and switch to the fallback once it is
    # spent. It used to be a screen of its own, which is how an allowance came
    # to be declared in one place and counted in another.
    #
    # Both values are the plan's rather than the rate's. There is one grid
    # import meter, so the sensor is one answer shown wherever a cap is; a
    # later rate finds it already filled in.
    schema[vol.Required(CONF_COUNT_ALLOWANCE, default=count_allowance)] = (
        selector.BooleanSelector()
    )
    schema[
        vol.Optional(
            CONF_IMPORT_ENERGY_SENSOR, description={"suggested_value": energy_sensor}
        )
    ] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class="energy")
    )
    if fields is not None:
        schema = {
            key: value for key, value in schema.items() if _field_name(key) in fields
        }
    return vol.Schema(_in_sections(schema))


# Which fields belong in which collapsible group, in the order they appear.
# Name and price are not in one: they are what every rate has, and the form
# opens on them.
RATE_SECTIONS: Final = (
    (SECTION_DEMAND, (CONF_DEMAND_PERIOD, CONF_DEMAND_RATE)),
    (
        SECTION_ALLOWANCE,
        (
            CONF_RATE_ALLOWANCE_KWH,
            CONF_FALLBACK_RATE,
            CONF_COUNT_ALLOWANCE,
            CONF_IMPORT_ENERGY_SENSOR,
        ),
    ),
    (
        SECTION_CONSTRAINTS,
        (CONF_INFORMATION_CONSTRAINTS, CONF_ENFORCEABLE_CONSTRAINTS),
    ),
)


def _in_sections(schema: dict[Any, Any]) -> dict[Any, Any]:
    """Group the rate form's fields into collapsible sections.

    Home Assistant has no conditional field visibility: a form is rendered
    from a schema handed over once and the frontend does not ask again when a
    box is ticked. What it does have is a named group that opens closed, and
    opening one and filling it in is itself the declaration — so a demand
    charge and an allowance no longer run together in one undivided list with
    nothing between them.

    Every section starts collapsed. A rate that has none of these things is
    then a name and a price, which is what most rates are.
    """
    grouped: dict[Any, Any] = {}
    claimed: set[str] = set()
    for name, members in RATE_SECTIONS:
        inner = {
            key: value for key, value in schema.items() if _field_name(key) in members
        }
        if not inner:
            continue
        claimed |= {_field_name(key) for key in inner}
        grouped[vol.Required(name)] = section(vol.Schema(inner), {"collapsed": True})
    loose = {
        key: value for key, value in schema.items() if _field_name(key) not in claimed
    }
    return {**loose, **grouped}


def flatten_sections(user_input: dict[str, Any]) -> dict[str, Any]:
    """Return a sectioned form's payload as one flat mapping.

    A section nests what it holds, so everything that reads the rate form
    would otherwise have to know which section each field sits in. It does
    not: a field means the same thing wherever it is shown.
    """
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def on_submit(
    key: str, default: str = SUBMIT_ADD, *, options: list[str] | None = None
) -> dict[Any, Any]:
    """Return the 'When I submit' choice for one screen.

    A form has one button, so what that button does has to be a field. Each
    screen names its own choices: on the rates screen it says add another rate
    or continue to the time periods, not 'add' and 'continue'.
    """
    return {
        vol.Required(CONF_ON_SUBMIT, default=default): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=options or [SUBMIT_ADD, SUBMIT_CONTINUE],
                mode=selector.SelectSelectorMode.LIST,
                translation_key=key,
            )
        )
    }


# Where each step goes when the user ticks Go back.
PREVIOUS_STEP = {
    "charges": "user",
    "rates": "charges",
    "days": "rates",
    "periods": "days",
    "export_rates": "periods",
    "export_periods": "export_rates",
}


def guarded_setup(func: Any) -> Any:
    """Show the failure on screen instead of 'Unknown error occurred'."""

    @functools.wraps(func)
    async def wrapper(
        self: AbodePowerTariffsConfigFlow, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            return await func(self, user_input)
        except Exception:  # Deliberate: nothing may escape into an empty dialog
            _LOGGER.exception("Setup step %s failed", func.__name__)
            self._failure = traceback.format_exc()
            return await self.async_step_setup_failure()

    return wrapper


class AbodePowerTariffsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Collect the whole plan: name, supply charge, rates, days, time periods.

    Nothing is created that the user did not enter.
    """

    def __init__(self) -> None:
        """Start with nothing."""
        self._name: str = ""
        self._description: str = ""
        self._supply_charge: float = 0.0
        self._include_gst: bool = True
        self._gst_percent: float = DEFAULT_GST_PERCENT
        self._billing_cycle_day: int | None = None
        self._single_rate: bool = False
        self._has_export: bool = False
        # Plan-level, collected on the rate screen beside the cap they belong to.
        self._count_allowance: bool = False
        self._energy_sensor: str | None = None
        self._monthly_charge: float = 0.0
        self._patterns: list[dict[str, Any]] = []
        self._export_same_all_day: bool = True
        self._export_flat: float = 0.0
        self._export_allowance: float | None = None
        self._export_fallback: float | None = None
        self._export_rates: list[dict[str, Any]] = []
        self._export_periods: list[dict[str, Any]] = []
        self._rates: list[dict[str, Any]] = []
        self._pattern_name: str = EVERY_DAY
        self._pattern_days: list[str] = list(ALL_DAY_TOKENS)
        self._periods: list[dict[str, Any]] = []
        self._failure: str = ""

    async def async_step_setup_failure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what went wrong, in full, where it can be copied."""
        if user_input is not None:
            return self.async_abort(reason="setup_failed")
        return self.async_show_form(
            step_id="setup_failure",
            data_schema=vol.Schema({}),
            description_placeholders={"detail": self._failure or "No detail captured."},
        )

    def _pattern_rates(self) -> list[dict[str, Any]]:
        """Return the import rates belonging to the timetable being entered."""
        return [
            rate
            for rate in self._rates
            if rate.get(CONF_TIMETABLE) == self._pattern_name
        ]

    def _pattern_export_rates(self) -> list[dict[str, Any]]:
        """Return the feed-in rates belonging to the timetable being entered."""
        prefix = f"{self._pattern_name} "
        return [
            rate for rate in self._export_rates if rate[CONF_NAME].startswith(prefix)
        ]

    def _store_pattern(self) -> None:
        """Put the timetable just entered into the plan and start clean."""
        self._patterns.append(
            merged(
                DayPattern,
                None,
                {
                    CONF_NAME: self._pattern_name,
                    CONF_DAYS: self._pattern_days,
                    CONF_PERIODS: self._periods,
                    CONF_EXPORT_PERIODS: self._export_periods,
                    CONF_EXPORT_SAME_ALL_DAY: self._export_same_all_day,
                    CONF_EXPORT_FLAT_CENTS: self._export_flat,
                    CONF_EXPORT_ALLOWANCE_KWH: self._export_allowance,
                    CONF_EXPORT_FALLBACK_CENTS: self._export_fallback,
                },
            )
        )
        self._pattern_name = ""
        self._pattern_days = []
        self._periods = []
        self._export_periods = []
        self._export_same_all_day = True
        self._export_flat = 0.0
        self._export_allowance = None
        self._export_fallback = None

    # ---------------------------------------------------------------- 1 name

    @guarded_setup
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the plan, and choose its shape before anything else."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = str(user_input[CONF_PLAN_NAME]).strip()
            if not name:
                errors[CONF_PLAN_NAME] = "name_required"
            else:
                await self.async_set_unique_id(slugify(name))
                self._abort_if_unique_id_configured()
                self._name = name
                self._description = str(
                    user_input.get(CONF_PLAN_DESCRIPTION) or ""
                ).strip()
                self._single_rate = bool(user_input.get(CONF_SINGLE_RATE))
                self._has_export = bool(user_input.get(CONF_HAS_EXPORT))
                return await self.async_step_charges()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PLAN_NAME, default="Electricity"
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_PLAN_DESCRIPTION, default=""
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    # No clock involved: one rate for the first N kWh, then
                    # another for the rest of the billing period, rather than
                    # rates that apply at particular times of day.
                    vol.Required(
                        CONF_SINGLE_RATE, default=False
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_HAS_EXPORT, default=False
                    ): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------- 2 charges

    @guarded_setup
    async def async_step_charges(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the fixed charge and the tax treatment, before any rate."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._supply_charge = float(user_input[CONF_SUPPLY_CHARGE_CENTS])
            self._include_gst = bool(user_input[CONF_PRICES_INCLUDE_GST])
            self._gst_percent = float(user_input[CONF_GST_PERCENT])
            self._monthly_charge = float(user_input[CONF_MONTHLY_CHARGE])
            day = int(user_input.get(CONF_BILLING_CYCLE_DAY) or 0)
            if day > MAX_BILLING_CYCLE_DAY:
                # A cycle starts on the same day every month, so the day has to
                # be one every month has.
                errors[CONF_BILLING_CYCLE_DAY] = "billing_day_out_of_range"
            else:
                self._billing_cycle_day = day or None
                if self._single_rate:
                    return await self.async_step_single_rate()
                return await self.async_step_days()

        return self.async_show_form(
            step_id="charges",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SUPPLY_CHARGE_CENTS, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_MONTHLY_CHARGE, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_BILLING_CYCLE_DAY, default=0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=31,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_PRICES_INCLUDE_GST, default=True
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_GST_PERCENT, default=DEFAULT_GST_PERCENT
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=0.1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"plan": self._name},
        )

    # ------------------------------------------------------- 2a single rate

    @guarded_setup
    async def async_step_single_rate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """No clock: an amount for the first N kWh, then a price for the rest.

        Declared the same way any capped rate declares its allowance and
        fallback — nothing here counts usage yet. Built as an ordinary
        all-day rate underneath, so it reads and resolves exactly like any
        other plan; the shortcut is only in how it was entered.
        """
        if user_input is not None:
            included: dict[str, Any] = {
                CONF_NAME: "Included",
                CONF_TIMETABLE: EVERY_DAY,
                CONF_IMPORT_CENTS: user_input[CONF_IMPORT_CENTS],
                CONF_RATE_ALLOWANCE_KWH: user_input.get(CONF_RATE_ALLOWANCE_KWH)
                or None,
                CONF_FALLBACK_RATE: "Additional",
            }
            additional: dict[str, Any] = {
                CONF_NAME: "Additional",
                CONF_TIMETABLE: EVERY_DAY,
                CONF_IMPORT_CENTS: user_input[CONF_AFTER_ALLOWANCE_CENTS],
            }
            export_flat = 0.0
            export_allowance: float | None = None
            export_fallback: float | None = None
            if self._has_export:
                export_flat = float(user_input.get(CONF_EXPORT_FLAT_CENTS) or 0.0)
                # Beside the export price, not on the import rate. A
                # single-rate plan's feed-in is one price all day, so the
                # declaration lives on the timetable that carries it.
                export_allowance = user_input.get(CONF_EXPORT_ALLOWANCE_KWH) or None
                export_fallback = user_input.get(CONF_EXPORT_FALLBACK_CENTS) or None
            self._rates = [
                merged(Rate, None, included),
                merged(Rate, None, additional),
            ]
            self._patterns = [
                merged(
                    DayPattern,
                    None,
                    {
                        CONF_NAME: EVERY_DAY,
                        CONF_DAYS: list(ALL_DAY_TOKENS),
                        CONF_PERIODS: [
                            merged(
                                Period,
                                None,
                                {
                                    CONF_START: "00:00",
                                    CONF_END: "24:00",
                                    CONF_RATE: "Included",
                                },
                            )
                        ],
                        CONF_EXPORT_PERIODS: [],
                        CONF_EXPORT_SAME_ALL_DAY: True,
                        CONF_EXPORT_FLAT_CENTS: export_flat,
                        CONF_EXPORT_ALLOWANCE_KWH: export_allowance,
                        CONF_EXPORT_FALLBACK_CENTS: export_fallback,
                    },
                )
            ]
            return await self.async_step_finish()

        schema: dict[Any, Any] = {
            vol.Required(CONF_IMPORT_CENTS, default=0.0): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1000, step=0.01, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(CONF_RATE_ALLOWANCE_KWH, default=0.0): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1000, step=0.1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_AFTER_ALLOWANCE_CENTS, default=0.0
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1000, step=0.01, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
        if self._has_export:
            schema[vol.Required(CONF_EXPORT_FLAT_CENTS, default=0.0)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1000, step=0.01, mode=selector.NumberSelectorMode.BOX
                    )
                )
            )
            schema[vol.Required(CONF_EXPORT_ALLOWANCE_KWH, default=0.0)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1000, step=0.1, mode=selector.NumberSelectorMode.BOX
                    )
                )
            )
            schema[vol.Required(CONF_EXPORT_FALLBACK_CENTS, default=0.0)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1000, step=0.01, mode=selector.NumberSelectorMode.BOX
                    )
                )
            )

        return self.async_show_form(
            step_id="single_rate",
            data_schema=vol.Schema(schema),
            description_placeholders={"plan": self._name},
        )

    # --------------------------------------------------------------- 4 rates

    @guarded_setup
    async def async_step_rates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Every rate in the plan, whenever it applies."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = flatten_sections(user_input)
            action = user_input[CONF_ON_SUBMIT]
            # A form has one button, so what it does is a field. Choosing to
            # move on used to return here and throw away whatever had been
            # typed on the screen, silently. Now only a blank screen moves on
            # untouched; anything filled in is checked and kept first.
            if (
                action == SUBMIT_CONTINUE
                and not str(user_input.get(CONF_NAME) or "").strip()
                and self._pattern_rates()
            ):
                return await self.async_step_periods()
            record = _rate_record(user_input)
            typed = record[CONF_NAME]
            # The rate belongs to this timetable, and says so in a field rather
            # than by having the timetable's name pushed onto the front of its
            # own. That is what lets a weekend Peak sit alongside a weekday
            # Peak while both are still called Peak.
            record[CONF_TIMETABLE] = self._pattern_name
            if not typed:
                errors[CONF_NAME] = "name_required"
            elif any(
                rate[CONF_NAME] == typed
                and rate.get(CONF_TIMETABLE) == self._pattern_name
                for rate in self._rates
            ):
                errors[CONF_NAME] = "rate_exists"
            elif _rules_in_both_lists(user_input):
                errors[CONF_ENFORCEABLE_CONSTRAINTS] = "rule_in_both_lists"
            elif _counting_without_meter(user_input):
                errors[CONF_IMPORT_ENERGY_SENSOR] = "energy_sensor_required"
            elif _demand_without_rate(user_input):
                errors[CONF_DEMAND_RATE] = "demand_rate_required"
            else:
                # Plan-level, not the rate's: one grid meter, one answer.
                self._count_allowance = bool(user_input.get(CONF_COUNT_ALLOWANCE))
                self._energy_sensor = user_input.get(CONF_IMPORT_ENERGY_SENSOR) or None
                self._rates.append(merged(Rate, None, record))
                if action == SUBMIT_CONTINUE:
                    return await self.async_step_periods()
                return await self.async_step_rates()

        schema = {
            **_rate_schema(
                {},
                [str(rate.get(CONF_NAME, "")) for rate in self._pattern_rates()],
                fields=SETUP_RATE_FIELDS,
                known_constraints=known_constraints(self._rates),
                count_allowance=self._count_allowance,
                energy_sensor=self._energy_sensor,
            ).schema,
            **on_submit("submit_rates"),
        }

        return self.async_show_form(
            step_id="rates",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "plan": self._name,
                "pattern": self._pattern_name,
                "so_far": ", ".join(rate[CONF_NAME] for rate in self._pattern_rates())
                or "none yet",
            },
        )

    # ---------------------------------------------------------------- 3 days

    @guarded_setup
    async def async_step_days(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Which days this timetable covers."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            days = (
                list(ALL_DAY_TOKENS)
                if user_input["same_every_day"]
                else list(user_input.get(CONF_DAYS) or [])
            )
            if not name:
                errors[CONF_NAME] = "name_required"
            elif not days:
                errors[CONF_DAYS] = "days_required"
            else:
                self._pattern_name = name
                self._pattern_days = days
                return await self.async_step_rates()

        return self.async_show_form(
            step_id="days",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME,
                        default=EVERY_DAY if not self._patterns else "Weekend",
                    ): selector.TextSelector(),
                    vol.Required(
                        "same_every_day", default=not self._patterns
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_DAYS, default=list(WEEKDAY_TOKENS)
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(ALL_DAY_TOKENS),
                            multiple=True,
                            translation_key="day_tokens",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------- 5 periods

    @guarded_setup
    async def async_step_periods(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """When each rate applies, until the day is covered."""
        errors: dict[str, str] = {}
        names = [rate[CONF_NAME] for rate in self._pattern_rates()]
        covered = _covered_minutes(self._periods)

        if user_input is not None:
            action = user_input[CONF_ON_SUBMIT]
            # Moving on is a gate, not a preference. A plan with an uncovered
            # stretch of day is not a valid plan, and the screen said so; it
            # just did not refuse. A period cannot be blank, so there is
            # nothing to move on from except a day already accounted for.
            if action == SUBMIT_CONTINUE and covered >= MINUTES_PER_DAY:
                return await self.async_step_feed_in()
            try:
                start = _time_to_minutes(str(user_input[CONF_START]), is_end=False)
                end = _time_to_minutes(str(user_input[CONF_END]), is_end=True)
            except PlanError:
                errors["base"] = "bad_time"
            else:
                if end <= start:
                    errors[CONF_END] = "end_before_start"
                elif any(
                    start < parse_time(existing[CONF_END])
                    and end > parse_time(existing[CONF_START])
                    for existing in self._periods
                ):
                    errors["base"] = "period_overlaps"
                else:
                    self._periods.append(
                        merged(
                            Period,
                            None,
                            {
                                CONF_START: format_time(start),
                                CONF_END: format_time(end),
                                CONF_RATE: str(user_input[CONF_RATE]),
                            },
                        )
                    )
                    if (
                        action == SUBMIT_CONTINUE
                        and _covered_minutes(self._periods) >= MINUTES_PER_DAY
                    ):
                        return await self.async_step_feed_in()
                    return await self.async_step_periods()

        ordered = sorted(
            self._periods, key=lambda period: parse_time(period[CONF_START])
        )
        next_start = 0
        for period in ordered:
            if parse_time(period[CONF_START]) > next_start:
                break
            next_start = max(next_start, parse_time(period[CONF_END]))
        complete = covered >= MINUTES_PER_DAY

        return self.async_show_form(
            step_id="periods",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_START, default=_minutes_to_selector(min(next_start, 1439))
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_END, default=_minutes_to_selector(MINUTES_PER_DAY)
                    ): selector.TimeSelector(),
                    vol.Required(CONF_RATE, default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    ),
                    **on_submit(
                        "submit_periods", SUBMIT_CONTINUE if complete else SUBMIT_ADD
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "pattern": self._pattern_name,
                "so_far": "\n".join(
                    f"  {period[CONF_START]} to {period[CONF_END]}  {period[CONF_RATE]}"
                    for period in ordered
                )
                or "  none yet",
                "remaining": f"{(MINUTES_PER_DAY - covered) // 60}h "
                f"{(MINUTES_PER_DAY - covered) % 60}m still uncovered"
                if not complete
                else "the whole day is covered",
            },
        )

    @guarded_setup
    async def async_step_export_rates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Every feed-in rate, when export is not one price all day."""
        errors: dict[str, str] = {}

        if user_input is not None:
            action = user_input[CONF_ON_SUBMIT]
            typed = str(user_input.get(CONF_NAME) or "").strip()
            # Blank screen: move on, nothing entered. Filled in: keep it
            # first, then move on. The import side reads the same way.
            if action == SUBMIT_CONTINUE and not typed and self._pattern_export_rates():
                return await self.async_step_export_periods()
            full = f"{self._pattern_name} {typed}".strip()
            if not typed:
                errors[CONF_NAME] = "name_required"
            elif any(rate[CONF_NAME] == full for rate in self._export_rates):
                errors[CONF_NAME] = "rate_exists"
            else:
                self._export_rates.append(
                    merged(
                        ExportRate,
                        None,
                        {
                            CONF_NAME: full,
                            CONF_EXPORT_CENTS: user_input[CONF_EXPORT_CENTS],
                            # The cap on this feed-in price and what is paid
                            # past it, declared beside the price they belong to.
                            CONF_EXPORT_ALLOWANCE_KWH: (
                                user_input.get(CONF_EXPORT_ALLOWANCE_KWH) or None
                            ),
                            CONF_EXPORT_FALLBACK_CENTS: (
                                user_input.get(CONF_EXPORT_FALLBACK_CENTS) or None
                            ),
                        },
                    )
                )
                if action == SUBMIT_CONTINUE:
                    return await self.async_step_export_periods()
                return await self.async_step_export_rates()

        return self.async_show_form(
            step_id="export_rates",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=""): selector.TextSelector(),
                    vol.Required(
                        CONF_EXPORT_CENTS, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_EXPORT_ALLOWANCE_KWH, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_EXPORT_FALLBACK_CENTS, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    **on_submit("submit_export_rates"),
                }
            ),
            errors=errors,
            description_placeholders={
                "pattern": self._pattern_name,
                "so_far": ", ".join(
                    rate[CONF_NAME] for rate in self._pattern_export_rates()
                )
                or "none yet",
            },
        )

    @guarded_setup
    async def async_step_export_periods(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """When each feed-in rate applies."""
        errors: dict[str, str] = {}
        names = [rate[CONF_NAME] for rate in self._pattern_export_rates()]
        covered = _covered_minutes(self._export_periods)

        if user_input is not None:
            action = user_input[CONF_ON_SUBMIT]
            # The same gate as the import side. This branch is only reached
            # when the user turned the all-day feed-in price off, so having
            # chosen periods they have to cover the day with them.
            if action == SUBMIT_CONTINUE and covered >= MINUTES_PER_DAY:
                return await self.async_step_timetable_done()
            try:
                start = _time_to_minutes(str(user_input[CONF_START]), is_end=False)
                end = _time_to_minutes(str(user_input[CONF_END]), is_end=True)
            except PlanError:
                errors["base"] = "bad_time"
            else:
                if end <= start:
                    errors[CONF_END] = "end_before_start"
                elif any(
                    start < parse_time(existing[CONF_END])
                    and end > parse_time(existing[CONF_START])
                    for existing in self._export_periods
                ):
                    errors["base"] = "period_overlaps"
                else:
                    self._export_periods.append(
                        merged(
                            Period,
                            None,
                            {
                                CONF_START: format_time(start),
                                CONF_END: format_time(end),
                                CONF_RATE: str(user_input[CONF_RATE]),
                            },
                        )
                    )
                    if (
                        action == SUBMIT_CONTINUE
                        and _covered_minutes(self._export_periods) >= MINUTES_PER_DAY
                    ):
                        return await self.async_step_timetable_done()
                    return await self.async_step_export_periods()

        ordered = sorted(
            self._export_periods, key=lambda period: parse_time(period[CONF_START])
        )
        next_start = 0
        for period in ordered:
            if parse_time(period[CONF_START]) > next_start:
                break
            next_start = max(next_start, parse_time(period[CONF_END]))
        complete = covered >= MINUTES_PER_DAY

        return self.async_show_form(
            step_id="export_periods",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_START, default=_minutes_to_selector(min(next_start, 1439))
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_END, default=_minutes_to_selector(MINUTES_PER_DAY)
                    ): selector.TimeSelector(),
                    vol.Required(CONF_RATE, default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    ),
                    **on_submit(
                        "submit_export_periods",
                        SUBMIT_CONTINUE if complete else SUBMIT_ADD,
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "so_far": "\n".join(
                    f"  {period[CONF_START]} to {period[CONF_END]}  {period[CONF_RATE]}"
                    for period in ordered
                )
                or "  none yet",
                "remaining": f"{(MINUTES_PER_DAY - covered) // 60}h "
                f"{(MINUTES_PER_DAY - covered) % 60}m still uncovered"
                if not complete
                else "the whole day is covered",
            },
        )

    @guarded_setup
    async def async_step_feed_in(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Feed-in for this timetable: one price all day, or its own periods."""
        if user_input is not None:
            self._export_same_all_day = bool(user_input[CONF_EXPORT_SAME_ALL_DAY])
            self._export_flat = float(user_input[CONF_EXPORT_FLAT_CENTS])
            # Declared against the flat price, and only meaningful with it.
            # The all-day tickbox ends the periods branch, not the
            # declaration: an all-day feed-in can be one price up to an
            # allowance and another after it, the same shape as a
            # single-rate import plan.
            self._export_allowance = (
                user_input.get(CONF_EXPORT_ALLOWANCE_KWH) or None
                if self._export_same_all_day
                else None
            )
            self._export_fallback = (
                user_input.get(CONF_EXPORT_FALLBACK_CENTS) or None
                if self._export_same_all_day
                else None
            )
            if self._export_same_all_day:
                return await self.async_step_timetable_done()
            return await self.async_step_export_rates()

        return self.async_show_form(
            step_id="feed_in",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EXPORT_SAME_ALL_DAY, default=True
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_EXPORT_FLAT_CENTS, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_EXPORT_ALLOWANCE_KWH, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_EXPORT_FALLBACK_CENTS, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            description_placeholders={"pattern": self._pattern_name},
        )

    @guarded_setup
    async def async_step_timetable_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Two buttons: another timetable, or finish."""
        self._store_pattern()
        return self.async_show_menu(
            step_id="timetable_done",
            menu_options=["days", "finish"],
            description_placeholders={
                "so_far": ", ".join(
                    str(pattern[CONF_NAME]) for pattern in self._patterns
                )
            },
        )

    @guarded_setup
    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry."""
        return self._create()

    def _create(self) -> ConfigFlowResult:
        """Create the entry from exactly what was entered."""
        return self.async_create_entry(
            title=self._name,
            data={CONF_PLAN_NAME: self._name},
            options={
                CONF_PLAN_DESCRIPTION: self._description,
                CONF_RATES: self._rates,
                CONF_DAY_PATTERNS: self._patterns,
                CONF_EXPORT_RATES: self._export_rates,
                CONF_MONTHLY_CHARGE: self._monthly_charge,
                CONF_SUPPLY_CHARGE_CENTS: self._supply_charge,
                CONF_PRICES_INCLUDE_GST: self._include_gst,
                CONF_GST_PERCENT: self._gst_percent,
                CONF_BILLING_CYCLE_DAY: self._billing_cycle_day,
                CONF_COUNT_ALLOWANCE: self._count_allowance,
                CONF_IMPORT_ENERGY_SENSOR: self._energy_sensor,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> AbodePowerTariffsOptionsFlow:
        """Return the options flow."""
        return AbodePowerTariffsOptionsFlow()


type StepHandler = Callable[
    ["AbodePowerTariffsOptionsFlow", dict[str, Any] | None], Awaitable[ConfigFlowResult]
]


def guarded(func: StepHandler) -> StepHandler:
    """Show the failure instead of an empty dialog.

    Without this, an exception anywhere in a step gives the user a dialog with
    the word Error and nothing else, and the detail is only in the full log.
    """

    @functools.wraps(func)
    async def wrapper(
        self: AbodePowerTariffsOptionsFlow, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            return await func(self, user_input)
        except Exception:  # Deliberate: nothing may escape into an empty dialog
            _LOGGER.exception("Configuration step %s failed", func.__name__)
            self._failure = traceback.format_exc()
            return await self.async_step_failure()

    return wrapper


MENU = [
    "rates_menu",
    "day_patterns_menu",
    "periods_pick_day_pattern",
    "export_menu",
    "general",
    "usage_tracking",
    "rate_plan_card",
    "save",
]


class AbodePowerTariffsOptionsFlow(OptionsFlow):
    """Edit the plan."""

    def __init__(self) -> None:
        """Initialise the working copy lazily, on first step."""
        self._working: dict[str, Any] | None = None
        self._day_pattern_index: int | None = None
        self._period_index: int | None = None
        self._rate_index: int | None = None
        self._export_rate_index: int | None = None
        self._editing_export: bool = False
        self._failure: str = ""

    # ------------------------------------------------------------- utilities

    @property
    def working(self) -> dict[str, Any]:
        """Return the in-progress copy of the options."""
        if self._working is None:
            self._working = copy.deepcopy(dict(self.config_entry.options))
            self._working.setdefault(CONF_RATES, [])
            self._working.setdefault(CONF_DAY_PATTERNS, [])
        return self._working

    def _plan(self) -> Plan | None:
        try:
            return Plan.from_dict({**self.working, CONF_NAME: self.config_entry.title})
        except PlanError:
            return None

    def _plan_text(self) -> str:
        plan = self._plan()
        if plan is None:
            return "The stored plan cannot be read. Edit a rate to rebuild it."
        summary = render_plan(plan)
        problems = validate_plan(plan)
        if problems:
            summary += "\n\nNot ready to save:\n" + "\n".join(
                f"  {p}" for p in problems
            )
        # Warnings do not stop a save. They are configurations only the user
        # can judge, so they are said out loud rather than enforced.
        warnings = plan_warnings(plan)
        if warnings:
            summary += "\n\nWorth checking:\n" + "\n".join(f"  {w}" for w in warnings)
        return summary

    def _placeholders(self) -> dict[str, str]:
        return {"plan": self._plan_text()}

    def _rates(self) -> list[dict[str, Any]]:
        rates: list[dict[str, Any]] = self.working[CONF_RATES]
        return rates

    def _day_patterns(self) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = self.working[CONF_DAY_PATTERNS]
        return patterns

    def _rate_names(self) -> list[str]:
        return [str(rate.get(CONF_NAME, "")) for rate in self._rates()]

    def _timetable_rates(self) -> list[dict[str, Any]]:
        """Return the rates allocatable in the timetable being edited.

        Only this timetable's own. A rate is its timetable plus its name, so
        weekday.peak is not weekend.peak and must not be offered under the
        weekend. A rate with no timetable at all belongs to none and resolves
        in any, which is what a plan stored before the scoping existed
        consists of entirely, so those stay allocatable.
        """
        timetable = str(self._current_day_pattern().get(CONF_NAME, ""))
        return [
            rate
            for rate in self._rates()
            if rate.get(CONF_TIMETABLE) in (timetable, None)
        ]

    def _rate_name_by_id(self, wanted: str) -> str:
        """Return the name a period stores, given the identifier shown."""
        for rate in self._rates():
            if rate_id(rate) == wanted:
                return str(rate.get(CONF_NAME, ""))
        return wanted

    def _rate_ids(self) -> list[str]:
        """Return what each rate will be published as, which is unique."""
        return [rate_id(rate) for rate in self._rates()]

    def _rate_index_by_id(self, wanted: str) -> int | None:
        for position, rate in enumerate(self._rates()):
            if rate_id(rate) == wanted:
                return position
        return None

    def _known_constraints(self) -> list[str]:
        """Every rule already used anywhere in the plan, so it can be picked."""
        return known_constraints(self._rates())

    def _day_pattern_names(self) -> list[str]:
        return [str(pattern.get(CONF_NAME, "")) for pattern in self._day_patterns()]

    def _menu(self, step_id: str) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id=step_id,
            menu_options=MENU,
            description_placeholders=self._placeholders(),
        )

    # ------------------------------------------------------------------ menu

    @guarded
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the plan and the menu."""
        self._editing_export = False
        return self._menu("init")

    @guarded
    async def async_step_failure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what went wrong, in full, where it can be copied."""
        if user_input is not None:
            return self._menu("init")
        return self.async_show_form(
            step_id="failure",
            data_schema=vol.Schema({}),
            description_placeholders={"detail": self._failure or "No detail captured."},
        )

    @guarded
    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and store the plan."""
        plan = self._plan()
        if plan is None or validate_plan(plan):
            return self._menu("init")
        return self.async_create_entry(title="", data=self.working)

    @guarded
    async def async_step_rate_plan_card(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the plan in the shape an inverter's tariff screen asks for."""
        if user_input is not None:
            return self._menu("init")
        plan = self._plan()
        card = render_rate_plan_card(plan) if plan else "The plan cannot be read."
        return self.async_show_form(
            step_id="rate_plan_card",
            data_schema=vol.Schema({}),
            description_placeholders={"card": card},
        )

    # ----------------------------------------------------------------- rates

    @guarded
    async def async_step_rates_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add, edit or remove a rate."""
        listing = "\n".join(
            f"  {rate.get(CONF_NAME)}   "
            f"{float(rate.get(CONF_IMPORT_CENTS) or 0):.2f} c/kWh"
            f"   [{rate_id(rate)}]"
            for rate in self._rates()
        )
        return self.async_show_menu(
            step_id="rates_menu",
            menu_options=[
                "rate_add",
                "rate_pick",
                "rate_remove",
                "init",
            ],
            description_placeholders={"rates": listing or "  none yet"},
        )

    @guarded
    async def async_step_rate_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a rate to edit."""
        names = self._rate_ids()
        if not names:
            self._rate_index = None
            return await self.async_step_rate_add()
        if user_input is not None:
            self._rate_index = self._rate_index_by_id(str(user_input[CONF_NAME]))
            return await self.async_step_rate_add()
        return self.async_show_form(
            step_id="rate_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    )
                }
            ),
        )

    @guarded
    async def async_step_rate_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a rate, or edit the one chosen."""
        errors: dict[str, str] = {}
        index = self._rate_index
        existing: dict[str, Any] = self._rates()[index] if index is not None else {}

        if user_input is not None:
            user_input = flatten_sections(user_input)
            record = _rate_record(user_input)
            name = record[CONF_NAME]
            # Identified by the pair, so the same name under a different
            # timetable is a different rate rather than a clash.
            clash = any(
                rate.get(CONF_NAME) == name
                and rate.get(CONF_TIMETABLE) == record.get(CONF_TIMETABLE)
                for position, rate in enumerate(self._rates())
                if position != index
            )
            if not name:
                errors[CONF_NAME] = "name_required"
            elif clash:
                errors[CONF_NAME] = "rate_exists"
            elif _rules_in_both_lists(user_input):
                errors[CONF_ENFORCEABLE_CONSTRAINTS] = "rule_in_both_lists"
            elif _counting_without_meter(user_input):
                errors[CONF_IMPORT_ENERGY_SENSOR] = "energy_sensor_required"
            elif _demand_without_rate(user_input):
                errors[CONF_DEMAND_RATE] = "demand_rate_required"
            else:
                self.working[CONF_COUNT_ALLOWANCE] = bool(
                    user_input.get(CONF_COUNT_ALLOWANCE)
                )
                self.working[CONF_IMPORT_ENERGY_SENSOR] = (
                    user_input.get(CONF_IMPORT_ENERGY_SENSOR) or None
                )
                if index is None:
                    self._rates().append(merged(Rate, None, record))
                else:
                    previous = str(self._rates()[index].get(CONF_NAME, ""))
                    # Onto the rate already stored. The screen writes the
                    # fields it asked about; everything else stays as it is.
                    self._rates()[index] = merged(Rate, existing, record)
                    if previous and previous != name:
                        self._rename_rate(previous, name, record.get(CONF_TIMETABLE))
                self._rate_index = None
                return await self.async_step_rates_menu()

        # A rate falls back to another in its own timetable.
        scope = existing.get(CONF_TIMETABLE)
        fallback_options = [
            str(rate.get(CONF_NAME, ""))
            for rate in self._rates()
            if rate.get(CONF_TIMETABLE) == scope
            and rate.get(CONF_NAME) != existing.get(CONF_NAME)
        ]
        return self.async_show_form(
            step_id="rate_add",
            data_schema=_require_name(
                _rate_schema(
                    existing,
                    fallback_options,
                    known_constraints=self._known_constraints(),
                    timetables=self._day_pattern_names(),
                    count_allowance=bool(self.working.get(CONF_COUNT_ALLOWANCE)),
                    energy_sensor=self.working.get(CONF_IMPORT_ENERGY_SENSOR),
                )
            ),
            errors=errors,
        )

    def _rename_rate(self, previous: str, current: str, timetable: str | None) -> None:
        """Follow a rate rename into the periods that name it.

        Scoped to the rate's own timetable: renaming the weekday Peak must not
        repoint the weekend's periods at it.
        """
        for pattern in self._day_patterns():
            if timetable is not None and pattern.get(CONF_NAME) != timetable:
                continue
            for period in pattern.get(CONF_PERIODS, []):
                if period.get(CONF_RATE) == previous:
                    period[CONF_RATE] = current
        for rate in self._rates():
            if (
                rate.get(CONF_FALLBACK_RATE) == previous
                and rate.get(CONF_TIMETABLE) == timetable
            ):
                rate[CONF_FALLBACK_RATE] = current

    def _rename_timetable(self, previous: str, current: str) -> None:
        """Follow a timetable rename into the rates that belong to it.

        A rate belongs to a timetable and holds that timetable's name to say
        so. Renaming the timetable and leaving the rates behind orphans every
        one of them: the periods on the renamed timetable then name rates
        that, as far as the plan is concerned, do not exist. Renaming is only
        a rename — which days the timetable covers has no bearing on which
        rates belong to it.

        A rate with no timetable is left alone. It belongs to none and
        resolves in any, and pulling it into this one would change what it
        means.
        """
        for rate in self._rates():
            if rate.get(CONF_TIMETABLE) == previous:
                rate[CONF_TIMETABLE] = current

    @guarded
    async def async_step_rate_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a rate that no time period uses."""
        errors: dict[str, str] = {}
        names = self._rate_ids()
        if not names:
            return await self.async_step_rates_menu()

        if user_input is not None:
            wanted = str(user_input[CONF_NAME])
            index = self._rate_index_by_id(wanted)
            doomed = self._rates()[index] if index is not None else {}
            scope = doomed.get(CONF_TIMETABLE)
            in_use = any(
                period.get(CONF_RATE) == doomed.get(CONF_NAME)
                for pattern in self._day_patterns()
                if scope is None or pattern.get(CONF_NAME) == scope
                for period in pattern.get(CONF_PERIODS, [])
            )
            if in_use:
                errors[CONF_NAME] = "rate_in_use"
            else:
                self.working[CONF_RATES] = [
                    rate for rate in self._rates() if rate_id(rate) != wanted
                ]
                return await self.async_step_rates_menu()

        return self.async_show_form(
            step_id="rate_remove",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    )
                }
            ),
            errors=errors,
        )

    # ---------------------------------------------------------- day patterns

    @guarded
    async def async_step_day_patterns_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add, edit, duplicate or remove a day pattern."""
        return self.async_show_menu(
            step_id="day_patterns_menu",
            menu_options=[
                "day_pattern_add",
                "day_pattern_pick",
                "day_pattern_duplicate",
                "day_pattern_remove",
                "init",
            ],
            description_placeholders=self._placeholders(),
        )

    @guarded
    async def async_step_day_pattern_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a day pattern to edit."""
        names = self._day_pattern_names()
        if not names:
            self._day_pattern_index = None
            return await self.async_step_day_pattern_add()
        if user_input is not None:
            self._day_pattern_index = names.index(str(user_input[CONF_NAME]))
            return await self.async_step_day_pattern_add()
        return self.async_show_form(
            step_id="day_pattern_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    )
                }
            ),
        )

    @guarded
    async def async_step_day_pattern_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a day pattern, or edit the one chosen."""
        errors: dict[str, str] = {}
        index = self._day_pattern_index
        existing: dict[str, Any] = (
            self._day_patterns()[index] if index is not None else {}
        )
        current_days = list(existing.get(CONF_DAYS) or ALL_DAY_TOKENS)
        same_every_day = set(current_days) == set(ALL_DAY_TOKENS)

        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            days = (
                list(ALL_DAY_TOKENS)
                if user_input["same_every_day"]
                else list(user_input.get(CONF_DAYS) or [])
            )
            if not name:
                errors[CONF_NAME] = "name_required"
            elif not days:
                errors[CONF_DAYS] = "days_required"
            else:
                all_day = bool(user_input[CONF_EXPORT_SAME_ALL_DAY])
                # The fields this screen asked about, and no others. The time
                # periods are not among them: they belong to the record and
                # are carried by it, not hand-copied across by this screen.
                fields = {
                    CONF_NAME: name,
                    CONF_DAYS: days,
                    CONF_SEASON_FROM: str(
                        user_input.get(CONF_SEASON_FROM) or ""
                    ).strip()
                    or None,
                    CONF_SEASON_TO: str(user_input.get(CONF_SEASON_TO) or "").strip()
                    or None,
                    CONF_EXPORT_SAME_ALL_DAY: all_day,
                    CONF_EXPORT_FLAT_CENTS: user_input[CONF_EXPORT_FLAT_CENTS],
                    # Declared against the all-day price and only meaningful
                    # with it, exactly as the setup feed-in screen has it.
                    CONF_EXPORT_ALLOWANCE_KWH: (
                        user_input.get(CONF_EXPORT_ALLOWANCE_KWH) or None
                        if all_day
                        else None
                    ),
                    CONF_EXPORT_FALLBACK_CENTS: (
                        user_input.get(CONF_EXPORT_FALLBACK_CENTS) or None
                        if all_day
                        else None
                    ),
                }
                record = merged(DayPattern, existing or None, fields)
                if index is None:
                    self._day_patterns().append(record)
                else:
                    previous = str(existing.get(CONF_NAME, ""))
                    self._day_patterns()[index] = record
                    if previous and previous != name:
                        self._rename_timetable(previous, name)
                self._day_pattern_index = None
                return await self.async_step_day_patterns_menu()

        return self.async_show_form(
            step_id="day_pattern_add",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=str(existing.get(CONF_NAME) or EVERY_DAY)
                    ): selector.TextSelector(),
                    vol.Required(
                        "same_every_day", default=same_every_day
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_DAYS,
                        default=current_days
                        if not same_every_day
                        else list(WEEKDAY_TOKENS),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(ALL_DAY_TOKENS),
                            multiple=True,
                            translation_key="day_tokens",
                        )
                    ),
                    vol.Required(
                        CONF_SEASON_FROM,
                        default=str(existing.get(CONF_SEASON_FROM) or ""),
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_SEASON_TO, default=str(existing.get(CONF_SEASON_TO) or "")
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_EXPORT_SAME_ALL_DAY,
                        default=bool(existing.get(CONF_EXPORT_SAME_ALL_DAY, True)),
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_EXPORT_FLAT_CENTS,
                        default=float(existing.get(CONF_EXPORT_FLAT_CENTS) or 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    # The all-day feed-in price, its cap, and what is paid past
                    # the cap are one declaration. Setup asks for all three, so
                    # Configure has to as well: otherwise a plan can say
                    # something at setup that can never be corrected.
                    vol.Required(
                        CONF_EXPORT_ALLOWANCE_KWH,
                        default=float(existing.get(CONF_EXPORT_ALLOWANCE_KWH) or 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_EXPORT_FALLBACK_CENTS,
                        default=float(existing.get(CONF_EXPORT_FALLBACK_CENTS) or 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    @guarded
    async def async_step_day_pattern_duplicate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Copy a day pattern and its periods, then open it for editing."""
        names = self._day_pattern_names()
        if not names:
            self._day_pattern_index = None
            return await self.async_step_day_pattern_add()
        if user_input is not None:
            source = self._day_patterns()[names.index(str(user_input["source"]))]
            copied = copy.deepcopy(source)
            copied[CONF_NAME] = (
                str(user_input[CONF_NAME]).strip() or f"{source.get(CONF_NAME)} copy"
            )
            self._day_patterns().append(copied)
            self._day_pattern_index = len(self._day_patterns()) - 1
            return await self.async_step_day_pattern_add()
        return self.async_show_form(
            step_id="day_pattern_duplicate",
            data_schema=vol.Schema(
                {
                    vol.Required("source", default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    ),
                    vol.Required(CONF_NAME, default="Weekend"): selector.TextSelector(),
                }
            ),
        )

    @guarded
    async def async_step_day_pattern_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a day pattern."""
        names = self._day_pattern_names()
        if not names:
            return await self.async_step_day_patterns_menu()
        if user_input is not None:
            name = str(user_input[CONF_NAME])
            self.working[CONF_DAY_PATTERNS] = [
                pattern
                for pattern in self._day_patterns()
                if pattern.get(CONF_NAME) != name
            ]
            self._day_pattern_index = None
            return await self.async_step_day_patterns_menu()
        return self.async_show_form(
            step_id="day_pattern_remove",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    )
                }
            ),
        )

    # --------------------------------------------------------- time  periods

    @guarded
    async def async_step_periods_pick_day_pattern(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose whose time periods to edit. Import or feed-in, per the flag."""
        names = self._day_pattern_names()
        if not names:
            self._day_pattern_index = None
            return await self.async_step_day_pattern_add()
        if len(names) == 1:
            self._day_pattern_index = 0
            return await self.async_step_periods_menu()
        if user_input is not None:
            self._day_pattern_index = names.index(str(user_input[CONF_NAME]))
            return await self.async_step_periods_menu()
        return self.async_show_form(
            step_id="periods_pick_day_pattern",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    )
                }
            ),
            description_placeholders=self._placeholders(),
        )

    def _current_day_pattern(self) -> dict[str, Any]:
        patterns = self._day_patterns()
        if not patterns:
            raise PlanError("There are no day patterns to hold a time period")
        index = self._day_pattern_index or 0
        if index >= len(patterns):
            index = 0
            self._day_pattern_index = 0
        return patterns[index]

    def _periods(self) -> list[dict[str, Any]]:
        key = CONF_EXPORT_PERIODS if self._editing_export else CONF_PERIODS
        periods: list[dict[str, Any]] = self._current_day_pattern().setdefault(key, [])
        return periods

    def _period_rate_names(self) -> list[str]:
        """Return what the time period screen offers, in the form it shows it.

        Import rates are offered as published identifiers — weekday.peak, not
        Peak — the way every other screen that shows a rate to a human does,
        and scoped to the timetable being edited. Export rates are not scoped
        to a timetable at all, so they are offered as they always were.
        """
        if self._editing_export:
            return self._export_rate_names()
        return [rate_id(rate) for rate in self._timetable_rates()]

    def _export_rates(self) -> list[dict[str, Any]]:
        rates: list[dict[str, Any]] = self.working.setdefault(CONF_EXPORT_RATES, [])
        return rates

    def _export_rate_names(self) -> list[str]:
        return [str(rate.get(CONF_NAME, "")) for rate in self._export_rates()]

    def _period_labels(self) -> list[str]:
        return [
            f"{period.get(CONF_START)} to {period.get(CONF_END)}  {period.get(CONF_RATE)}"
            for period in self._periods()
        ]

    def _period_placeholders(self) -> dict[str, str]:
        """Return the strip for the chosen day pattern only."""
        pattern_name = str(self._current_day_pattern().get(CONF_NAME, ""))
        plan = self._plan()
        if plan is not None:
            for pattern in plan.day_patterns:
                if pattern.name == pattern_name:
                    text = render_day_pattern(plan, pattern)
                    break
            else:
                text = self._plan_text()
        else:
            text = self._plan_text()
        return {"day_pattern": pattern_name, "plan": text}

    @guarded
    async def async_step_periods_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add, edit or remove a time period in the chosen day pattern."""
        return self.async_show_menu(
            step_id="periods_menu",
            menu_options=["period_add", "period_pick", "period_remove", "init"],
            description_placeholders=self._period_placeholders(),
        )

    @guarded
    async def async_step_period_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a time period to edit."""
        labels = self._period_labels()
        if not labels:
            self._period_index = None
            return await self.async_step_period_add()
        if user_input is not None:
            self._period_index = labels.index(str(user_input["period"]))
            return await self.async_step_period_add()
        return self.async_show_form(
            step_id="period_pick",
            data_schema=vol.Schema(
                {
                    vol.Required("period", default=labels[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=labels)
                    )
                }
            ),
            description_placeholders=self._period_placeholders(),
        )

    @guarded
    async def async_step_period_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a time period, or edit the one chosen. Three fields, nothing else."""
        errors: dict[str, str] = {}
        names = self._period_rate_names()
        if not names:
            if self._editing_export:
                return await self.async_step_export_rate_add()
            self._rate_index = None
            return await self.async_step_rate_add()

        index = self._period_index
        existing: dict[str, Any] = self._periods()[index] if index is not None else {}

        if user_input is not None:
            try:
                start = _time_to_minutes(str(user_input[CONF_START]), is_end=False)
                end = _time_to_minutes(str(user_input[CONF_END]), is_end=True)
            except PlanError:
                errors["base"] = "bad_time"
            else:
                if end <= start:
                    errors[CONF_END] = "end_before_start"
                else:
                    chosen = str(user_input[CONF_RATE])
                    record = merged(
                        Period,
                        existing or None,
                        {
                            CONF_START: format_time(start),
                            CONF_END: format_time(end),
                            # The import screen shows identifiers; a period
                            # stores the name, which is what it is resolved by.
                            CONF_RATE: (
                                chosen
                                if self._editing_export
                                else self._rate_name_by_id(chosen)
                            ),
                        },
                    )
                    if index is None:
                        self._periods().append(record)
                    else:
                        self._periods()[index] = record
                    self._period_index = None
                    return await self.async_step_periods_menu()

        default_start = parse_time(str(existing[CONF_START])) if existing else 0
        default_end = (
            parse_time(str(existing[CONF_END])) if existing else MINUTES_PER_DAY
        )
        # Stored as a name, shown as an identifier on the import screen.
        stored_rate = str(existing.get(CONF_RATE) or "")
        default_rate = stored_rate
        if stored_rate and not self._editing_export:
            for rate in self._timetable_rates():
                if str(rate.get(CONF_NAME, "")) == stored_rate:
                    default_rate = rate_id(rate)
                    break
        if default_rate not in names:
            default_rate = names[0]

        return self.async_show_form(
            step_id="period_add",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_START, default=_minutes_to_selector(default_start)
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_END, default=_minutes_to_selector(default_end)
                    ): selector.TimeSelector(),
                    vol.Required(
                        CONF_RATE, default=default_rate
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    ),
                }
            ),
            errors=errors,
            description_placeholders=self._period_placeholders(),
        )

    @guarded
    async def async_step_period_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a time period."""
        labels = self._period_labels()
        if not labels:
            return await self.async_step_periods_menu()
        if user_input is not None:
            self._periods().pop(labels.index(str(user_input["period"])))
            self._period_index = None
            return await self.async_step_periods_menu()
        return self.async_show_form(
            step_id="period_remove",
            data_schema=vol.Schema(
                {
                    vol.Required("period", default=labels[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=labels)
                    )
                }
            ),
            description_placeholders=self._period_placeholders(),
        )

    # --------------------------------------------------------------- general

    @guarded
    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Supply charge, tax, validity and the two optional sensors."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.working[CONF_SUPPLY_CHARGE_CENTS] = user_input[
                CONF_SUPPLY_CHARGE_CENTS
            ]
            self.working[CONF_PRICES_INCLUDE_GST] = user_input[CONF_PRICES_INCLUDE_GST]
            self.working[CONF_GST_PERCENT] = user_input[CONF_GST_PERCENT]
            self.working[CONF_VALID_FROM] = user_input.get(CONF_VALID_FROM) or None
            self.working[CONF_VALID_TO] = user_input.get(CONF_VALID_TO) or None
            self.working[CONF_HOLIDAY_SENSOR] = (
                user_input.get(CONF_HOLIDAY_SENSOR) or None
            )
            self.working[CONF_MONTHLY_CHARGE] = user_input[CONF_MONTHLY_CHARGE]
            day = int(user_input.get(CONF_BILLING_CYCLE_DAY) or 0)
            self.working[CONF_BILLING_CYCLE_DAY] = day or None
            plan = self._plan()
            if day > MAX_BILLING_CYCLE_DAY:
                # The cycle starts on the same day every month, so the day has
                # to be one that every month has.
                errors[CONF_BILLING_CYCLE_DAY] = "billing_day_out_of_range"
            elif plan is not None and any(
                "validity" in str(problem) for problem in validate_plan(plan)
            ):
                errors[CONF_VALID_TO] = "validity_backwards"
            else:
                return self._menu("init")

        options = self.working
        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SUPPLY_CHARGE_CENTS,
                        default=float(options.get(CONF_SUPPLY_CHARGE_CENTS) or 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_PRICES_INCLUDE_GST,
                        default=bool(options.get(CONF_PRICES_INCLUDE_GST, True)),
                    ): selector.BooleanSelector(),
                    vol.Required(
                        CONF_GST_PERCENT,
                        default=float(
                            options.get(CONF_GST_PERCENT) or DEFAULT_GST_PERCENT
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=0.1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_VALID_FROM,
                        description={"suggested_value": options.get(CONF_VALID_FROM)},
                    ): selector.DateSelector(),
                    vol.Optional(
                        CONF_VALID_TO,
                        description={"suggested_value": options.get(CONF_VALID_TO)},
                    ): selector.DateSelector(),
                    vol.Optional(
                        CONF_HOLIDAY_SENSOR,
                        description={
                            "suggested_value": options.get(CONF_HOLIDAY_SENSOR)
                        },
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="binary_sensor")
                    ),
                    vol.Required(
                        CONF_MONTHLY_CHARGE,
                        default=float(options.get(CONF_MONTHLY_CHARGE) or 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_BILLING_CYCLE_DAY,
                        default=int(options.get(CONF_BILLING_CYCLE_DAY) or 0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=31,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # ---------------------------------------------------------------- feed-in

    @guarded
    async def async_step_export_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Feed-in rates and when they apply."""
        listing = "\n".join(
            f"  {rate.get(CONF_NAME)}   "
            f"{float(rate.get(CONF_EXPORT_CENTS) or 0):.2f} c/kWh"
            for rate in self._export_rates()
        )
        return self.async_show_menu(
            step_id="export_menu",
            menu_options=[
                "export_rate_add",
                "export_rate_pick",
                "export_rate_remove",
                "export_periods_pick",
                "init",
            ],
            description_placeholders={"rates": listing or "  none yet"},
        )

    @guarded
    async def async_step_export_rate_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose a feed-in rate to edit."""
        names = self._export_rate_names()
        if not names:
            self._export_rate_index = None
            return await self.async_step_export_rate_add()
        if user_input is not None:
            self._export_rate_index = names.index(str(user_input[CONF_NAME]))
            return await self.async_step_export_rate_add()
        return self.async_show_form(
            step_id="export_rate_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    )
                }
            ),
        )

    @guarded
    async def async_step_export_rate_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a feed-in rate, or edit the one chosen."""
        errors: dict[str, str] = {}
        index = self._export_rate_index
        existing: dict[str, Any] = (
            self._export_rates()[index] if index is not None else {}
        )

        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            clash = any(
                rate.get(CONF_NAME) == name
                for position, rate in enumerate(self._export_rates())
                if position != index
            )
            if not name:
                errors[CONF_NAME] = "name_required"
            elif clash:
                errors[CONF_NAME] = "rate_exists"
            else:
                record = merged(
                    ExportRate,
                    existing or None,
                    {
                        CONF_NAME: name,
                        CONF_EXPORT_CENTS: user_input[CONF_EXPORT_CENTS],
                        CONF_EXPORT_ALLOWANCE_KWH: (
                            user_input.get(CONF_EXPORT_ALLOWANCE_KWH) or None
                        ),
                        CONF_EXPORT_FALLBACK_CENTS: (
                            user_input.get(CONF_EXPORT_FALLBACK_CENTS) or None
                        ),
                    },
                )
                if index is None:
                    self._export_rates().append(record)
                else:
                    previous = str(self._export_rates()[index].get(CONF_NAME, ""))
                    self._export_rates()[index] = record
                    if previous and previous != name:
                        for pattern in self._day_patterns():
                            for period in pattern.get(CONF_EXPORT_PERIODS, []):
                                if period.get(CONF_RATE) == previous:
                                    period[CONF_RATE] = name
                self._export_rate_index = None
                return await self.async_step_export_menu()

        return self.async_show_form(
            step_id="export_rate_add",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=str(existing.get(CONF_NAME) or "")
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_EXPORT_CENTS,
                        default=float(existing.get(CONF_EXPORT_CENTS) or 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_EXPORT_ALLOWANCE_KWH,
                        default=float(existing.get(CONF_EXPORT_ALLOWANCE_KWH) or 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_EXPORT_FALLBACK_CENTS,
                        default=float(existing.get(CONF_EXPORT_FALLBACK_CENTS) or 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    @guarded
    async def async_step_export_rate_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a feed-in rate that no feed-in period uses."""
        errors: dict[str, str] = {}
        names = self._export_rate_names()
        if not names:
            return await self.async_step_export_menu()
        if user_input is not None:
            name = str(user_input[CONF_NAME])
            in_use = any(
                period.get(CONF_RATE) == name
                for pattern in self._day_patterns()
                for period in pattern.get(CONF_EXPORT_PERIODS, [])
            )
            if in_use:
                errors[CONF_NAME] = "rate_in_use"
            else:
                self.working[CONF_EXPORT_RATES] = [
                    rate for rate in self._export_rates() if rate.get(CONF_NAME) != name
                ]
                return await self.async_step_export_menu()
        return self.async_show_form(
            step_id="export_rate_remove",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=names[0]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=names)
                    )
                }
            ),
            errors=errors,
        )

    @guarded
    async def async_step_export_periods_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the feed-in time periods, using the same screens as import."""
        self._editing_export = True
        return await self.async_step_periods_pick_day_pattern()

    # -------------------------------------------------------- usage tracking

    @guarded
    async def async_step_usage_tracking(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Split kWh by rate, and keep the split pointed at the right rate."""
        return self.async_show_menu(
            step_id="usage_tracking",
            menu_options=["meter_create", "meter_link", "init"],
            description_placeholders={
                "rates": ", ".join(self._rate_names()) or "none",
                "linked": ", ".join(self.working.get(CONF_TARIFF_SELECTS) or [])
                or "none",
            },
        )

    @guarded
    async def async_step_meter_create(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a utility meter with this plan's rates as its tariffs.

        Creates a config entry belonging to the utility_meter integration. It is
        not owned here: renaming a rate afterwards does not rename its tariff,
        and deleting this channel does not delete the meter.
        """
        errors: dict[str, str] = {}
        names = self._rate_names()
        if not names:
            self._rate_index = None
            return await self.async_step_rate_add()

        if user_input is not None:
            result = await self.hass.config_entries.flow.async_init(
                UTILITY_METER_DOMAIN,
                context={"source": SOURCE_USER},
                data={
                    UM_CONF_NAME: str(user_input[CONF_NAME]).strip(),
                    UM_CONF_SOURCE: user_input[CONF_SOURCE_ENERGY_SENSOR],
                    UM_CONF_CYCLE: UM_CYCLE_MONTHLY,
                    UM_CONF_OFFSET: 0,
                    UM_CONF_TARIFFS: names,
                    UM_CONF_NET_CONSUMPTION: False,
                    UM_CONF_DELTA_VALUES: False,
                    UM_CONF_PERIODICALLY_RESETTING: True,
                    UM_CONF_ALWAYS_AVAILABLE: False,
                },
            )
            if result.get("type") != "create_entry":
                errors["base"] = "meter_not_created"
                _LOGGER.error("Utility meter was not created: %s", result)
            else:
                return await self.async_step_meter_link()

        return self.async_show_form(
            step_id="meter_create",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=f"{self.config_entry.title} by rate"
                    ): selector.TextSelector(),
                    vol.Required(CONF_SOURCE_ENERGY_SENSOR): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="energy"
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"rates": ", ".join(names)},
        )

    @guarded
    async def async_step_meter_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nominate which tariff dropdowns to keep pointed at the current rate."""
        if user_input is not None:
            self.working[CONF_TARIFF_SELECTS] = (
                user_input.get(CONF_TARIFF_SELECTS) or []
            )
            return self._menu("init")

        options = self.working
        return self.async_show_form(
            step_id="meter_link",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TARIFF_SELECTS,
                        description={
                            "suggested_value": options.get(CONF_TARIFF_SELECTS) or []
                        },
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="select", multiple=True)
                    ),
                }
            ),
            description_placeholders={"rates": ", ".join(self._rate_names()) or "none"},
        )
