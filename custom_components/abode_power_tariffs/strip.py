"""Rendering the plan as text. Pure module.

There used to be a coloured 24-hour bar here, drawn with emoji squares under a
character ruler. It could not be made to line up: the ruler is plain characters
and the squares are emoji, and their widths differ by font and platform, so the
bar drifted against the clock by hours on some browsers. A visual you cannot
read against the clock is worse than none.

So this renders the plan as plain text, which is exact everywhere, and the
picture is left to Home Assistant. `sensor.<name>_rate` is an enum, so a
built-in history-graph card draws it as a coloured timeline with a real time
axis. See the README.

One table. There used to be a second block underneath restating every rate and
its price with no times against them, which meant every price appeared twice
and the same rate was called two different things in the two places. The
identifier the second block existed to show is now a column of the first.
"""

from __future__ import annotations

from .plan import DayPattern, Plan, format_time
from .validate import validate_periods

# "00:00-24:00".
TIME_COLUMN = 11

MISSING = "rate missing"


def column_widths(plan: Plan) -> tuple[int, int]:
    """Return the name and identifier column widths for the whole plan.

    Measured once across every timetable, so two tables line up with each other
    and not merely each with itself. A fixed pad silently fails the moment a
    name outgrows it: the price is pushed right and the column bends round it.
    """
    names = 0
    identifiers = 0
    for day_pattern in plan.day_patterns:
        for period in (*day_pattern.periods, *day_pattern.export_periods):
            names = max(names, len(period.rate))
    for rate in plan.rates:
        names = max(names, len(rate.name))
        identifiers = max(identifiers, len(rate.qualified_name))
    for export_rate in plan.export_rates:
        names = max(names, len(export_rate.name))
    return names, identifiers


def name_width(plan: Plan) -> int:
    """Return the name column width on its own."""
    return column_widths(plan)[0]


def _row(
    time_cell: str,
    name: str,
    identifier: str,
    price: str,
    widths: tuple[int, int],
    indent: str = "  ",
) -> str:
    """Return one padded row of the table."""
    names, identifiers = widths
    return (
        f"{indent}{time_cell:<{TIME_COLUMN}}  {name:<{names}}  "
        f"{identifier:<{identifiers}}  {price}"
    ).rstrip()


def _price(cents: float) -> str:
    """Return a price cell, right-aligned on the decimal point."""
    return f"{cents:>7.2f} c/kWh"


def render_export_row(
    plan: Plan, day_pattern: DayPattern, widths: tuple[int, int] | None = None
) -> str:
    """Return the feed-in side of one timetable, in the same columns."""
    columns = column_widths(plan) if widths is None else widths
    if day_pattern.export_same_all_day:
        return f"  Feed-in: {day_pattern.export_flat_price * 100:.2f} c/kWh all day"

    lines = ["  Feed-in:"]
    for period in day_pattern.sorted_export_periods():
        rate = plan.export_rate_by_name(period.rate)
        lines.append(
            _row(
                f"{format_time(period.start)}-{format_time(period.end)}",
                period.rate,
                "",
                MISSING if rate is None else _price(rate.price * 100),
                columns,
                indent="    ",
            )
        )
    return "\n".join(lines)


def render_day_pattern(
    plan: Plan, day_pattern: DayPattern, widths: tuple[int, int] | None = None
) -> str:
    """Render one timetable as a table: when, what it is called, its id, its price."""
    columns = column_widths(plan) if widths is None else widths
    season = ""
    if day_pattern.is_seasonal:
        assert day_pattern.season_from is not None
        assert day_pattern.season_to is not None
        season = (
            f"  ({day_pattern.season_from[1]:02d}/{day_pattern.season_from[0]:02d}"
            f" - {day_pattern.season_to[1]:02d}/{day_pattern.season_to[0]:02d})"
        )

    lines = [f"{day_pattern.name}{season}"]
    for period in day_pattern.sorted_periods():
        rate = plan.rate_by_name(period.rate, day_pattern.name)
        lines.append(
            _row(
                f"{format_time(period.start)}-{format_time(period.end)}",
                period.rate,
                "" if rate is None else rate.qualified_name,
                MISSING if rate is None else _price(rate.import_price * 100),
                columns,
            )
        )

    problems = validate_periods(day_pattern)
    if problems:
        lines.extend(f"  {problem.message}" for problem in problems)
    else:
        lines.append(
            f"  Coverage: complete. {len(day_pattern.periods)} periods, no gaps, "
            "no overlaps."
        )

    lines.append(render_export_row(plan, day_pattern, columns))
    return "\n".join(lines)


def render_unscheduled(plan: Plan, widths: tuple[int, int]) -> str:
    """Return rows for rates no time period names.

    A fallback used only past an allowance has no period of its own, so a table
    keyed on periods would drop it. It is still part of the plan and still gets
    published, so it gets a row with the time cell empty.
    """
    scheduled = {
        (day_pattern.name, period.rate)
        for day_pattern in plan.day_patterns
        for period in day_pattern.periods
    }
    rows = [
        _row(
            "", rate.name, rate.qualified_name, _price(rate.import_price * 100), widths
        )
        for rate in plan.rates
        if not any(
            name == rate.name and (rate.timetable in (timetable, None))
            for timetable, name in scheduled
        )
    ]
    return "\n".join(rows)


def render_plan(plan: Plan) -> str:
    """Render every timetable in the plan as one table each."""
    if not plan.rates:
        return "No rates entered yet."
    widths = column_widths(plan)
    if not plan.day_patterns:
        rows = "\n".join(
            _row(
                "",
                rate.name,
                rate.qualified_name,
                _price(rate.import_price * 100),
                widths,
            )
            for rate in plan.rates
        )
        return f"No day patterns configured yet.\n{rows}"
    body = "\n\n".join(
        render_day_pattern(plan, day_pattern, widths)
        for day_pattern in plan.day_patterns
    )
    unscheduled = render_unscheduled(plan, widths)
    if unscheduled:
        return f"{body}\n\nNot on any timetable\n{unscheduled}"
    return body


def render_rate_plan_card(plan: Plan) -> str:
    """Render the plan in the shape an inverter's own tariff screen asks for.

    Tesla, Sungrow, Fronius and GoodWe all have that screen. This renders the
    plan for transcription; nothing is written to any vendor.
    """
    lines: list[str] = [f"Rate plan: {plan.name}", ""]
    if plan.description:
        lines.extend([plan.description, ""])

    if plan.prices_include_gst:
        lines.append(f"Prices include GST at {plan.gst_percent:g}%.")
    else:
        lines.append("Prices exclude tax.")
    lines.append(f"Daily supply charge: {plan.daily_supply_charge * 100:.2f} c/day")
    if plan.monthly_charge:
        lines.append(f"Monthly charge: {plan.monthly_charge:.2f} per month")
    if plan.billing_cycle_day:
        lines.append(
            f"Billing cycle starts on day {plan.billing_cycle_day} of the month"
        )
    lines.append("")

    for day_pattern in plan.day_patterns:
        lines.append(f"{day_pattern.name} — {', '.join(sorted(day_pattern.days))}")
        if day_pattern.is_seasonal:
            assert day_pattern.season_from is not None
            assert day_pattern.season_to is not None
            lines.append(
                f"  Season {day_pattern.season_from[1]:02d}/{day_pattern.season_from[0]:02d}"
                f" to {day_pattern.season_to[1]:02d}/{day_pattern.season_to[0]:02d}"
            )
        for period in day_pattern.sorted_periods():
            rate = plan.rate_by_name(period.rate, day_pattern.name)
            if rate is None:
                lines.append(
                    f"  {format_time(period.start)}-{format_time(period.end)}"
                    f"  {period.rate}  ({MISSING})"
                )
                continue
            lines.append(
                f"  {format_time(period.start)}-{format_time(period.end)}"
                f"  {rate.qualified_name:<12}"
                f"  buy {rate.import_price:.4f}/kWh"
            )
        lines.append(f"  {render_export_row(plan, day_pattern).strip()}")
        lines.append("")

    return "\n".join(lines).rstrip()
