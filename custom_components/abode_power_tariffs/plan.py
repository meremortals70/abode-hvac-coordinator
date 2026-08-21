"""The tariff plan: rates, day sets, periods, and resolution.

Pure module. Imports nothing from Home Assistant.

Times are held as minutes since local midnight, 0 to 1440 inclusive. 1440 is
"end of day" and is only ever an end. Start is inclusive, end exclusive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, TypeVar

from .const import (
    ALL_DAY_TOKENS,
    CONF_BILLING_CYCLE_DAY,
    CONF_COMPONENTS,
    CONF_CONSTRAINTS,
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
    CONF_IMPORT_CENTS,
    CONF_MONTHLY_CHARGE,
    CONF_NAME,
    CONF_PERIODS,
    CONF_PLAN_DESCRIPTION,
    CONF_PRICES_INCLUDE_GST,
    CONF_RATE,
    CONF_RATE_ALLOWANCE_KWH,
    CONF_RATES,
    CONF_SEASON_FROM,
    CONF_SEASON_TO,
    CONF_START,
    CONF_SUPPLY_CHARGE_CENTS,
    CONF_TIMETABLE,
    CONF_VALID_FROM,
    CONF_VALID_TO,
    CONSTRAINT_COASTING_PERMITTED,
    DAY_HOLIDAY,
    MINUTES_PER_DAY,
    WEEKDAY_TOKENS,
)


class PlanError(ValueError):
    """Raised when stored configuration cannot be read into a plan."""


def parse_time(value: str) -> int:
    """Parse 'HH:MM' into minutes since midnight. '24:00' is accepted."""
    try:
        raw_hour, raw_minute = value.strip().split(":")
        hour = int(raw_hour)
        minute = int(raw_minute)
    except (AttributeError, ValueError) as err:
        raise PlanError(f"'{value}' is not a time in HH:MM form") from err
    if hour == 24 and minute == 0:
        return MINUTES_PER_DAY
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise PlanError(f"'{value}' is not a valid time of day")
    return hour * 60 + minute


def format_time(minutes: int) -> str:
    """Format minutes since midnight as 'HH:MM'. 1440 renders as '24:00'."""
    if minutes == MINUTES_PER_DAY:
        return "24:00"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_month_day(value: str) -> tuple[int, int]:
    """Parse 'MM-DD' into a (month, day) pair."""
    try:
        raw_month, raw_day = value.strip().split("-")
        month = int(raw_month)
        day = int(raw_day)
    except (AttributeError, ValueError) as err:
        raise PlanError(f"'{value}' is not a date in MM-DD form") from err
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise PlanError(f"'{value}' is not a valid month and day")
    return month, day


def format_month_day(value: tuple[int, int]) -> str:
    """Format a (month, day) pair as 'MM-DD'."""
    return f"{value[0]:02d}-{value[1]:02d}"


def slug(value: str) -> str:
    """Reduce a name to lowercase words joined by underscores.

    Used to build a rate's unique identifier from its timetable and its name.
    Pure, so it cannot use Home Assistant's slugify.
    """
    cleaned = "".join(
        character if character.isalnum() else " " for character in value.lower()
    )
    return "_".join(cleaned.split())


def day_token(day: date, is_holiday: bool) -> str:
    """Return the day type token for a calendar date."""
    if is_holiday:
        return DAY_HOLIDAY
    return WEEKDAY_TOKENS[day.weekday()]


@dataclass(frozen=True, slots=True)
class Rate:
    """One named rate. Prices are dollars per kWh."""

    name: str
    import_price: float
    # Which timetable this rate belongs to. Two timetables can each have a
    # rate called Peak at different prices, because a rate is identified by
    # the pair rather than by the name alone.
    #
    # None means the rate was stored before the scoping existed, when the
    # setup flow guaranteed uniqueness by prefixing the timetable name onto
    # the rate name. Such a rate keeps the name it was given, is already
    # unique, and resolves in any timetable. It is not a way to share a rate;
    # it is what the old plans look like.
    timetable: str | None = None
    export_price: float = 0.0
    constraints: frozenset[str] = field(default_factory=frozenset)
    # The subset of the above the user has declared other systems should treat
    # as a rule rather than a hint. A declaration about the meaning of the
    # rate, not an instruction: this component still enforces nothing.
    enforceable_constraints: frozenset[str] = field(default_factory=frozenset)
    rate_allowance_kwh: float | None = None
    fallback_rate: str | None = None
    demand_period: bool = False
    # Dollars per kW per month. Belongs to the rate, not the plan: a demand
    # charge is what makes drawing power during this rate's window expensive,
    # not a plan-wide fact. Declared, never blended into import_price — a
    # consumer applies its own assumption about draw to work out the real
    # cost, the same way it already does with an allowance.
    demand_rate_per_kw_month: float = 0.0
    components: tuple[tuple[str, float], ...] = ()

    @property
    def coasting_permitted(self) -> bool:
        """Return whether the user declared coasting acceptable in this rate.

        A rule like any other rule, not a field of its own. It says the same
        kind of thing the rest of them say — something another system may act
        on while this rate is in force — and it is declared the same way.
        """
        return CONSTRAINT_COASTING_PERMITTED in self.constraints

    @property
    def has_allowance(self) -> bool:
        """Return whether this rate is capped by an energy allowance."""
        return self.rate_allowance_kwh is not None

    @property
    def informational_constraints(self) -> frozenset[str]:
        """Return the rules the user did not declare enforceable."""
        return self.constraints - self.enforceable_constraints

    @property
    def qualified_name(self) -> str:
        """Return the identifier that is unique across the whole plan.

        'weekday.peak'. This is what appears anywhere uniqueness is required —
        the rate sensor, the utility meter's tariffs — while the name the user
        typed is what they see on the rate itself.

        Always qualified when the rate belongs to a timetable. At the point
        setup names the first timetable's rates it cannot know whether a second
        timetable is coming, and Configure can add one years later, so an
        identifier that depended on the count would be assigned before the
        fact that decides it is known.
        """
        if self.timetable is None:
            return self.name
        return f"{slug(self.timetable)}.{slug(self.name)}"

    def as_dict(self) -> dict[str, Any]:
        """Return the rate as a plain dictionary."""
        return {
            CONF_NAME: self.name,
            CONF_TIMETABLE: self.timetable,
            CONF_IMPORT_CENTS: round(self.import_price * 100, 4),
            CONF_EXPORT_CENTS: round(self.export_price * 100, 4),
            CONF_CONSTRAINTS: sorted(self.constraints),
            CONF_ENFORCEABLE_CONSTRAINTS: sorted(self.enforceable_constraints),
            CONF_RATE_ALLOWANCE_KWH: self.rate_allowance_kwh,
            CONF_FALLBACK_RATE: self.fallback_rate,
            CONF_DEMAND_PERIOD: self.demand_period,
            CONF_DEMAND_RATE: self.demand_rate_per_kw_month,
            CONF_COMPONENTS: dict(self.components),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Rate:
        """Build a rate from stored configuration."""
        name = str(raw.get(CONF_NAME, "")).strip()
        if not name:
            raise PlanError("A rate must have a name")
        components = raw.get(CONF_COMPONENTS) or {}
        timetable = raw.get(CONF_TIMETABLE)
        return cls(
            name=name,
            # Absent on a plan stored before the scoping existed, whose rate
            # names already carry the timetable and are already unique.
            timetable=str(timetable).strip() or None if timetable else None,
            import_price=_cents_to_dollars(raw.get(CONF_IMPORT_CENTS)),
            export_price=_cents_to_dollars(raw.get(CONF_EXPORT_CENTS)),
            constraints=frozenset(
                str(item).strip()
                for item in raw.get(CONF_CONSTRAINTS) or ()
                if str(item).strip()
            ),
            # Absent on a plan written before the distinction existed, which
            # loads as nothing enforceable. Existing rules keep the meaning
            # they were given rather than being silently strengthened.
            enforceable_constraints=frozenset(
                str(item).strip()
                for item in raw.get(CONF_ENFORCEABLE_CONSTRAINTS) or ()
                if str(item).strip()
            ),
            rate_allowance_kwh=_optional_float(raw.get(CONF_RATE_ALLOWANCE_KWH)),
            fallback_rate=(
                str(raw[CONF_FALLBACK_RATE]) if raw.get(CONF_FALLBACK_RATE) else None
            ),
            demand_period=bool(raw.get(CONF_DEMAND_PERIOD, False)),
            demand_rate_per_kw_month=float(raw.get(CONF_DEMAND_RATE) or 0.0),
            components=tuple(
                (str(key), float(value)) for key, value in sorted(components.items())
            ),
        )


def _cents_to_dollars(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return round(float(value) / 100.0, 6)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    return int(value)


@dataclass(frozen=True, slots=True)
class ExportRate:
    """A named feed-in price, in dollars per kWh.

    The cap and what is paid past it sit here, beside the price they belong
    to, exactly as the import allowance and fallback sit on the import rate.
    They used to live on the import rate, which is a different flow with its
    own periods and could say nothing about which export price a cap applied
    to. Declared only: nothing is counted against an export allowance.
    """

    name: str
    price: float
    allowance_kwh: float | None = None
    # A bare price rather than the name of another export rate: there is no
    # second export rate to point at the way import's fallback_rate does.
    fallback_price: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the export rate as a plain dictionary."""
        return {
            CONF_NAME: self.name,
            CONF_EXPORT_CENTS: round(self.price * 100, 4),
            CONF_EXPORT_ALLOWANCE_KWH: self.allowance_kwh,
            CONF_EXPORT_FALLBACK_CENTS: (
                None
                if self.fallback_price is None
                else round(self.fallback_price * 100, 4)
            ),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ExportRate:
        """Build an export rate from stored configuration."""
        name = str(raw.get(CONF_NAME, "")).strip()
        if not name:
            raise PlanError("An export rate must have a name")
        return cls(
            name=name,
            price=_cents_to_dollars(raw.get(CONF_EXPORT_CENTS)),
            allowance_kwh=_optional_float(raw.get(CONF_EXPORT_ALLOWANCE_KWH)),
            fallback_price=(
                _cents_to_dollars(raw[CONF_EXPORT_FALLBACK_CENTS])
                if raw.get(CONF_EXPORT_FALLBACK_CENTS)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class Period:
    """One time period within a day set, naming a rate."""

    start: int
    end: int
    rate: str

    def contains(self, minutes: int) -> bool:
        """Return whether a minute of the day falls inside this period."""
        return self.start <= minutes < self.end

    def as_dict(self) -> dict[str, Any]:
        """Return the period as a plain dictionary."""
        return {
            CONF_START: format_time(self.start),
            CONF_END: format_time(self.end),
            CONF_RATE: self.rate,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Period:
        """Build a period from stored configuration."""
        rate = str(raw.get(CONF_RATE, "")).strip()
        if not rate:
            raise PlanError("A period must name a rate")
        return cls(
            start=parse_time(str(raw.get(CONF_START, ""))),
            end=parse_time(str(raw.get(CONF_END, ""))),
            rate=rate,
        )


@dataclass(frozen=True, slots=True)
class DayPattern:
    """A set of day types, optionally limited to a season, and its periods."""

    name: str
    days: frozenset[str]
    periods: tuple[Period, ...] = ()
    season_from: tuple[int, int] | None = None
    season_to: tuple[int, int] | None = None
    export_periods: tuple[Period, ...] = ()
    export_same_all_day: bool = True
    export_flat_price: float = 0.0
    # The cap on the all-day feed-in price and what is paid past it. The
    # all-day tickbox ends the periods branch, not the declaration: an
    # all-day export can be one price up to an allowance and another after,
    # the same shape as the single-rate import plan.
    export_allowance_kwh: float | None = None
    export_fallback_price: float | None = None

    @property
    def is_seasonal(self) -> bool:
        """Return whether this day set is limited to a date range."""
        return self.season_from is not None and self.season_to is not None

    def covers_date(self, day: date) -> bool:
        """Return whether the season range contains this date."""
        if not self.is_seasonal:
            return True
        assert self.season_from is not None
        assert self.season_to is not None
        current = (day.month, day.day)
        start = self.season_from
        end = self.season_to
        if start <= end:
            return start <= current <= end
        # Wraps the new year, e.g. 11-01 to 03-31.
        return current >= start or current <= end

    def matches(self, token: str, day: date) -> bool:
        """Return whether this day set applies to a day type on a date."""
        return token in self.days and self.covers_date(day)

    def export_period_at(self, minutes: int) -> Period | None:
        """Return the export period containing a minute of the day, if any."""
        for period in self.export_periods:
            if period.contains(minutes):
                return period
        return None

    def sorted_export_periods(self) -> tuple[Period, ...]:
        """Return the export periods in start order."""
        return tuple(sorted(self.export_periods, key=lambda period: period.start))

    def period_at(self, minutes: int) -> Period | None:
        """Return the period containing a minute of the day, if any."""
        for period in self.periods:
            if period.contains(minutes):
                return period
        return None

    def sorted_periods(self) -> tuple[Period, ...]:
        """Return the periods in start order."""
        return tuple(sorted(self.periods, key=lambda period: period.start))

    def as_dict(self) -> dict[str, Any]:
        """Return the day set as a plain dictionary."""
        return {
            CONF_NAME: self.name,
            CONF_DAYS: [token for token in ALL_DAY_TOKENS if token in self.days],
            CONF_SEASON_FROM: (
                format_month_day(self.season_from) if self.season_from else None
            ),
            CONF_SEASON_TO: (
                format_month_day(self.season_to) if self.season_to else None
            ),
            CONF_PERIODS: [period.as_dict() for period in self.sorted_periods()],
            CONF_EXPORT_PERIODS: [
                period.as_dict() for period in self.sorted_export_periods()
            ],
            CONF_EXPORT_SAME_ALL_DAY: self.export_same_all_day,
            CONF_EXPORT_FLAT_CENTS: round(self.export_flat_price * 100, 4),
            CONF_EXPORT_ALLOWANCE_KWH: self.export_allowance_kwh,
            CONF_EXPORT_FALLBACK_CENTS: (
                None
                if self.export_fallback_price is None
                else round(self.export_fallback_price * 100, 4)
            ),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DayPattern:
        """Build a day set from stored configuration."""
        name = str(raw.get(CONF_NAME, "")).strip()
        if not name:
            raise PlanError("A day set must have a name")
        days = frozenset(str(token) for token in raw.get(CONF_DAYS) or ())
        unknown = days - set(ALL_DAY_TOKENS)
        if unknown:
            raise PlanError(
                f"Unknown day types in '{name}': {', '.join(sorted(unknown))}"
            )
        season_from = raw.get(CONF_SEASON_FROM)
        season_to = raw.get(CONF_SEASON_TO)
        return cls(
            name=name,
            days=days,
            periods=tuple(
                Period.from_dict(item) for item in raw.get(CONF_PERIODS) or ()
            ),
            export_periods=tuple(
                Period.from_dict(item) for item in raw.get(CONF_EXPORT_PERIODS) or ()
            ),
            export_same_all_day=bool(raw.get(CONF_EXPORT_SAME_ALL_DAY, True)),
            export_flat_price=_cents_to_dollars(raw.get(CONF_EXPORT_FLAT_CENTS)),
            export_allowance_kwh=_optional_float(raw.get(CONF_EXPORT_ALLOWANCE_KWH)),
            export_fallback_price=(
                _cents_to_dollars(raw[CONF_EXPORT_FALLBACK_CENTS])
                if raw.get(CONF_EXPORT_FALLBACK_CENTS)
                else None
            ),
            season_from=parse_month_day(str(season_from)) if season_from else None,
            season_to=parse_month_day(str(season_to)) if season_to else None,
        )


@dataclass(frozen=True, slots=True)
class ExportPricing:
    """The feed-in declaration in force at one moment.

    Kept apart from the import Resolution because import and export are
    separate flows with separate rates and separate periods.
    """

    price: float
    allowance_kwh: float | None
    fallback_price: float | None


@dataclass(frozen=True, slots=True)
class Resolution:
    """The plan resolved at one instant."""

    day_pattern: DayPattern
    period: Period
    rate: Rate


@dataclass(frozen=True, slots=True)
class Plan:
    """A complete tariff plan for one metering channel."""

    name: str
    rates: tuple[Rate, ...] = ()
    day_patterns: tuple[DayPattern, ...] = ()
    daily_supply_charge: float = 0.0
    prices_include_gst: bool = True
    gst_percent: float = 0.0
    valid_from: date | None = None
    valid_to: date | None = None
    description: str = ""
    export_rates: tuple[ExportRate, ...] = ()
    monthly_charge: float = 0.0
    # The day of the month the billing cycle starts. Declared and published;
    # nothing is derived from it here. Working out where a cycle begins and
    # ends, or how many days are left of one, is the consumer's arithmetic.
    billing_cycle_day: int | None = None

    def rate_by_name(self, name: str, timetable: str | None = None) -> Rate | None:
        """Return a rate by name, preferring one belonging to a timetable.

        A rate scoped to the timetable asked for wins, so a period in the
        Weekend timetable naming 'Peak' gets the weekend's Peak and not the
        weekday's. An unscoped rate is the fallback, which is what a plan
        stored before the scoping existed consists of entirely.
        """
        unscoped: Rate | None = None
        for rate in self.rates:
            if rate.name != name:
                continue
            if timetable is not None and rate.timetable == timetable:
                return rate
            if rate.timetable is None and unscoped is None:
                unscoped = rate
        return unscoped

    def rates_for(self, timetable: str) -> tuple[Rate, ...]:
        """Return the rates belonging to one timetable, plus any unscoped ones."""
        return tuple(
            rate
            for rate in self.rates
            if rate.timetable == timetable or rate.timetable is None
        )

    @property
    def rate_names(self) -> tuple[str, ...]:
        """Return every rate's name as the user typed it, in configured order."""
        return tuple(rate.name for rate in self.rates)

    @property
    def qualified_rate_names(self) -> tuple[str, ...]:
        """Return every rate's unique identifier, in configured order."""
        return tuple(rate.qualified_name for rate in self.rates)

    @property
    def constraints(self) -> tuple[str, ...]:
        """Return every constraint declared anywhere in the plan, sorted."""
        found: set[str] = set()
        for rate in self.rates:
            found |= rate.constraints
        return tuple(sorted(found))

    @property
    def day_pattern_names(self) -> tuple[str, ...]:
        """Return every day set name, in configured order."""
        return tuple(day_pattern.name for day_pattern in self.day_patterns)

    def day_pattern_for(self, day: date, is_holiday: bool) -> DayPattern | None:
        """Return the day set that applies on a date, or None."""
        token = day_token(day, is_holiday)
        seasonal: DayPattern | None = None
        general: DayPattern | None = None
        for day_pattern in self.day_patterns:
            if not day_pattern.matches(token, day):
                continue
            if day_pattern.is_seasonal:
                if seasonal is None:
                    seasonal = day_pattern
            elif general is None:
                general = day_pattern
        # A seasonal day set is more specific than a year-round one.
        return seasonal or general

    def export_rate_by_name(self, name: str) -> ExportRate | None:
        """Return an export rate by name, or None."""
        for rate in self.export_rates:
            if rate.name == name:
                return rate
        return None

    @property
    def export_rate_names(self) -> tuple[str, ...]:
        """Return every export rate name, in configured order."""
        return tuple(rate.name for rate in self.export_rates)

    def export_at(self, day: date, minutes: int, is_holiday: bool) -> ExportPricing:
        """Return the whole feed-in declaration in force at a moment.

        The price, the cap on it, and what is paid past that cap — read from
        wherever the feed-in price is declared. The mode is a property of the
        timetable: one may be flat while another has periods, and the
        declaration follows the price either way.
        """
        pattern = self.day_pattern_for(day, is_holiday)
        if pattern is None:
            return ExportPricing(0.0, None, None)
        if pattern.export_same_all_day:
            return ExportPricing(
                pattern.export_flat_price,
                pattern.export_allowance_kwh,
                pattern.export_fallback_price,
            )
        period = pattern.export_period_at(minutes)
        if period is None:
            return ExportPricing(0.0, None, None)
        rate = self.export_rate_by_name(period.rate)
        if rate is None:
            return ExportPricing(0.0, None, None)
        return ExportPricing(rate.price, rate.allowance_kwh, rate.fallback_price)

    def export_price_at(self, day: date, minutes: int, is_holiday: bool) -> float:
        """Return the feed-in price in force, in dollars per kWh."""
        return self.export_at(day, minutes, is_holiday).price

    def is_active_on(self, day: date) -> bool:
        """Return whether the plan's validity range contains this date."""
        if self.valid_from is not None and day < self.valid_from:
            return False
        return not (self.valid_to is not None and day > self.valid_to)

    def resolve(self, day: date, minutes: int, is_holiday: bool) -> Resolution | None:
        """Resolve the plan at a local date and minute of the day."""
        day_pattern = self.day_pattern_for(day, is_holiday)
        if day_pattern is None:
            return None
        period = day_pattern.period_at(minutes)
        if period is None:
            return None
        rate = self.rate_by_name(period.rate, day_pattern.name)
        if rate is None:
            return None
        return Resolution(day_pattern=day_pattern, period=period, rate=rate)

    @property
    def has_export_periods(self) -> bool:
        """Return whether any timetable prices feed-in by time of day."""
        return any(
            not day_pattern.export_same_all_day and day_pattern.export_periods
            for day_pattern in self.day_patterns
        )

    def boundaries_for(self, day: date, is_holiday: bool) -> tuple[int, ...]:
        """Return the import period boundaries in force on a date, in minutes."""
        day_pattern = self.day_pattern_for(day, is_holiday)
        if day_pattern is None:
            return ()
        edges = {0, MINUTES_PER_DAY}
        for period in day_pattern.periods:
            edges.add(period.start)
            edges.add(period.end)
        return tuple(sorted(edges))

    def export_boundaries_for(self, day: date, is_holiday: bool) -> tuple[int, ...]:
        """Return the feed-in price boundaries in force on a date, in minutes.

        Empty when the timetable is on one price all day: the feed-in price
        never changes, so there is nothing to wake up for. Kept apart from the
        import boundaries because the two are separate facts — the import rate
        can be flat while the feed-in price moves, and a consumer deciding what
        to do about that needs to know which one is changing.
        """
        day_pattern = self.day_pattern_for(day, is_holiday)
        if day_pattern is None or day_pattern.export_same_all_day:
            return ()
        edges = {0, MINUTES_PER_DAY}
        for period in day_pattern.export_periods:
            edges.add(period.start)
            edges.add(period.end)
        return tuple(sorted(edges))

    def as_dict(self) -> dict[str, Any]:
        """Return the plan as a plain dictionary, ready for storage."""
        return {
            CONF_NAME: self.name,
            CONF_PLAN_DESCRIPTION: self.description,
            CONF_RATES: [rate.as_dict() for rate in self.rates],
            CONF_EXPORT_RATES: [rate.as_dict() for rate in self.export_rates],
            CONF_MONTHLY_CHARGE: self.monthly_charge,
            CONF_BILLING_CYCLE_DAY: self.billing_cycle_day,
            CONF_DAY_PATTERNS: [
                day_pattern.as_dict() for day_pattern in self.day_patterns
            ],
            CONF_SUPPLY_CHARGE_CENTS: round(self.daily_supply_charge * 100, 4),
            CONF_PRICES_INCLUDE_GST: self.prices_include_gst,
            CONF_GST_PERCENT: self.gst_percent,
            CONF_VALID_FROM: self.valid_from.isoformat() if self.valid_from else None,
            CONF_VALID_TO: self.valid_to.isoformat() if self.valid_to else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Plan:
        """Build a plan from stored configuration."""
        return cls(
            name=str(raw.get(CONF_NAME, "Tariff")),
            description=str(raw.get(CONF_PLAN_DESCRIPTION) or ""),
            rates=tuple(Rate.from_dict(item) for item in raw.get(CONF_RATES) or ()),
            day_patterns=tuple(
                DayPattern.from_dict(item) for item in raw.get(CONF_DAY_PATTERNS) or ()
            ),
            daily_supply_charge=_cents_to_dollars(raw.get(CONF_SUPPLY_CHARGE_CENTS)),
            prices_include_gst=bool(raw.get(CONF_PRICES_INCLUDE_GST, True)),
            gst_percent=float(raw.get(CONF_GST_PERCENT) or 0.0),
            export_rates=tuple(
                ExportRate.from_dict(item) for item in raw.get(CONF_EXPORT_RATES) or ()
            ),
            # A plan stored before demand moved onto the rate (pre-release
            # only, no migration) may still carry CONF_DEMAND_RATE here. It is
            # not read: the value belongs to whichever rate has demand_period
            # set now, and there is no way to know which rate that was meant
            # for from a single plan-wide number.
            monthly_charge=float(raw.get(CONF_MONTHLY_CHARGE) or 0.0),
            billing_cycle_day=_optional_int(raw.get(CONF_BILLING_CYCLE_DAY)),
            valid_from=_optional_date(raw.get(CONF_VALID_FROM)),
            valid_to=_optional_date(raw.get(CONF_VALID_TO)),
        )


def _optional_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


StoredRecord = TypeVar("StoredRecord", Rate, ExportRate, Period, DayPattern)


def merged(
    model: type[StoredRecord],
    existing: dict[str, Any] | None,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Return one stored record after a screen has written the fields it owns.

    The single place that knows what a stored object is made of is the model
    above. A screen that assembles its own dictionary holds a second copy of
    that knowledge, and the second copy is the one that goes stale: a field
    added here is picked up by storage for free and then has to be remembered,
    by hand, in every screen that writes the object. It was not remembered, and
    an edit to a timetable's name deleted the export allowance declared beside
    it.

    So a screen passes only the fields it asked the user about. Anything else
    comes from the record already stored, and the shape comes from the model:

    - nothing can be dropped, because every key the model reads is written;
    - nothing can be invented, because keys the model does not read are not;
    - nothing untouched is rewritten, because a value present in ``existing``
      is passed through exactly as stored rather than round-tripped.

    ``existing`` is None when the object is being created. A field absent at
    creation means not declared, which is true, and the model's own default
    is what fills it.
    """
    combined = {**(existing or {}), **changes}
    canonical = model.from_dict(combined).as_dict()
    return {key: combined.get(key, value) for key, value in canonical.items()}
