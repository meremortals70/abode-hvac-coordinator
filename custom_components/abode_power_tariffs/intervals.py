"""Forward interval generation and boundary calculation. Pure module.

Periods are wall-clock times: a peak period is 16:00 local on both sides of a
daylight-saving transition, and the plan never changes because the clocks did.

The horizon is wall clock too: 24 hours from 18:00 means 18:00 tomorrow, which
is 23 real hours on the day the clocks go forward and 25 on the day they go
back. Within that horizon the walk is done in real instants, so the hour that
does not exist is not emitted and the hour that happens twice is emitted twice.
Every interval is published in local time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from .const import MINUTES_PER_DAY
from .plan import Plan, Resolution

HolidayCheck = Callable[[date], bool]


@dataclass(frozen=True, slots=True)
class Interval:
    """One resolved slice of the forward horizon."""

    start: datetime
    end: datetime
    rate: str
    import_price: float
    export_price: float
    constraints: tuple[str, ...]
    enforceable_constraints: tuple[str, ...]
    coasting_permitted: bool
    allowance_kwh: float | None
    fallback_rate: str | None
    fallback_per_kwh: float | None
    day_pattern: str
    # Declared, not blended into import_price. A consumer that wants the real
    # cost of drawing power in this interval applies its own assumption about
    # kW draw to demand_rate_per_kw_month, the same way it already applies
    # its own arithmetic to the allowance.
    demand_period: bool
    demand_rate_per_kw_month: float
    # The export side of the same declaration import already makes: the
    # allowance and what is paid past it, never blended into export_price.
    export_allowance_kwh: float | None
    export_fallback_price: float | None

    @property
    def duration_minutes(self) -> int:
        """Return the interval length in whole minutes of real time.

        Normalised to UTC first. Subtracting two datetimes that carry the same
        tzinfo is done on the wall clock, which would report the resolution
        that was asked for even where a transition makes the interval shorter
        or longer than that.
        """
        return int(
            (self.end.astimezone(UTC) - self.start.astimezone(UTC)).total_seconds()
            // 60
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the interval in the published service response shape."""
        return {
            "start_time": self.start.isoformat(),
            "end_time": self.end.isoformat(),
            "duration": self.duration_minutes,
            "per_kwh": round(self.import_price, 6),
            "export_per_kwh": round(self.export_price, 6),
            "rate": self.rate,
            # The flat list is every rule, unchanged, so a consumer that has
            # always read it sees what it always saw. The sibling key names
            # the subset the user declared other systems should treat as a
            # rule rather than a hint.
            "constraints": list(self.constraints),
            "enforceable_constraints": list(self.enforceable_constraints),
            "coasting_permitted": self.coasting_permitted,
            # The cap and what is paid past it, so a consumer can apply the
            # rule itself whether or not this component is counting.
            "allowance_kwh": self.allowance_kwh,
            "fallback_rate": self.fallback_rate,
            "fallback_per_kwh": self.fallback_per_kwh,
            "day_pattern": self.day_pattern,
            "demand_period": self.demand_period,
            "demand_rate_per_kw_month": self.demand_rate_per_kw_month,
            "export_allowance_kwh": self.export_allowance_kwh,
            "export_fallback_price": (
                None
                if self.export_fallback_price is None
                else round(self.export_fallback_price, 6)
            ),
            "forecast": False,
        }

    def as_evcc_entry(self) -> dict[str, Any]:
        """Return the interval in the shape evcc reads from a forecast attribute.

        Local time, with the offset, like everything else this component
        publishes. The offset makes the instant unambiguous, including on the
        day the clocks go back and a wall-clock time occurs twice.
        """
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "value": round(self.import_price, 6),
        }


def local_minutes(moment: datetime, zone: tzinfo) -> tuple[date, int]:
    """Return the local date and minute of the day for an instant."""
    local = moment.astimezone(zone)
    return local.date(), local.hour * 60 + local.minute


def resolve_at(
    plan: Plan,
    moment: datetime,
    zone: tzinfo,
    is_holiday: HolidayCheck,
) -> Resolution | None:
    """Resolve the plan at an instant."""
    day, minutes = local_minutes(moment, zone)
    return plan.resolve(day, minutes, is_holiday(day))


def instants_at(day: date, minutes: int, zone: tzinfo) -> tuple[datetime, ...]:
    """Return every real instant a wall-clock time names on a local date.

    One ordinarily. Two on the morning the clocks go back, when the same
    minute past midnight comes round twice an hour apart — a boundary is
    stored as minutes past midnight and that alone does not identify a moment.

    The arithmetic is done while the value is naive and the zone attached
    afterwards. Adding a timedelta to an aware midnight is wall-clock
    arithmetic and always lands on the first pass, so the second is never
    built and cannot be chosen.
    """
    naive = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minutes)
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    if first.utcoffset() == second.utcoffset():
        return (first,)
    return (first, second)


def next_boundary(
    plan: Plan,
    moment: datetime,
    zone: tzinfo,
    is_holiday: HolidayCheck,
    *,
    max_days: int = 3,
    export: bool = False,
) -> datetime | None:
    """Return the next instant at which the resolved period changes.

    Walks forward over the wall-clock boundaries of today and the following
    days, building every real instant each one names and taking the soonest
    that is still ahead.

    The comparison is made in UTC. Two aware datetimes that share a tzinfo are
    compared on the wall clock, where the two passes through a repeated hour
    are indistinguishable, and the earlier one wins on the digits alone.

    With ``export`` set, walks the feed-in boundaries instead of the import
    ones. Returns None when there are none to find — a plan on one feed-in
    price all day has nothing to schedule.
    """
    reference = moment.astimezone(UTC)
    day = moment.astimezone(zone).date()

    for offset in range(max_days + 1):
        current_day = day + timedelta(days=offset)
        edges = (
            plan.export_boundaries_for(current_day, is_holiday(current_day))
            if export
            else plan.boundaries_for(current_day, is_holiday(current_day))
        )
        if not edges:
            # No day set on this date; the next day may still have one.
            continue
        soonest: datetime | None = None
        for edge in edges:
            if edge >= MINUTES_PER_DAY:
                candidate_day = current_day + timedelta(days=1)
                candidate_minutes = 0
            else:
                candidate_day = current_day
                candidate_minutes = edge
            for candidate in instants_at(candidate_day, candidate_minutes, zone):
                if candidate.astimezone(UTC) <= reference:
                    continue
                if soonest is None or candidate.astimezone(UTC) < soonest.astimezone(
                    UTC
                ):
                    soonest = candidate
        if soonest is not None:
            return soonest.astimezone(moment.tzinfo or zone)

    return None


def generate(
    plan: Plan,
    start: datetime,
    zone: tzinfo,
    is_holiday: HolidayCheck,
    *,
    hours: int = 24,
    resolution_minutes: int = 30,
) -> list[Interval]:
    """Generate the forward interval series from an instant."""
    if resolution_minutes <= 0:
        raise ValueError("resolution_minutes must be positive")
    if hours <= 0:
        raise ValueError("hours must be positive")

    step = timedelta(minutes=resolution_minutes)
    # Align the first interval to the resolution grid in local time, so a
    # 30-minute series starts on the hour or the half hour, then leave local
    # time behind: the walk is in real instants so the step is always the
    # resolution asked for, whatever the clocks did.
    local_start = start.astimezone(zone)
    minute_of_day = local_start.hour * 60 + local_start.minute
    aligned_minute = (minute_of_day // resolution_minutes) * resolution_minutes
    # The aligned wall-clock minute names one real instant ordinarily, but two
    # on the morning the clocks go back. Combining a naive datetime with a
    # timedelta and attaching tzinfo afterwards always lands on fold=0 — the
    # first pass — which silently pulls a second-pass start an hour into the
    # past. Build every real instant the aligned minute names and take the
    # latest one that is not after ``start`` itself, so the chosen instant
    # falls in the same pass ``start`` does.
    reference = start.astimezone(UTC)
    candidates = [
        candidate.astimezone(UTC)
        for candidate in instants_at(local_start.date(), aligned_minute, zone)
    ]
    eligible = [candidate for candidate in candidates if candidate <= reference]
    cursor = eligible[-1] if eligible else candidates[0]
    # The horizon is wall clock. Twenty-four hours from 18:00 is 18:00
    # tomorrow, which is 23 or 25 real hours across a transition.
    finish = (local_start + timedelta(hours=hours)).astimezone(UTC)

    intervals: list[Interval] = []
    while cursor < finish:
        nxt = cursor + step
        resolution = resolve_at(plan, cursor, zone, is_holiday)
        if resolution is not None:
            # Asked of the rate, not of the day set it was resolved through.
            # A rate belongs to a timetable and says so itself; validation and
            # allowance.apply both look a fallback up that way, and a third
            # answer here means the series can name a fallback validation
            # never approved.
            fallback = (
                plan.rate_by_name(
                    resolution.rate.fallback_rate, resolution.rate.timetable
                )
                if resolution.rate.fallback_rate
                else None
            )
            # Read once, from the export side's own resolution. The price, the
            # cap on it and what is paid past that cap are one declaration and
            # belong to the feed-in price in force — not to whichever import
            # rate happens to be running alongside it.
            export_day, export_minutes = local_minutes(cursor, zone)
            export = plan.export_at(export_day, export_minutes, is_holiday(export_day))
            intervals.append(
                Interval(
                    start=cursor.astimezone(zone),
                    end=nxt.astimezone(zone),
                    rate=resolution.rate.qualified_name,
                    import_price=resolution.rate.import_price,
                    export_price=export.price,
                    constraints=tuple(sorted(resolution.rate.constraints)),
                    enforceable_constraints=tuple(
                        sorted(resolution.rate.enforceable_constraints)
                    ),
                    coasting_permitted=resolution.rate.coasting_permitted,
                    allowance_kwh=resolution.rate.rate_allowance_kwh,
                    fallback_rate=None if fallback is None else fallback.qualified_name,
                    fallback_per_kwh=None
                    if fallback is None
                    else round(fallback.import_price, 6),
                    day_pattern=resolution.day_pattern.name,
                    demand_period=resolution.rate.demand_period,
                    demand_rate_per_kw_month=resolution.rate.demand_rate_per_kw_month,
                    export_allowance_kwh=export.allowance_kwh,
                    export_fallback_price=export.fallback_price,
                )
            )
        cursor = nxt

    return intervals
