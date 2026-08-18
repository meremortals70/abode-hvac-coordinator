"""Daily energy allowance accounting. Pure module.

Some plans give a period free or discounted only up to a daily cap — three free
hours capped at 24 kWh, for instance. Past the cap, consumption is priced at a
nominated fallback rate.
"""

from __future__ import annotations

from dataclasses import dataclass

from .plan import Plan, Rate


@dataclass(frozen=True, slots=True)
class AllowanceState:
    """The effective rate once the allowance has been taken into account."""

    rate: Rate
    exhausted: bool
    used_kwh: float
    remaining_kwh: float | None

    @property
    def reason(self) -> str:
        """Return a short explanation for the trace.

        No live figures. The trace is published as a sensor attribute, so a
        number that moves with every meter reading rewrites the attribute, and
        the entity state with it, several times a minute for no gain. How much
        allowance is left is published properly by its own sensor.
        """
        if self.remaining_kwh is None:
            return "no allowance on this rate"
        if self.exhausted:
            return "allowance spent"
        return "within the allowance"


def apply(plan: Plan, rate: Rate, used_kwh: float) -> AllowanceState:
    """Return the rate actually in force given today's consumption so far."""
    if not rate.has_allowance:
        return AllowanceState(
            rate=rate, exhausted=False, used_kwh=used_kwh, remaining_kwh=None
        )

    assert rate.daily_allowance_kwh is not None
    remaining = max(0.0, rate.daily_allowance_kwh - used_kwh)

    if remaining > 0:
        return AllowanceState(
            rate=rate, exhausted=False, used_kwh=used_kwh, remaining_kwh=remaining
        )

    # Looked up in the rate's own timetable first, so a weekday rate falls back
    # to the weekday's rate and not to a weekend rate of the same name.
    fallback = (
        plan.rate_by_name(rate.fallback_rate, rate.timetable)
        if rate.fallback_rate
        else None
    )
    # Validation guarantees a fallback exists, but a plan can be loaded from
    # storage written by an older version, so fall back to the rate itself.
    return AllowanceState(
        rate=fallback or rate,
        exhausted=True,
        used_kwh=used_kwh,
        remaining_kwh=0.0,
    )


def accumulate(
    previous_total: float | None, new_total: float | None, used_kwh: float
) -> float:
    """Add the delta of a monotonic energy meter to today's usage.

    A meter that resets, or that reports nothing, contributes nothing rather
    than a spurious negative or a spike.
    """
    if previous_total is None or new_total is None:
        return used_kwh
    delta = new_total - previous_total
    if delta <= 0:
        return used_kwh
    return used_kwh + delta
