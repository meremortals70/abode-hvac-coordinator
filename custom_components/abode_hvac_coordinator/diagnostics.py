"""Diagnostics.

Dumps configuration and the current decision for every room. Entity IDs are
included: they are how the user's own configuration is identified, and without
them a diagnostics download cannot explain a wrong decision.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import HvacConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HvacConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "rooms": {
            room_id: {
                "name": room.name,
                "climate_entity_id": room.climate_entity_id,
                "temperature_entity_id": room.temperature_entity_id,
                "humidity_entity_id": room.humidity_entity_id,
                "presence_entity_id": room.presence_entity_id,
                "opening_entity_ids": list(room.opening_entity_ids),
                "cover_entity_ids": list(room.cover_entity_ids),
                "lockout_reason": room.lockout_reason,
                "bands": {
                    str(mode): {"low": band.low, "high": band.high}
                    for mode, band in room.bands.items()
                },
            }
            for room_id, room in coordinator.rooms.items()
        },
        "tariff": {
            "source_config_entry_id": coordinator.tariff_entry_id,
            "fetched_at": (
                coordinator.tariff.fetched_at.isoformat()
                if coordinator.tariff
                else None
            ),
            "covers_until": (
                coordinator.tariff.covers_until.isoformat()
                if coordinator.tariff and coordinator.tariff.covers_until
                else None
            ),
            "windows": (
                [
                    {
                        "start": window.start.isoformat(),
                        "end": window.end.isoformat(),
                        "rate": window.rate,
                        "per_kwh": window.per_kwh,
                        "constraints": sorted(window.constraints),
                        "coasting_permitted": window.coasting_permitted,
                    }
                    for window in coordinator.tariff.windows()
                ]
                if coordinator.tariff
                else None
            ),
        },
        "unrecognised_constraints": sorted(
            coordinator.tariff.unrecognised_constraints()
            if coordinator.tariff
            else []
        ),
        "models": {
            room_id: model.diagnostics()
            for room_id, model in coordinator.models.items()
        },
        "forecast": (
            coordinator.forecast.as_attributes() if coordinator.forecast else None
        ),
        "outdoor_temperature_entity_id": coordinator.outdoor_entity_id,
        "traces": {
            room_id: trace.as_attributes()
            for room_id, trace in (coordinator.data or {}).items()
        },
    }
