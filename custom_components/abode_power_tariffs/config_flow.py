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
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    ALL_DAY_TOKENS,
    CONF_COASTING_PERMITTED,
    CONF_COUNT_ALLOWANCE,
    CONF_CONSTRAINTS,
    CONF_DAILY_ALLOWANCE_KWH,
    CONF_DAY_PATTERNS,
    CONF_DAYS,
    CONF_DEMAND_PERIOD,
    CONF_DEMAND_RATE,
    CONF_END,
    CONF_ENFORCEABLE_CONSTRAINTS,
    CONF_EXPORT_ALLOWANCE_KWH,
    CONF_EXPORT_CENTS,
    CONF_EXPORT_FLAT_CENTS,
    CONF_EXPORT_PERIODS,
    CONF_EXPORT_RATES,
    CONF_EXPORT_SAME_ALL_DAY,
    CONF_FALLBACK_RATE,
    CONF_GST_PERCENT,
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
    CONF_RATES,
    CONF_SEASON_FROM,
    CONF_SEASON_TO,
    CONF_SOURCE_ENERGY_SENSOR,
    CONF_START,
    CONF_SUPPLY_CHARGE_CENTS,
    CONF_SUPPLY_CHARGE_ENTITIES,
    CONF_TIMETABLE,
    CONF_TARIFF_SELECTS,
    CONF_VALID_FROM,
    CONF_VALID_TO,
    DEFAULT_GST_PERCENT,
    DOMAIN,
    KNOWN_CONSTRAINTS,
    MINUTES_PER_DAY,
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
from .plan import Plan, PlanError, format_time, parse_time, slug
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


def _rate_record(user_input: dict[str, Any]) -> dict[str, Any]:
    """Build a stored rate from a submitted form.

    The form asks for the two rule lists separately so nothing has to be typed
    twice. What is stored is the union plus the enforceable subset, so anything
    reading the flat list sees exactly what it always saw.
    """
    informational = _rules_from(user_input, CONF_INFORMATION_CONSTRAINTS)
    enforceable = _rules_from(user_input, CONF_ENFORCEABLE_CONSTRAINTS)
    union = [
        *informational,
        *(rule for rule in enforceable if rule not in informational),
    ]
    return {
        CONF_NAME: str(user_input.get(CONF_NAME) or "").strip(),
        CONF_TIMETABLE: _timetable_from(user_input),
        CONF_IMPORT_CENTS: user_input[CONF_IMPORT_CENTS],
        CONF_EXPORT_CENTS: user_input.get(CONF_EXPORT_CENTS, 0.0),
        CONF_CONSTRAINTS: union,
        CONF_ENFORCEABLE_CONSTRAINTS: enforceable,
        CONF_COASTING_PERMITTED: bool(user_input.get(CONF_COASTING_PERMITTED, True)),
        CONF_DEMAND_PERIOD: bool(user_input.get(CONF_DEMAND_PERIOD, False)),
        CONF_DAILY_ALLOWANCE_KWH: user_input.get(CONF_DAILY_ALLOWANCE_KWH) or None,
        CONF_EXPORT_ALLOWANCE_KWH: user_input.get(CONF_EXPORT_ALLOWANCE_KWH) or None,
        CONF_FALLBACK_RATE: user_input.get(CONF_FALLBACK_RATE) or None,
    }


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


# What the first-run form asks for. Everything else has a sensible default and
# is set afterwards in Configure. The two rule lists are here because they are
# the only rate fields that create entities: a plan set up without them gets no
# constraint sensors at all, and nothing on screen says the feature exists.
SETUP_RATE_FIELDS: Final = (
    CONF_NAME,
    CONF_IMPORT_CENTS,
    CONF_INFORMATION_CONSTRAINTS,
    CONF_ENFORCEABLE_CONSTRAINTS,
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
            CONF_COASTING_PERMITTED,
            default=bool(existing.get(CONF_COASTING_PERMITTED, True)),
        )
    ] = selector.BooleanSelector()
    schema[
        vol.Required(
            CONF_DEMAND_PERIOD, default=bool(existing.get(CONF_DEMAND_PERIOD, False))
        )
    ] = selector.BooleanSelector()
    schema[
        vol.Required(
            CONF_DAILY_ALLOWANCE_KWH,
            default=float(existing.get(CONF_DAILY_ALLOWANCE_KWH) or 0.0),
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=1000, step=0.1, mode=selector.NumberSelectorMode.BOX
        )
    )
    schema[
        vol.Required(
            CONF_EXPORT_ALLOWANCE_KWH,
            default=float(existing.get(CONF_EXPORT_ALLOWANCE_KWH) or 0.0),
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=1000, step=0.1, mode=selector.NumberSelectorMode.BOX
        )
    )
    if fallback_options:
        schema[
            vol.Required(
                CONF_FALLBACK_RATE,
                default=existing.get(CONF_FALLBACK_RATE) or fallback_options[0],
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=fallback_options)
        )
    if fields is not None:
        schema = {
            key: value for key, value in schema.items() if _field_name(key) in fields
        }
    return vol.Schema(schema)


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

    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Start with nothing."""
        self._name: str = ""
        self._description: str = ""
        self._supply_charge: float = 0.0
        self._include_gst: bool = True
        self._gst_percent: float = DEFAULT_GST_PERCENT
        self._demand_rate: float = 0.0
        self._monthly_charge: float = 0.0
        self._patterns: list[dict[str, Any]] = []
        self._export_same_all_day: bool = True
        self._export_flat: float = 0.0
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
            {
                CONF_NAME: self._pattern_name,
                CONF_DAYS: self._pattern_days,
                CONF_PERIODS: self._periods,
                CONF_EXPORT_PERIODS: self._export_periods,
                CONF_EXPORT_SAME_ALL_DAY: self._export_same_all_day,
                CONF_EXPORT_FLAT_CENTS: self._export_flat,
            }
        )
        self._pattern_name = ""
        self._pattern_days = []
        self._periods = []
        self._export_periods = []
        self._export_same_all_day = True
        self._export_flat = 0.0

    # ---------------------------------------------------------------- 1 name

    @guarded_setup
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the plan."""
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
        if user_input is not None:
            self._supply_charge = float(user_input[CONF_SUPPLY_CHARGE_CENTS])
            self._include_gst = bool(user_input[CONF_PRICES_INCLUDE_GST])
            self._gst_percent = float(user_input[CONF_GST_PERCENT])
            self._monthly_charge = float(user_input[CONF_MONTHLY_CHARGE])
            self._demand_rate = float(user_input[CONF_DEMAND_RATE])
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
                    vol.Required(
                        CONF_DEMAND_RATE, default=0.0
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step="any",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
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
            action = user_input[CONF_ON_SUBMIT]
            if action == SUBMIT_CONTINUE and self._pattern_rates():
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
            else:
                self._rates.append(record)
                return await self.async_step_rates()

        schema = {
            **_rate_schema(
                {},
                [],
                fields=SETUP_RATE_FIELDS,
                known_constraints=known_constraints(self._rates),
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
        covered = sum(
            parse_time(period[CONF_END]) - parse_time(period[CONF_START])
            for period in self._periods
        )

        if user_input is not None:
            action = user_input[CONF_ON_SUBMIT]
            if action == SUBMIT_CONTINUE and self._periods:
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
                        {
                            CONF_START: format_time(start),
                            CONF_END: format_time(end),
                            CONF_RATE: str(user_input[CONF_RATE]),
                        }
                    )
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
            if action == SUBMIT_CONTINUE and self._pattern_export_rates():
                return await self.async_step_export_periods()
            typed = str(user_input.get(CONF_NAME) or "").strip()
            full = f"{self._pattern_name} {typed}".strip()
            if not typed:
                errors[CONF_NAME] = "name_required"
            elif any(rate[CONF_NAME] == full for rate in self._export_rates):
                errors[CONF_NAME] = "rate_exists"
            else:
                self._export_rates.append(
                    {CONF_NAME: full, CONF_EXPORT_CENTS: user_input[CONF_EXPORT_CENTS]}
                )
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
        covered = sum(
            parse_time(period[CONF_END]) - parse_time(period[CONF_START])
            for period in self._export_periods
        )

        if user_input is not None:
            action = user_input[CONF_ON_SUBMIT]
            if action == SUBMIT_CONTINUE and self._export_periods:
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
                        {
                            CONF_START: format_time(start),
                            CONF_END: format_time(end),
                            CONF_RATE: str(user_input[CONF_RATE]),
                        }
                    )
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
                CONF_DEMAND_RATE: self._demand_rate,
                CONF_MONTHLY_CHARGE: self._monthly_charge,
                CONF_SUPPLY_CHARGE_CENTS: self._supply_charge,
                CONF_PRICES_INCLUDE_GST: self._include_gst,
                CONF_GST_PERCENT: self._gst_percent,
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
    "allowance_counting",
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
            menu_options=["rate_add", "rate_pick", "rate_remove", "init"],
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
            else:
                if index is None:
                    self._rates().append(record)
                else:
                    previous = str(self._rates()[index].get(CONF_NAME, ""))
                    self._rates()[index] = record
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
                record = {
                    CONF_NAME: name,
                    CONF_DAYS: days,
                    CONF_SEASON_FROM: str(
                        user_input.get(CONF_SEASON_FROM) or ""
                    ).strip()
                    or None,
                    CONF_SEASON_TO: str(user_input.get(CONF_SEASON_TO) or "").strip()
                    or None,
                    CONF_PERIODS: existing.get(CONF_PERIODS, []),
                    CONF_EXPORT_PERIODS: existing.get(CONF_EXPORT_PERIODS, []),
                    CONF_EXPORT_SAME_ALL_DAY: user_input[CONF_EXPORT_SAME_ALL_DAY],
                    CONF_EXPORT_FLAT_CENTS: user_input[CONF_EXPORT_FLAT_CENTS],
                }
                if index is None:
                    self._day_patterns().append(record)
                else:
                    self._day_patterns()[index] = record
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
        return self._export_rate_names() if self._editing_export else self._rate_names()

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
                    record = {
                        CONF_START: format_time(start),
                        CONF_END: format_time(end),
                        CONF_RATE: str(user_input[CONF_RATE]),
                    }
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
        default_rate = str(existing.get(CONF_RATE) or names[0])
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
            self.working[CONF_DEMAND_RATE] = user_input[CONF_DEMAND_RATE]
            self.working[CONF_MONTHLY_CHARGE] = user_input[CONF_MONTHLY_CHARGE]
            plan = self._plan()
            if plan is not None and any(
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
                        CONF_DEMAND_RATE,
                        default=float(options.get(CONF_DEMAND_RATE) or 0.0),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1000,
                            step="any",
                            mode=selector.NumberSelectorMode.BOX,
                        )
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
                }
            ),
            errors=errors,
        )

    # -------------------------------------------------------------- allowance

    @guarded
    async def async_step_allowance_counting(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Whether to keep a running total against a capped rate.

        Separate from the plan on purpose. The plan declares the cap and what
        is paid past it whatever this says; this only decides whether the
        component keeps its own count, which is an estimate.
        """
        if user_input is not None:
            self.working[CONF_COUNT_ALLOWANCE] = bool(user_input[CONF_COUNT_ALLOWANCE])
            self.working[CONF_IMPORT_ENERGY_SENSOR] = (
                user_input.get(CONF_IMPORT_ENERGY_SENSOR) or None
            )
            return self._menu("init")

        options = self.working
        capped = [
            str(rate.get(CONF_NAME))
            for rate in self._rates()
            if rate.get(CONF_DAILY_ALLOWANCE_KWH)
        ]
        return self.async_show_form(
            step_id="allowance_counting",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_COUNT_ALLOWANCE,
                        default=bool(options.get(CONF_COUNT_ALLOWANCE, False)),
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_IMPORT_ENERGY_SENSOR,
                        description={
                            "suggested_value": options.get(CONF_IMPORT_ENERGY_SENSOR)
                        },
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="energy"
                        )
                    ),
                }
            ),
            description_placeholders={"capped": ", ".join(capped) or "none"},
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
                record = {
                    CONF_NAME: name,
                    CONF_EXPORT_CENTS: user_input[CONF_EXPORT_CENTS],
                }
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
            self.working[CONF_SUPPLY_CHARGE_ENTITIES] = user_input[
                CONF_SUPPLY_CHARGE_ENTITIES
            ]
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
                    vol.Required(
                        CONF_SUPPLY_CHARGE_ENTITIES,
                        default=bool(options.get(CONF_SUPPLY_CHARGE_ENTITIES, False)),
                    ): selector.BooleanSelector(),
                }
            ),
            description_placeholders={"rates": ", ".join(self._rate_names()) or "none"},
        )
