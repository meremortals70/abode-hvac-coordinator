"""Tariff, consumed from Abode Power Tariffs.

Pure. No Home Assistant imports.

This controller no longer holds a tariff of its own. The plan — periods, rates,
prices, feed-in and the daily supply charge — belongs to the `abode_power_tariffs`
integration, which publishes it as a forward interval series. This module turns
that series into the two things the controller actually needs: what rules are in
force right now, and how the next few hours are divided up.

Constraints are absolute rules, not price hints. They are declared in the
tariff, never hard-coded here. Each is consumed by whichever system owns the
relevant actuator: grid_charge_battery by the battery automations,
precool_opportunity and no_grid_import by this controller. An unrecognised
constraint is carried through and reported, never silently dropped, so adding
one needs no code change here.

A constraint is never traded against price or comfort at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any, Final

#: Constraints this controller acts on. Anything else is passed through to the
#: trace and reported as unrecognised, which is deliberate, not a gap.
CONSTRAINT_NO_GRID_IMPORT: Final = "no_grid_import"
CONSTRAINT_PRECOOL_OPPORTUNITY: Final = "precool_opportunity"
CONSTRAINT_GRID_CHARGE_BATTERY: Final = "grid_charge_battery"

KNOWN_CONSTRAINTS: Final = frozenset(
    {
        CONSTRAINT_NO_GRID_IMPORT,
        CONSTRAINT_PRECOOL_OPPORTUNITY,
        CONSTRAINT_GRID_CHARGE_BATTERY,
    }
)


class TariffPayloadError(ValueError):
    """The interval series could not be understood.

    Raised rather than returning None so the reason reaches the log. A tariff
    that cannot be parsed must not take the controller down: rooms still hold
    their comfort bands, and only the window-driven behaviour is lost.
    """


@dataclass(frozen=True, slots=True)
class Interval:
    """One slice of the forward series, as published by Abode Power Tariffs.

    Prices are dollars per kWh. That is the publisher's unit — see
    `intervals.py` `Interval.as_dict` in `abode_power_tariffs`, which rounds
    `per_kwh` and `export_per_kwh` to six decimal places in dollars. Nothing
    here converts to cents; carrying two units through the code is how the
    wrong one ends up on a dashboard.
    """

    start: datetime
    end: datetime
    rate: str
    per_kwh: float | None
    export_per_kwh: float | None
    constraints: frozenset[str]
    coasting_permitted: bool

    def contains(self, at: datetime) -> bool:
        """Whether an instant falls in this interval. End is exclusive."""
        return self.start <= at < self.end

    def unrecognised_constraints(self) -> frozenset[str]:
        """Constraints on this interval this controller does not act on itself.

        Reported rather than dropped, so a constraint meant for another system
        is visible instead of silently ignored.
        """
        return frozenset(self.constraints) - KNOWN_CONSTRAINTS


@dataclass(frozen=True, slots=True)
class TariffWindow:
    """A run of consecutive intervals sharing a rate and a rule set.

    The forward series arrives at a fixed resolution — thirty minutes by
    default — so a five-hour peak period is ten identical intervals. The demand
    forecast wants the period, not the slices, so consecutive intervals that
    agree on rate, constraints and coasting are collapsed back into one.
    """

    start: time
    end: time
    rate: str
    per_kwh: float | None
    constraints: frozenset[str]
    coasting_permitted: bool


def _as_datetime(value: Any, field: str, default_tz: tzinfo) -> datetime:
    """Parse an interval timestamp, and guarantee it is timezone-aware.

    Abode Power Tariffs generates its intervals in Home Assistant's local
    timezone, so in practice these arrive with an offset. This does not rely on
    that: a naive timestamp compared against an aware `utcnow()` raises
    `TypeError` and takes down the whole evaluation loop, which is exactly what
    the forecast did the first time it was configured.

    Naive values are attached to the caller's timezone rather than assumed UTC.
    A tariff period is a wall-clock concept — "peak starts at four" — so local
    is the right reading, and assuming UTC would shift every window by ten
    hours in Brisbane without reporting anything.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as err:
            raise TariffPayloadError(
                f"{field} is not a timestamp: {value!r}"
            ) from err
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=default_tz)
    return parsed


def _as_optional_float(value: Any, field: str) -> float | None:
    """A price, or None where the tariff carries none for that interval."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as err:
        raise TariffPayloadError(f"{field} is not a number: {value!r}") from err


def interval_from_response(
    raw: dict[str, Any], default_tz: tzinfo = UTC
) -> Interval:
    """Build one interval from the published service response shape.

    Field names follow `abode_power_tariffs.get_intervals`: `start_time`,
    `end_time`, `per_kwh`, `export_per_kwh`, `rate`, `constraints`,
    `coasting_permitted`. `duration`, `allowance_kwh`, `day_pattern` and
    `forecast` are published too and are not read here — nothing this
    controller decides turns on them.
    """
    try:
        rate = str(raw["rate"])
    except KeyError as err:
        raise TariffPayloadError("interval has no rate") from err

    return Interval(
        start=_as_datetime(raw.get("start_time"), "start_time", default_tz),
        end=_as_datetime(raw.get("end_time"), "end_time", default_tz),
        rate=rate,
        per_kwh=_as_optional_float(raw.get("per_kwh"), "per_kwh"),
        export_per_kwh=_as_optional_float(raw.get("export_per_kwh"), "export_per_kwh"),
        constraints=frozenset(raw.get("constraints") or ()),
        coasting_permitted=bool(raw.get("coasting_permitted", True)),
    )


class TariffSeries:
    """The forward interval series, in order.

    Replaces the schedule this controller used to hold itself. There is no
    coverage validation here: completeness is the publishing integration's
    responsibility, and duplicating the check would mean two places to fix when
    a plan is wrong.
    """

    def __init__(
        self,
        intervals: tuple[Interval, ...],
        fetched_at: datetime,
        local_tz: tzinfo = UTC,
    ) -> None:
        """Hold the series, when it was fetched, and the wall clock it belongs to.

        The timezone is needed because a tariff window is a wall-clock concept.
        Collapsing intervals into periods takes the time-of-day off each end,
        and doing that on a UTC-aware timestamp would produce a window that
        starts at 06:00 for a period the plan calls 16:00.
        """
        self._intervals = tuple(sorted(intervals, key=lambda i: i.start))
        self.fetched_at = fetched_at
        self.local_tz = local_tz

    @classmethod
    def from_response(
        cls,
        response: dict[str, Any] | None,
        fetched_at: datetime,
        default_tz: tzinfo = UTC,
    ) -> TariffSeries:
        """Build a series from the raw service response."""
        if not isinstance(response, dict):
            raise TariffPayloadError(f"response is not a mapping: {type(response)}")
        raw_intervals = response.get("intervals")
        if not isinstance(raw_intervals, list):
            raise TariffPayloadError("response carries no interval list")
        if not raw_intervals:
            raise TariffPayloadError("response carries an empty interval list")
        return cls(
            tuple(interval_from_response(raw, default_tz) for raw in raw_intervals),
            fetched_at,
            default_tz,
        )

    @property
    def intervals(self) -> tuple[Interval, ...]:
        """Every interval in the series, earliest first."""
        return self._intervals

    @property
    def covers_until(self) -> datetime | None:
        """The end of the series, or None when it is empty."""
        return self._intervals[-1].end if self._intervals else None

    def interval_at(self, at: datetime) -> Interval | None:
        """The interval in force at an instant, or None past the horizon."""
        for interval in self._intervals:
            if interval.contains(at):
                return interval
        return None

    def windows(self) -> tuple[TariffWindow, ...]:
        """Consecutive intervals collapsed into periods, for the forecast.

        Two intervals join when they are contiguous and agree on rate,
        constraints and coasting. Price is taken from the first of the run: a
        period whose price changed partway through would not have been one
        period in the plan that produced it.
        """
        windows: list[TariffWindow] = []
        run_start: Interval | None = None
        previous: Interval | None = None

        for interval in self._intervals:
            if (
                previous is not None
                and run_start is not None
                and interval.start == previous.end
                and interval.rate == previous.rate
                and interval.constraints == previous.constraints
                and interval.coasting_permitted == previous.coasting_permitted
            ):
                previous = interval
                continue

            if run_start is not None and previous is not None:
                windows.append(_window(run_start, previous, self.local_tz))
            run_start = interval
            previous = interval

        if run_start is not None and previous is not None:
            windows.append(_window(run_start, previous, self.local_tz))
        return tuple(windows)

    def unrecognised_constraints(self) -> frozenset[str]:
        """Every declared constraint this controller does not act on itself."""
        found: set[str] = set()
        for interval in self._intervals:
            found |= interval.unrecognised_constraints()
        return frozenset(found)

    def hours_until_clear(self, constraint: str, now: datetime) -> float | None:
        """Hours from now until `constraint` is no longer in force.

        Walks the already-fetched series forward from now; no separate fetch.
        Zero when the constraint is not in force at `now` at all. None when
        every remaining interval in the series still carries it — the series
        does not reach far enough to say when it clears, not that it never
        does.

        Used by the power-aware compressor decision: how long a room needs to
        be carried on battery or solar before grid import is allowed again.
        """
        current = self.interval_at(now)
        if current is None or constraint not in current.constraints:
            return 0.0

        for interval in self._intervals:
            if interval.start < now:
                continue
            if constraint not in interval.constraints:
                cleared_at = max(interval.start, now)
                return (cleared_at - now).total_seconds() / 3600.0
        return None

    def cheaper_interval_ahead(
        self, now: datetime, current_per_kwh: float, horizon: timedelta
    ) -> datetime | None:
        """When the next strictly cheaper interval begins, within a horizon.

        0.8.11, finding 12. Every mode has to evaluate the cheapest way to
        deliver what the comfort band requires, and the ordinary case of
        that is timing: is a lower price imminent, and can the room wait
        for it. This answers the first half. An interval with no price at
        all is skipped rather than treated as free — a missing figure is
        not evidence of a cheaper window, on the same principle as every
        other "cannot compute it" case in this project defaulting to the
        safer, more conservative answer.

        `None` if no cheaper interval starts within the horizon, including
        when the series does not reach that far.
        """
        deadline = now + horizon
        for interval in self._intervals:
            if interval.start <= now:
                continue
            if interval.start > deadline:
                break
            if interval.per_kwh is None:
                continue
            if interval.per_kwh < current_per_kwh:
                return interval.start
        return None


def _window(first: Interval, last: Interval, local_tz: tzinfo) -> TariffWindow:
    """Collapse a run of intervals into the period they came from.

    Converted to local wall time before the time-of-day is taken. The demand
    forecast compares these against the local clock, so a window carried in UTC
    would be ten hours out in Brisbane and would still look plausible.
    """
    return TariffWindow(
        start=first.start.astimezone(local_tz).time(),
        end=last.end.astimezone(local_tz).time(),
        rate=first.rate,
        per_kwh=first.per_kwh,
        constraints=first.constraints,
        coasting_permitted=first.coasting_permitted,
    )
