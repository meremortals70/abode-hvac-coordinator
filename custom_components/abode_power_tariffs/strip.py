"""The 24-hour text strip. Pure module.

One line per day set, one character per 30 minutes. Shown on the Configure menu
and on every screen that touches periods, so a gap or an overlap is visible at
the moment it is made rather than at save.
"""

from __future__ import annotations

from .const import MINUTES_PER_DAY
from .plan import DayPattern, Plan, format_time
from .validate import validate_periods

SLOT_MINUTES = 30
SLOTS = MINUTES_PER_DAY // SLOT_MINUTES

# Coloured by price rank, cheapest to dearest. Emoji squares are used because
# the frontend renders the strip as markdown and strips styling, but renders
# emoji in colour. They are all double width, so the columns still line up.
SCALE = ("\U0001f7e9", "\U0001f7e6", "\U0001f7e8", "\U0001f7e7", "\U0001f7e5")
GAP = "\u2b1c"
CLASH = "\u274c"

RULER = "".join(f"{hour:<12}" for hour in range(0, 24, 3)).rstrip() + "  24"


def _rate_symbols(plan: Plan) -> dict[str, str]:
    """Map each rate to a colour, cheapest green through dearest red."""
    if not plan.rates:
        return {}
    ranked = sorted(plan.rates, key=lambda rate: rate.import_price)
    last = max(len(ranked) - 1, 1)
    return {
        rate.name: SCALE[round(position / last * (len(SCALE) - 1))]
        for position, rate in enumerate(ranked)
    }


def render_legend(plan: Plan) -> str:
    """Return one line per rate, colour then name then price."""
    symbols = _rate_symbols(plan)
    return "\n".join(
        f"{symbols[rate.name]} {rate.name} — {rate.import_price * 100:.2f} c/kWh"
        for rate in sorted(plan.rates, key=lambda rate: rate.import_price)
    )


def _export_symbols(plan: Plan) -> dict[str, str]:
    """Map each export rate to a colour, dearest green because more is better."""
    if not plan.export_rates:
        return {}
    ranked = sorted(plan.export_rates, key=lambda rate: -rate.price)
    last = max(len(ranked) - 1, 1)
    return {
        rate.name: SCALE[round(position / last * (len(SCALE) - 1))]
        for position, rate in enumerate(ranked)
    }


def render_export_row(plan: Plan, day_pattern: DayPattern) -> str:
    """Return the export strip, or a one-line note when it is flat."""
    if day_pattern.export_same_all_day:
        return f"Export: {day_pattern.export_flat_price * 100:.2f} c/kWh all day"
    symbols = _export_symbols(plan)
    slots = [GAP] * SLOTS
    for period in day_pattern.sorted_export_periods():
        symbol = symbols.get(period.rate, "?")
        first = period.start // SLOT_MINUTES
        last = min(SLOTS, -(-period.end // SLOT_MINUTES))
        for index in range(first, last):
            slots[index] = CLASH if slots[index] != GAP else symbol
    legend = "\n".join(
        f"{symbols[rate.name]} {rate.name} - {rate.price * 100:.2f} c/kWh export"
        for rate in sorted(plan.export_rates, key=lambda rate: -rate.price)
    )
    return "Export\n" + "".join(slots) + "\n" + legend


def render_day_pattern(plan: Plan, day_pattern: DayPattern) -> str:
    """Render one day set as a strip, a legend line and a coverage line."""
    symbols = _rate_symbols(plan)
    slots = [GAP] * SLOTS

    for period in day_pattern.sorted_periods():
        symbol = symbols.get(period.rate, "?")
        first = period.start // SLOT_MINUTES
        last = min(SLOTS, -(-period.end // SLOT_MINUTES))
        for index in range(first, last):
            slots[index] = CLASH if slots[index] != GAP else symbol

    strip = "".join(slots)

    problems = validate_periods(day_pattern)
    if problems:
        coverage = "  " + "\n  ".join(problem.message for problem in problems)
    else:
        coverage = (
            f"  Coverage: complete. {len(day_pattern.periods)} periods, no gaps, "
            "no overlaps."
        )

    season = ""
    if day_pattern.is_seasonal:
        assert day_pattern.season_from is not None
        assert day_pattern.season_to is not None
        season = (
            f"  ({day_pattern.season_from[1]:02d}/{day_pattern.season_from[0]:02d}"
            f" - {day_pattern.season_to[1]:02d}/{day_pattern.season_to[0]:02d})"
        )

    return "\n".join(
        [
            f"{day_pattern.name}{season}",
            RULER,
            strip,
            "",
            coverage,
            "",
            render_export_row(plan, day_pattern),
        ]
    )


def render_plan(plan: Plan) -> str:
    """Render every day pattern in the plan, with a colour legend."""
    if not plan.rates:
        return "No rates entered yet."
    legend = render_legend(plan)
    if not plan.day_patterns:
        return f"No day patterns configured yet.\n\n{legend}"
    body = "\n\n".join(
        render_day_pattern(plan, day_pattern) for day_pattern in plan.day_patterns
    )
    return f"{body}\n\n{legend}"


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
            rate = plan.rate_by_name(period.rate)
            if rate is None:
                lines.append(
                    f"  {format_time(period.start)}-{format_time(period.end)}"
                    f"  {period.rate}  (rate missing)"
                )
                continue
            lines.append(
                f"  {format_time(period.start)}-{format_time(period.end)}"
                f"  {rate.name:<12}"
                f"  buy {rate.import_price:.4f}/kWh"
                f"  sell {rate.export_price:.4f}/kWh"
            )
        lines.append("")

    return "\n".join(lines).rstrip()
