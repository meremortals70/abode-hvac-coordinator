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
                "climate_entity_ids": list(room.climate_entity_ids),
                "head_groups": dict(room.head_groups),
                "outdoor_units": list(room.groups),
                "temperature_entity_id": room.temperature_entity_id,
                "humidity_entity_id": room.humidity_entity_id,
                "presence_entity_id": room.presence_entity_id,
                "opening_entity_ids": list(room.opening_entity_ids),
                "cover_entity_ids": list(room.cover_entity_ids),
                "sleep_schedule_entity_id": room.sleep_schedule_entity_id,
                "direct_sun_entity_id": room.direct_sun_entity_id,
                "heat_load_entity_id": room.heat_load_entity_id,
                "window_direction": room.window_direction,
                "overhang_projection_m": room.overhang_projection_m,
                "overhang_height_m": room.overhang_height_m,
                "lockout_reason": room.lockout_reason,
                "allow_comfort_reduction": room.allow_comfort_reduction,
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
        # Which attribute answered the compressor question, per room. A room
        # reading `hvac_mode` is learning from mode rather than action and its
        # sensible coefficient is diluted by idle time.
        #
        # Top level rather than inside `models`: every key in there is a
        # coefficient with a value, variance, sample count and converged flag,
        # and a string among them breaks a shape something may iterate over.
        # A room whose climate entity has never had a state is absent rather
        # than null — absent means the entity was not there, "hvac_mode" means
        # it was and published no action, and those are different problems.
        "action_sources": coordinator.action_sources(),
        # Keyed by outdoor unit, not by room. Two rooms sharing a compressor
        # appear once.
        "compressors": {
            group: {
                "running": state.running,
                "changed_at": state.changed_at.isoformat()
                if state.changed_at
                else None,
            }
            for group, state in coordinator.compressor_state().items()
        },
        # Learned draw per outdoor unit group (0.8.9, finding 14). Only
        # groups actually seen this session — a group with no entry has not
        # had a rated-kW figure asked of it yet and is still on
        # `ASSUMED_UNIT_KW` everywhere it is used.
        "draw": {
            group: model.diagnostics()
            for group, model in coordinator.draw_models().items()
        },
        # 0.8.10, findings 10/11/15. Per-room budget, ceiling and bin are on
        # each room's own trace; this is the shared, house-level half.
        "power": coordinator.power_state(),
        "forecast": (
            coordinator.forecast.as_attributes() if coordinator.forecast else None
        ),
        "outdoor_temperature_entity_id": coordinator.outdoor_entity_id,
        "outdoor_humidity_entity_id": coordinator.outdoor_humidity_entity_id,
        "outdoor_wind_entity_id": coordinator.outdoor_wind_entity_id,
        "outdoor_apparent_c": coordinator.outdoor_apparent_temperature(),
        "outdoor_dew_point_c": coordinator.outdoor_dew_point(),
        "weather": {
            "entity_id": coordinator.weather_entity_id,
            "fetched_at": (
                coordinator.trajectory.fetched_at.isoformat()
                if coordinator.trajectory
                else None
            ),
            "covers_until": (
                coordinator.trajectory.covers_until.isoformat()
                if coordinator.trajectory and coordinator.trajectory.covers_until
                else None
            ),
            "hours": (
                len(coordinator.trajectory.points) if coordinator.trajectory else 0
            ),
            "peak_c": coordinator.forecast_peak(),
            "peak_at": coordinator.forecast_peak_at(),
        },
        # The trim, per room. Cycling state moved to `compressors` in 0.8.8,
        # because it belongs to the outdoor unit and two rooms can share one.
        "regulation": {
            room_id: {
                "trim_c": round(state.trim_c, 3),
                "updated_at": (
                    state.updated_at.isoformat() if state.updated_at else None
                ),
            }
            for room_id, state in coordinator.regulation_state().items()
        },
        "traces": {
            room_id: trace.as_attributes()
            for room_id, trace in (coordinator.data or {}).items()
        },
    }
