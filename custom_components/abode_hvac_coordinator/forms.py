"""Pure helpers for turning setup forms into stored configuration.

No Home Assistant imports, so the shaping of configuration can be tested
directly. `config_flow.py` owns the schemas and the step sequence; everything
here is data in, data out.
"""

from __future__ import annotations

import re
from typing import Any

from .const import (
    CONF_ALLOW_COMFORT_REDUCTION,
    CONF_ALLOW_COVER_CONTROL,
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_TARGETS,
    CONF_BAND_HIGH,
    CONF_BAND_LOW,
    CONF_BANDS,
    CONF_CLIMATE_ENTITIES,
    CONF_COVER_ENTITIES,
    CONF_DIRECT_SUN_ENTITY,
    CONF_FAN_ENTITY,
    CONF_HEAD_GROUPS,
    CONF_HEAT_LOAD_ENTITY,
    CONF_HUMIDITY_ENTITY,
    CONF_LOCKOUT_REASON,
    CONF_OCCUPIED_AFTER,
    CONF_OPENING_ENTITIES,
    CONF_OVERHANG_HEIGHT,
    CONF_OVERHANG_PROJECTION,
    CONF_PRESENCE_ENTITY,
    CONF_ROOM_ID,
    CONF_SLEEP_SCHEDULE_ENTITY,
    CONF_TEMPERATURE_ENTITY,
    CONF_VACANT_AFTER,
    CONF_WARNING_GRACE,
    CONF_WINDOW_DIRECTION,
    DEFAULT_BANDS,
    DEFAULT_LOCKOUT_REASONS,
    NOT_LOCKED_OUT,
    OWN_OUTDOOR_UNIT,
)
from .grace import (
    DEFAULT_OCCUPIED_AFTER,
    DEFAULT_VACANT_AFTER,
    DEFAULT_WARNING_GRACE,
)
from .models import Mode

#: Modes that carry a band of their own. Unoccupied is off, precondition uses
#: the occupied band, coast inherits, lockout never actuates.
BAND_MODES = (Mode.OCCUPIED, Mode.SLEEP, Mode.PRECOOL)


def slug(name: str) -> str:
    """Derive a stable room id from the room name."""
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")


def room_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn the room form into a stored room.

    The lockout reason is deliberately left empty here. It is filled in by the
    lockout step, which is only reached when the box was ticked, so an
    unticked room can never carry a reason left over from a previous edit.
    """
    return {
        CONF_ROOM_ID: slug(user_input["name"]),
        "name": user_input["name"],
        CONF_CLIMATE_ENTITIES: list(user_input[CONF_CLIMATE_ENTITIES]),
        CONF_TEMPERATURE_ENTITY: user_input.get(CONF_TEMPERATURE_ENTITY),
        CONF_HUMIDITY_ENTITY: user_input.get(CONF_HUMIDITY_ENTITY),
        CONF_PRESENCE_ENTITY: user_input.get(CONF_PRESENCE_ENTITY),
        CONF_SLEEP_SCHEDULE_ENTITY: user_input.get(CONF_SLEEP_SCHEDULE_ENTITY),
        CONF_DIRECT_SUN_ENTITY: user_input.get(CONF_DIRECT_SUN_ENTITY),
        CONF_HEAT_LOAD_ENTITY: user_input.get(CONF_HEAT_LOAD_ENTITY),
        CONF_FAN_ENTITY: user_input.get(CONF_FAN_ENTITY),
        CONF_WINDOW_DIRECTION: user_input.get(CONF_WINDOW_DIRECTION),
        CONF_OVERHANG_PROJECTION: user_input.get(CONF_OVERHANG_PROJECTION),
        CONF_OVERHANG_HEIGHT: user_input.get(CONF_OVERHANG_HEIGHT),
        CONF_OPENING_ENTITIES: user_input.get(CONF_OPENING_ENTITIES, []),
        CONF_COVER_ENTITIES: user_input.get(CONF_COVER_ENTITIES, []),
        CONF_ALLOW_COVER_CONTROL: bool(
            user_input.get(CONF_ALLOW_COVER_CONTROL, True)
        ),
        CONF_OCCUPIED_AFTER: user_input.get(CONF_OCCUPIED_AFTER),
        CONF_VACANT_AFTER: user_input.get(CONF_VACANT_AFTER),
        CONF_WARNING_GRACE: user_input.get(CONF_WARNING_GRACE),
        CONF_ANNOUNCE: bool(user_input.get(CONF_ANNOUNCE, False)),
        CONF_ANNOUNCE_TARGETS: user_input.get(CONF_ANNOUNCE_TARGETS, []),
        CONF_LOCKOUT_REASON: _lockout_reason(user_input.get(CONF_LOCKOUT_REASON)),
        CONF_ALLOW_COMFORT_REDUCTION: bool(
            user_input.get(CONF_ALLOW_COMFORT_REDUCTION, False)
        ),
    }


def head_groups_from_input(user_input: dict[str, Any]) -> dict[str, str]:
    """The outdoor-unit step's answers, as entity id to group name.

    The sentinel first option is dropped rather than stored. A head with no
    entry is on an outdoor unit of its own, which is the same statement and
    leaves nothing behind if the head is later moved to a different room.
    """
    return {
        entity_id: str(group).strip()
        for entity_id, group in user_input.items()
        if group and str(group).strip() not in ("", OWN_OUTDOOR_UNIT)
    }


def known_head_groups(stored: list[str]) -> list[str]:
    """Every outdoor unit group any room has named, in a stable order."""
    return sorted(set(stored))


def extend_head_groups(stored: list[str], room: dict[str, Any]) -> list[str]:
    """Add this room's outdoor unit names to the list a later room picks from.

    Kept even when the room that named a group is removed. The name costs
    nothing to offer, and a house that re-adds the room should not have to
    retype it.
    """
    named = {
        str(group).strip()
        for group in (room.get(CONF_HEAD_GROUPS) or {}).values()
        if str(group).strip()
    }
    return sorted({*stored, *named})


def _lockout_reason(chosen: str | None) -> str | None:
    """The stored lockout reason, or None when the room is not locked out.

    One dropdown answers both questions. The first option means not locked out,
    so there is no toggle to tick and no second screen to reach.
    """
    if chosen is None:
        return None
    reason = str(chosen).strip()
    if not reason or reason == NOT_LOCKED_OUT:
        return None
    return reason


def bands_from_input(user_input: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Collect only the bands where both bounds were supplied."""
    bands: dict[str, dict[str, float]] = {}
    for mode in BAND_MODES:
        low = user_input.get(f"{mode}_{CONF_BAND_LOW}")
        high = user_input.get(f"{mode}_{CONF_BAND_HIGH}")
        if low is not None and high is not None:
            bands[str(mode)] = {CONF_BAND_LOW: float(low), CONF_BAND_HIGH: float(high)}
    return bands


def bands_are_valid(bands: dict[str, dict[str, float]]) -> bool:
    """Every configured band must have its low below its high."""
    return all(
        values[CONF_BAND_LOW] < values[CONF_BAND_HIGH] for values in bands.values()
    )


def bands_as_suggestions(bands: dict[str, dict[str, float]]) -> dict[str, float]:
    """Flatten stored bands back into form field values, for editing."""
    return {
        f"{mode}_{bound}": values[bound]
        for mode, values in bands.items()
        for bound in (CONF_BAND_LOW, CONF_BAND_HIGH)
        if bound in values
    }


def default_band_suggestions() -> dict[str, float]:
    """The seeded bands, flattened into form field values.

    Every room starts from the same numbers, so a fresh install is consistent
    and needs no configuration to be sensible.
    """
    return bands_as_suggestions(DEFAULT_BANDS)


def default_grace_suggestions() -> dict[str, float | bool]:
    """Grace timings the room form arrives pre-filled with."""
    return {
        CONF_OCCUPIED_AFTER: DEFAULT_OCCUPIED_AFTER.total_seconds() / 60,
        CONF_VACANT_AFTER: DEFAULT_VACANT_AFTER.total_seconds() / 60,
        CONF_WARNING_GRACE: DEFAULT_WARNING_GRACE.total_seconds() / 60,
        CONF_ANNOUNCE: False,
    }


def known_lockout_reasons(stored: list[str]) -> list[str]:
    """The lockout dropdown: not-locked-out first, then every known reason."""
    return [NOT_LOCKED_OUT, *sorted({*DEFAULT_LOCKOUT_REASONS, *stored})]


def extend_lockout_reasons(stored: list[str], room: dict[str, Any]) -> list[str]:
    """Add this room's reason to the stored list if it is a new custom one.

    Built-in reasons are not stored: they are always offered, and storing them
    would leave stale copies behind if the built-in list ever changed.
    """
    reason = room.get(CONF_LOCKOUT_REASON)
    if (
        not reason
        or reason == NOT_LOCKED_OUT
        or reason in DEFAULT_LOCKOUT_REASONS
        or reason in stored
    ):
        return sorted(stored)
    return sorted([*stored, reason])


def describe_room(room: dict[str, Any]) -> str:
    """A room's whole configuration, as readable lines.

    Shown on the menu so the current settings can be read without opening the
    form that set them. A configuration you have to edit to inspect is a
    configuration nobody checks.
    """
    lines: list[str] = []

    def entry(label: str, value: Any, suffix: str = "") -> None:
        lines.append(f"  {label}: {value}{suffix}" if value else f"  {label}: —")

    lines.append(f"**{room.get('name', '?')}**")
    heads = room.get(CONF_CLIMATE_ENTITIES) or []
    groups = room.get(CONF_HEAD_GROUPS) or {}
    entry("Air conditioners", ", ".join(heads))
    for head in heads:
        if groups.get(head):
            lines.append(f"    {head} → outdoor unit: {groups[head]}")
    entry("Temperature", room.get(CONF_TEMPERATURE_ENTITY))
    entry("Humidity", room.get(CONF_HUMIDITY_ENTITY))
    entry("Presence", room.get(CONF_PRESENCE_ENTITY))
    entry("Sleep schedule", room.get(CONF_SLEEP_SCHEDULE_ENTITY))
    entry("Heat source", room.get(CONF_HEAT_LOAD_ENTITY))
    entry("Air movement", room.get(CONF_FAN_ENTITY))
    entry("Windows face", room.get(CONF_WINDOW_DIRECTION))

    projection = room.get(CONF_OVERHANG_PROJECTION)
    if projection:
        height = room.get(CONF_OVERHANG_HEIGHT)
        lines.append(f"  Overhang: {projection} m out, {height} m above the glass")
    else:
        lines.append("  Overhang: none")

    openings = room.get(CONF_OPENING_ENTITIES) or []
    covers = room.get(CONF_COVER_ENTITIES) or []
    lines.append(f"  Windows and doors: {len(openings) or '—'}")
    if covers and not room.get(CONF_ALLOW_COVER_CONTROL, True):
        lines.append(f"  Blinds: {len(covers)} configured, control disabled")
    else:
        lines.append(f"  Blinds: {len(covers) or '—'}")

    bands = room.get(CONF_BANDS) or {}
    if bands:
        described = ", ".join(
            f"{mode} {v[CONF_BAND_LOW]}–{v[CONF_BAND_HIGH]}"
            for mode, v in sorted(bands.items())
        )
        lines.append(f"  Bands: {described}")
    else:
        lines.append("  Bands: none — this room will never be actuated")

    lines.append(
        "  Waiting: {} min to start, {} min to stop".format(
            room.get(CONF_OCCUPIED_AFTER, "?"), room.get(CONF_VACANT_AFTER, "?")
        )
    )
    if room.get(CONF_ANNOUNCE):
        targets = room.get(CONF_ANNOUNCE_TARGETS) or []
        lines.append(f"  Announces before shutdown through {len(targets)} player(s)")

    reason = room.get(CONF_LOCKOUT_REASON)
    if reason:
        lines.append(f"  **LOCKED OUT — {reason}**")

    return "\n".join(lines)


def describe_rooms(rooms: list[dict[str, Any]]) -> str:
    """Every room and every setting on it."""
    if not rooms:
        return "No rooms configured yet."
    return "\n".join(describe_room(room) for room in rooms)


def describe_tariff(tariff_title: str | None) -> str:
    """Where the tariff comes from, or that none is selected.

    The plan itself is not shown: it lives in Abode Power Tariffs and is
    displayed there. Repeating it here would be a second copy to keep in step.
    """
    if not tariff_title:
        return (
            "Nothing selected. Without a tariff the controller holds comfort "
            "and nothing else: no precool window, no import constraint and no "
            "costed forecast."
        )
    return f"{tariff_title} (Abode Power Tariffs)"


def describe_global(
    tariff_title: str | None,
    outdoor_entity_id: str | None,
    outdoor_humidity_entity_id: str | None = None,
    outdoor_wind_entity_id: str | None = None,
    weather_entity_id: str | None = None,
) -> str:
    """The house-wide settings, as readable lines."""
    return "\n".join(
        [
            "**Tariff**",
            f"  {describe_tariff(tariff_title)}",
            "",
            "**Outdoor temperature**",
            f"  {outdoor_entity_id or 'Nothing selected'}",
            "",
            "**Outdoor humidity**",
            f"  {outdoor_humidity_entity_id or 'Nothing selected'}"
            + (
                ""
                if outdoor_humidity_entity_id
                else " — without it there is no free-cooling advice at all"
            ),
            "",
            "**Outdoor wind**",
            f"  {outdoor_wind_entity_id or 'Nothing selected'}"
            + (
                ""
                if outdoor_wind_entity_id
                else " — still air is assumed, so a breezy evening will be "
                "judged as though it were calm"
            ),
            "",
            "**Weather forecast**",
            f"  {weather_entity_id or 'Nothing selected'}"
            + (
                ""
                if weather_entity_id
                else " — precool falls back to comparing conditions right now, "
                "which at midday cannot see the afternoon coming"
            ),
        ]
    )


def describe_power(
    battery_soc_entity_id: str | None,
    battery_capacity_kwh: str | None,
    solar_entity_id: str | None,
    house_load_entity_id: str | None,
    reserve_margin_kwh: str | None,
    grid_entity_id: str | None = None,
    battery_max_discharge_kw: str | None = None,
) -> str:
    """The power-aware settings, as readable lines.

    The first five are required together before any of this engages. Showing
    which are missing is more useful than a single on/off line, because a
    partial setup is the state most installs will pass through on the way to
    a complete one.

    The grid sensor (0.8.10, finding 11) is not part of that group — the
    budget still runs on the energy figure alone without it, just without a
    measured breach figure afterward.

    The maximum discharge power is a nameplate spec, entered rather than
    learned — see `CONF_BATTERY_MAX_DISCHARGE_KW`. Optional: without it the
    budget is bounded only by the energy figure, not by what the inverter
    can physically deliver.
    """
    fields = {
        "Battery charge sensor": battery_soc_entity_id,
        "Battery usable capacity": (
            f"{battery_capacity_kwh} kWh" if battery_capacity_kwh else None
        ),
        "Solar output sensor": solar_entity_id,
        "House load sensor": house_load_entity_id,
        "Reserve margin": (
            f"{reserve_margin_kwh} kWh" if reserve_margin_kwh else None
        ),
    }
    lines = [f"  {label}: {value or 'Nothing selected'}" for label, value in fields.items()]
    lines.append(
        "  Battery max discharge power: "
        + (f"{battery_max_discharge_kw} kW" if battery_max_discharge_kw else "Not set")
    )
    lines.append(f"  Grid power sensor: {grid_entity_id or 'Nothing selected'}")
    if all(fields.values()):
        lines.append(
            "  Active: while the tariff forbids grid import, a room whose "
            "occupant has permitted comfort reduction is throttled to what "
            "the battery affords rather than stopped. It is never turned "
            "off for this."
        )
        if not battery_max_discharge_kw:
            lines.append(
                "  Without the maximum discharge power, the throttle is "
                "bounded only by stored energy, not by what the battery can "
                "physically deliver."
            )
        if not grid_entity_id:
            lines.append(
                "  Without the grid sensor, no breach is measured afterward."
            )
    else:
        lines.append(
            "  Inactive — every field above must be set. Until then "
            "no_grid_import is observed but never acted on, exactly as "
            "before this feature existed."
        )
    return "\n".join(lines)


def describe_configuration(
    rooms: list[dict[str, Any]],
    tariff_title: str | None,
    outdoor_entity_id: str | None,
) -> str:
    """Everything currently configured, for the menu screen."""
    lines: list[str] = ["**Rooms**"]
    if rooms:
        lines.extend(describe_room(room) for room in rooms)
    else:
        lines.append("  None configured.")

    lines.append("")
    lines.append("**House**")
    lines.append(f"  Tariff: {describe_tariff(tariff_title)}")
    lines.append(f"  Outdoor temperature: {outdoor_entity_id or '—'}")
    return "\n".join(lines)
