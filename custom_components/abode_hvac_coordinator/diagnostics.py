"""The diagnostics download (Section 7) [D052]."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import AbodeHvacConfigEntry

# the home's position never appears, even if a later change adds it [R18]
TO_REDACT = {"latitude", "longitude", "home", "location"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: AbodeHvacConfigEntry) -> dict[str, Any]:
    """Every room's settings, choices, learning, plan and trace; the last
    200 commands; the learned draw, the power budget, the tariff and the
    forecasts."""
    coordinator = entry.runtime_data.coordinator
    out = {"house": dict(entry.data), **coordinator.diagnostics()}
    redacted: dict[str, Any] = async_redact_data(out, TO_REDACT)
    return redacted
