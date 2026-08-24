"""Grid-aware power budgeting.

Pure. No Home Assistant imports. 0.8.10, findings 10 and 11.

Replaces the boolean `_power_available` veto with a kWh-and-ceiling
mechanism. The compressor was always continuously variable — the setpoint
and fan speed levers were already in `actuator.py` — what was missing was a
rate and a cost at an operating point, which 0.8.9's approach bins supply.

WHAT CHANGED FROM THE PRE-0.8.10 VETO
--------------------------------------
The old `_power_available` could refuse the compressor outright, above the
comfort band, on a positively computed shortfall. That refusal is retired
here, not narrowed. Comfort is the constraint, not the variable: the budget
now only ever shapes *how gently* the compressor runs, via a setpoint
ceiling, and never turns it off. A room whose battery genuinely cannot carry
it still runs; the shortfall is reported afterward from measured grid
import, not enforced beforehand from a projection.

The ceiling itself normally lifts the moment a room actually needs
correction (`demand` is not None) — the point where comfort, not cost, must
decide. `power_management` is the one room-level exception, and it is a
three-state setting, not a boolean: `off` (the default) never computes a
ceiling for this room; `guidance` computes and reports the ceiling — it is
on the trace and named in `reasons` — but never applies it, even once the
room needs correction; `enforced` is the pre-0.8.12 behaviour, keeping the
ceiling applying even then, because the room's occupant has said they would
rather run gently than run at whatever the compressor wants. Whether
`guidance` actually stops short of applying the ceiling is decided in
`coordinator._regulate`, not here — this module only ever computes the
ceiling and states its own state in `reasons`.
"""

from __future__ import annotations

#: A positive raw reading means the house is drawing from the grid.
GRID_SIGN_IMPORTING = "importing"
#: A positive raw reading means the house is feeding the grid.
GRID_SIGN_EXPORTING = "exporting"

#: Below this, `house_load - solar` is not decisive either way about the
#: grid reading's sign — solar could be covering the load, the battery could
#: be carrying it, or the grid could genuinely be near zero. No default is
#: offered in that case; the setup step says so rather than guessing.
AMBIGUOUS_BELOW_W = 500.0

#: A raw grid reading within this of zero is treated as no flow, not as a
#: flow whose direction happens to be indeterminate. There is no sign to
#: read off a number that means nothing moved — a `no_grid_import` window
#: on battery, with solar covering nothing overnight, sits here every time,
#: and it is the single most common reading this feature sees. Set above
#: any plausible float noise a real sensor could report at rest, and well
#: below any real, meaningful grid flow.
NO_FLOW_BELOW_W = 5.0


def normalise_grid_import_w(raw_w: float, sign: str) -> float:
    """Positive means importing, regardless of the stored convention."""
    return raw_w if sign == GRID_SIGN_IMPORTING else -raw_w


def derive_battery_w(house_load_w: float, solar_w: float, grid_import_w: float) -> float:
    """Positive means the battery is discharging."""
    return house_load_w - solar_w - grid_import_w


def implied_sign(
    house_load_w: float, solar_w: float, grid_reading_w: float
) -> str | None:
    """Best-guess sign convention from one live snapshot, or None if ambiguous.

    `house_load - solar` is a lower bound on what the house must be drawing
    from somewhere. Where that figure is clearly positive, the grid
    reading's own sign resolves the question: if the reading reads positive
    while the house is a net draw, positive most plausibly means import;
    if the reading reads negative under the same condition, positive means
    export.

    A raw reading of zero, or close to it, is not ambiguous evidence about
    the sign — it is not evidence about the sign at all. Nothing moved, so
    there is no direction to have read wrong. Configuring at night, on
    battery, with a `no_grid_import` constraint already in force, is exactly
    when this comes up, and it should offer no default rather than a
    confidently wrong one.
    """
    if abs(grid_reading_w) < NO_FLOW_BELOW_W:
        return None
    lower_bound = house_load_w - solar_w
    if lower_bound < AMBIGUOUS_BELOW_W:
        return None
    return GRID_SIGN_IMPORTING if grid_reading_w > 0 else GRID_SIGN_EXPORTING


def allowable_draw_kw(
    available_kwh: float,
    hours_until_clear: float,
    max_discharge_kw: float | None,
    other_house_draw_kw: float,
) -> float:
    """The average kW this room may draw without importing.

    The lesser of the energy figure spread evenly over the remaining hours
    and what the battery can actually deliver on top of the rest of the
    house's own measured draw. Never negative: a house already over its own
    discharge ceiling gets a zero allowance, not a negative one.

    `max_discharge_kw` is the battery's rated maximum discharge power, a
    nameplate figure entered at setup (`CONF_BATTERY_MAX_DISCHARGE_KW`) —
    not learned. It is a specification, not something that varies or needs
    tracking over time.
    """
    if hours_until_clear <= 0:
        return 0.0
    energy_rate_kw = max(available_kwh, 0.0) / hours_until_clear
    if max_discharge_kw is None:
        return max(energy_rate_kw, 0.0)
    plant_rate_kw = max(max_discharge_kw - other_house_draw_kw, 0.0)
    return max(min(energy_rate_kw, plant_rate_kw), 0.0)


def ceiling_bin(allowance_kw: float, draw_kw_by_bin: list[float]) -> int | None:
    """The most permissive bin whose learned draw still fits the allowance.

    `draw_kw_by_bin` is ordered at_setpoint, close, working, pulldown —
    increasing draw. The allowance is read backwards from pulldown: the
    largest-approach bin that still fits is the operating point the room
    may run at, because the ceiling should cost as much of the allowance as
    it can afford, not as little. `None` means even holding at setpoint
    would exceed it.
    """
    fitting = [index for index, kw in enumerate(draw_kw_by_bin) if kw <= allowance_kw]
    return max(fitting) if fitting else None


def solar_offset_kw(
    solar_kw: float, other_house_draw_kw: float
) -> tuple[float, float]:
    """Split solar into what the rest of the house consumes and what is left.

    0.8.11, finding 18. Solar is a direct, primary offset against current
    draw, checked first — the battery only becomes a binding constraint at
    all where solar is insufficient to cover the house. Returns
    `(other_house_draw_kw_net, solar_credit_kw)`: the first is what the rest
    of the house still needs from the battery or grid after solar has paid
    down as much of it as it can, which shrinks how much of the discharge
    ceiling and the reserve energy the rest of the house is spoken for; the
    second is whatever solar is left over once the house is covered, which
    this room may draw on directly, free of both the energy and discharge-
    rate limits that bound the battery-sourced portion of its allowance.
    """
    other_house_draw_kw_net = max(other_house_draw_kw - solar_kw, 0.0)
    solar_credit_kw = max(solar_kw - other_house_draw_kw, 0.0)
    return other_house_draw_kw_net, solar_credit_kw
