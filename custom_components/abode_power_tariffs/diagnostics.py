"""Diagnostics for Abode Power Tariffs."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import TariffConfigEntry
from .serialise import periods_to_csv, rates_to_csv
from .strip import render_plan, render_rate_plan_card
from .validate import validate_plan


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TariffConfigEntry
) -> dict[str, Any]:
    """Return everything needed to reason about a plan without the system."""
    coordinator = entry.runtime_data
    plan = coordinator.plan
    state = coordinator.state

    return {
        "plan": plan.as_dict(),
        "rates_csv": rates_to_csv(plan),
        "periods_csv": periods_to_csv(plan),
        "strip": render_plan(plan),
        "rate_plan_card": render_rate_plan_card(plan),
        "problems": [str(problem) for problem in validate_plan(plan)],
        "current": {
            "rate": state.effective_rate.name if state.effective_rate else None,
            "scheduled_rate": state.resolution.rate.name if state.resolution else None,
            "day_pattern": state.resolution.day_pattern.name
            if state.resolution
            else None,
            "next_change": state.next_change.isoformat() if state.next_change else None,
            "allowance_used_kwh": state.allowance_used_kwh,
            "allowance_remaining_kwh": state.allowance_remaining_kwh,
            "plan_expired": state.plan_expired,
            "trace": list(state.trace),
        },
        "options": {
            key: value
            for key, value in entry.options.items()
            if key not in ("rates", "day_patterns")
        },
        "intervals": [
            interval.as_dict() for interval in coordinator.forward_intervals(24, 60)
        ],
    }
