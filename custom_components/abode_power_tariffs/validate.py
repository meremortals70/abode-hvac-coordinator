"""Validation of a plan. Pure module.

A plan that fails any of these cannot be saved. Every problem names the day set
and the hours involved, because "invalid configuration" is not a useful message
when six periods are on screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .const import ALL_DAY_TOKENS, MAX_BILLING_CYCLE_DAY, MINUTES_PER_DAY
from .plan import DayPattern, Plan, Rate, format_time


@dataclass(frozen=True, slots=True)
class Problem:
    """One validation failure."""

    scope: str
    message: str

    def __str__(self) -> str:
        """Return the problem as one line."""
        return f"{self.scope}: {self.message}" if self.scope else self.message


def validate_periods(day_pattern: DayPattern) -> list[Problem]:
    """Check that a day set's periods tile the day exactly once."""
    problems: list[Problem] = []
    periods = day_pattern.sorted_periods()

    if not periods:
        return [Problem(day_pattern.name, "has no periods")]

    for period in periods:
        if period.end <= period.start:
            problems.append(
                Problem(
                    day_pattern.name,
                    f"{format_time(period.start)}-{format_time(period.end)} ends "
                    "before it starts",
                )
            )

    cursor = 0
    for period in periods:
        if period.start > cursor:
            problems.append(
                Problem(
                    day_pattern.name,
                    f"nothing covers {format_time(cursor)}-{format_time(period.start)}",
                )
            )
        elif period.start < cursor:
            problems.append(
                Problem(
                    day_pattern.name,
                    f"{format_time(period.start)}-{format_time(period.end)} overlaps "
                    f"the period ending {format_time(cursor)}",
                )
            )
        cursor = max(cursor, period.end)

    if cursor < MINUTES_PER_DAY:
        problems.append(
            Problem(day_pattern.name, f"nothing covers {format_time(cursor)}-24:00")
        )

    return problems


def validate_day_coverage(plan: Plan) -> list[Problem]:
    """Check that every day type resolves to exactly one day set."""
    problems: list[Problem] = []
    # Two probe dates a season apart catch a range that covers only part of the
    # year without walking all 366 days.
    probes = _season_probe_dates(plan)

    for token in ALL_DAY_TOKENS:
        for probe in probes:
            matches = [
                day_pattern.name
                for day_pattern in plan.day_patterns
                if day_pattern.matches(token, probe)
            ]
            seasonal = [
                name
                for name in matches
                if _named(plan, name) is not None and _named(plan, name).is_seasonal  # type: ignore[union-attr]
            ]
            general = [name for name in matches if name not in seasonal]
            if not matches:
                problems.append(
                    Problem("", f"no day set covers {token} on {probe.isoformat()}")
                )
            elif len(seasonal) > 1:
                problems.append(
                    Problem(
                        "",
                        f"{token} on {probe.isoformat()} is claimed by more than one "
                        f"seasonal timetable: {', '.join(sorted(seasonal))}",
                    )
                )
            elif not seasonal and len(general) > 1:
                problems.append(
                    Problem(
                        "",
                        f"{token} is claimed by more than one timetable: "
                        f"{', '.join(sorted(general))}",
                    )
                )

    return problems


def _named(plan: Plan, name: str) -> DayPattern | None:
    for day_pattern in plan.day_patterns:
        if day_pattern.name == name:
            return day_pattern
    return None


def _season_probe_dates(plan: Plan) -> list[date]:
    """Return one date inside each declared season plus the four quarters."""
    year = 2001  # A non-leap year, so 02-29 never appears as a probe.
    probes = {date(year, month, 15) for month in (1, 4, 7, 10)}
    for day_pattern in plan.day_patterns:
        for edge in (day_pattern.season_from, day_pattern.season_to):
            if edge is not None:
                month, day = edge
                probes.add(date(year, month, min(day, 28)))
    return sorted(probes)


def validate_export(plan: Plan) -> list[Problem]:
    """Check the export side when it is not a single all-day price."""
    problems: list[Problem] = []
    names = set(plan.export_rate_names)

    for pattern in plan.day_patterns:
        if pattern.export_same_all_day:
            continue
        periods = pattern.sorted_export_periods()
        if not periods:
            problems.append(Problem(f"{pattern.name} export", "has no time periods"))
            continue
        cursor = 0
        for period in periods:
            if period.rate not in names:
                problems.append(
                    Problem(
                        f"{pattern.name} export",
                        f"{format_time(period.start)}-{format_time(period.end)} names "
                        f"an export rate '{period.rate}' that does not exist",
                    )
                )
            if period.start > cursor:
                problems.append(
                    Problem(
                        f"{pattern.name} export",
                        f"nothing covers {format_time(cursor)}-{format_time(period.start)}",
                    )
                )
            elif period.start < cursor:
                problems.append(
                    Problem(
                        f"{pattern.name} export",
                        f"{format_time(period.start)}-{format_time(period.end)} overlaps "
                        f"the period ending {format_time(cursor)}",
                    )
                )
            cursor = max(cursor, period.end)
        if cursor < MINUTES_PER_DAY:
            problems.append(
                Problem(
                    f"{pattern.name} export",
                    f"nothing covers {format_time(cursor)}-24:00",
                )
            )
    return problems


def validate_rates(plan: Plan) -> list[Problem]:
    """Check rate identity, references and allowance fallbacks."""
    problems: list[Problem] = []

    if not plan.rates:
        problems.append(Problem("", "the plan has no rates"))

    # A rate is identified by its timetable and its name together, so two
    # timetables can each have a Peak at a different price.
    pairs = [(rate.timetable, rate.name) for rate in plan.rates]
    if len(set(pairs)) != len(pairs):
        problems.append(Problem("", "two rates in the same timetable share a name"))

    # The qualified form is what entities and utility meter tariffs are named
    # by, so it has to be unique even when the names it is built from are not
    # identical. 'Off Peak' and 'off-peak' both reduce to off_peak.
    identifiers = [rate.qualified_name for rate in plan.rates]
    clashing = sorted({name for name in identifiers if identifiers.count(name) > 1})
    if clashing:
        problems.append(
            Problem("", f"two rates end up with the same id: {', '.join(clashing)}")
        )

    for rate in plan.rates:
        stray = rate.enforceable_constraints - rate.constraints
        if stray:
            problems.append(
                Problem(
                    rate.name,
                    "declares rules enforceable that it does not carry: "
                    f"{', '.join(sorted(stray))}",
                )
            )

    for rate in plan.rates:
        if rate.has_allowance:
            if not rate.fallback_rate:
                problems.append(
                    Problem(
                        rate.name,
                        "has an allowance but no fallback rate for beyond it",
                    )
                )
            else:
                # A fallback is looked up in the rate's own timetable first,
                # so a weekday rate falls back to the weekday's rate.
                fallback = plan.rate_by_name(rate.fallback_rate, rate.timetable)
                if fallback is None:
                    problems.append(
                        Problem(
                            rate.name,
                            f"names a fallback rate '{rate.fallback_rate}' "
                            "that does not exist",
                        )
                    )
                elif fallback.has_allowance:
                    problems.append(
                        Problem(
                            rate.name,
                            f"falls back to '{fallback.name}', which itself has an "
                            "allowance",
                        )
                    )

    for day_pattern in plan.day_patterns:
        for period in day_pattern.periods:
            if plan.rate_by_name(period.rate, day_pattern.name) is None:
                problems.append(
                    Problem(
                        day_pattern.name,
                        f"{format_time(period.start)}-{format_time(period.end)} names "
                        f"a rate '{period.rate}' that does not exist",
                    )
                )

    return problems


def validate_plan(plan: Plan) -> list[Problem]:
    """Run every check and return all problems found."""
    problems: list[Problem] = []

    if not plan.day_patterns:
        problems.append(Problem("", "the plan has no day sets"))

    problems.extend(validate_rates(plan))
    for day_pattern in plan.day_patterns:
        problems.extend(validate_periods(day_pattern))
    problems.extend(validate_day_coverage(plan))
    problems.extend(validate_export(plan))

    if (
        plan.valid_from is not None
        and plan.valid_to is not None
        and plan.valid_to < plan.valid_from
    ):
        problems.append(Problem("", "the plan's validity ends before it starts"))

    # A billing cycle starts on the same day every month, so the day has to be
    # one that every month has. A retailer does not bill on the 31st.
    if plan.billing_cycle_day is not None and not (
        1 <= plan.billing_cycle_day <= MAX_BILLING_CYCLE_DAY
    ):
        problems.append(
            Problem(
                "",
                f"the billing cycle starts on day {plan.billing_cycle_day}, which "
                f"does not exist in every month; use 1 to {MAX_BILLING_CYCLE_DAY}",
            )
        )

    return problems


def is_valid(plan: Plan) -> bool:
    """Return whether the plan passes every check."""
    return not validate_plan(plan)


def rates_capped_across_midnight(plan: Plan) -> list[Rate]:
    """Return capped rates that run through midnight.

    A period cannot cross midnight in this model — the day has to be covered
    exactly once from 00:00 to 24:00 — so a capped stretch running 22:00 to
    02:00 is entered as two periods naming the same rate, one either side.
    Finding the same rate at both ends of a day is how that is spotted.
    """
    found: list[Rate] = []
    for day_pattern in plan.day_patterns:
        ends = {
            period.rate
            for period in day_pattern.periods
            if period.end == MINUTES_PER_DAY
        }
        starts = {period.rate for period in day_pattern.periods if period.start == 0}
        for name in sorted(ends & starts):
            rate = plan.rate_by_name(name, day_pattern.name)
            if rate is not None and rate.has_allowance and rate not in found:
                found.append(rate)
    return found


def plan_warnings(plan: Plan) -> list[Problem]:
    """Return advice that does not stop the plan being saved.

    A warning is a configuration that is perfectly legitimate but where only
    the user knows whether it is what they meant. Refusing to save would be
    wrong; saying nothing would be worse.
    """
    warnings: list[Problem] = []
    for rate in rates_capped_across_midnight(plan):
        warnings.append(
            Problem(
                rate.name,
                "is capped and is entered as two periods either side of "
                "midnight. The allowance belongs to the slot, so each of the "
                "two gets its own — one unbroken stretch will be given its "
                "full allowance twice. Check how your retailer counts it.",
            )
        )
    return warnings
