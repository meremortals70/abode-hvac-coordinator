"""Rendering the plan as text, for diagnostics. Pure module.

Output only. There is no text import: with rates defined once, a period is
three fields, and a paste format would be a second way to enter the same thing
with every rate name an unvalidated string.
"""

from __future__ import annotations

import csv
import io

from .const import ALL_DAY_TOKENS
from .plan import Plan, format_time

PERIOD_HEADER = (
    "day_pattern",
    "days",
    "season_from",
    "season_to",
    "start",
    "end",
    "rate",
)
RATE_HEADER = (
    "rate",
    "import_c_per_kwh",
    "export_c_per_kwh",
    "constraints",
    "coasting_permitted",
    "daily_allowance_kwh",
    "fallback_rate",
)


def periods_to_csv(plan: Plan) -> str:
    """Render every period in the plan as CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(PERIOD_HEADER)
    for day_pattern in plan.day_patterns:
        days = " ".join(token for token in ALL_DAY_TOKENS if token in day_pattern.days)
        season_from = (
            f"{day_pattern.season_from[0]:02d}-{day_pattern.season_from[1]:02d}"
            if day_pattern.season_from
            else ""
        )
        season_to = (
            f"{day_pattern.season_to[0]:02d}-{day_pattern.season_to[1]:02d}"
            if day_pattern.season_to
            else ""
        )
        for period in day_pattern.sorted_periods():
            writer.writerow(
                [
                    day_pattern.name,
                    days,
                    season_from,
                    season_to,
                    format_time(period.start),
                    format_time(period.end),
                    period.rate,
                ]
            )
    return buffer.getvalue()


def rates_to_csv(plan: Plan) -> str:
    """Render every rate in the plan as CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(RATE_HEADER)
    for rate in plan.rates:
        writer.writerow(
            [
                rate.name,
                f"{rate.import_price * 100:.4f}",
                f"{rate.export_price * 100:.4f}",
                " ".join(sorted(rate.constraints)),
                "yes" if rate.coasting_permitted else "no",
                "" if rate.daily_allowance_kwh is None else rate.daily_allowance_kwh,
                rate.fallback_rate or "",
            ]
        )
    return buffer.getvalue()
