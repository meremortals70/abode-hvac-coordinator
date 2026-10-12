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

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, tzinfo
from typing import Any

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


def battery_available_kwh(
    soc_percent: float, capacity_kwh: float, reserve_margin_kwh: float
) -> float:
    """Energy the battery may give up before it reaches the reserve."""
    return (soc_percent / 100.0) * capacity_kwh - reserve_margin_kwh


def budget_allowance_kw(
    *,
    available_kwh: float,
    hours_until_clear: float,
    max_discharge_kw: float | None,
    house_load_w: float | None,
    own_draw_kw: float,
    sustained_solar_kw: float,
) -> float:
    """The average kW one room may draw without importing from the grid.

    DR-002, DR-037, DR-039. Moved here from the coordinator in 0.8.14 with the
    arithmetic unchanged. The rest of the house is the measured load less
    this room's own draw, never negative. Solar pays that down first; what
    solar leaves over goes straight to the room, outside the battery's energy
    and discharge limits. The battery then covers what remains, as the lesser
    of its energy spread over the hours left and its discharge rate less what
    the rest of the house still needs.
    """
    other_house_draw_kw = 0.0
    if house_load_w is not None:
        other_house_draw_kw = max(house_load_w / 1000.0 - own_draw_kw, 0.0)
    other_house_draw_kw, solar_credit_kw = solar_offset_kw(
        sustained_solar_kw, other_house_draw_kw
    )
    return solar_credit_kw + allowable_draw_kw(
        available_kwh,
        hours_until_clear,
        max_discharge_kw,
        other_house_draw_kw,
    )


def setpoint_direction(
    target_c: float | None, room_c: float | None
) -> str | None:
    """Which way a unit is being asked to work: "cool", "heat" or None.

    From the solved target against the room's reading, not from the room's
    demand. The ceiling's main case is a room already inside its band, where
    there is no demand and the target still says which way to work. Within
    0.01 C of the target there is no direction.
    """
    if target_c is None or room_c is None:
        return None
    gap = target_c - room_c
    if gap < -0.01:
        return "cool"
    if gap > 0.01:
        return "heat"
    return None


def held_setpoint(
    commanded_c: float, room_c: float, direction: str, ceiling_c: float
) -> float:
    """The commanded setpoint after the power ceiling has had its say.

    The ceiling is how far from the room's own reading the setpoint may be
    asked to go. Cooling may not be set colder than `room - ceiling`; heating
    may not be set warmer than `room + ceiling`. A ceiling of 0.0 holds the
    setpoint at the room's own reading, which the unit reaches at once.
    """
    if direction == "cool":
        return max(commanded_c, round(room_c - ceiling_c, 1))
    return min(commanded_c, round(room_c + ceiling_c, 1))


def ceiling_is_binding(
    uncapped_c: float, room_c: float, direction: str, ceiling_c: float
) -> bool:
    """Whether the ceiling would move this setpoint."""
    return held_setpoint(uncapped_c, room_c, direction, ceiling_c) != uncapped_c


# ---- forecast solar (0.9.3, DR-061) ----------------------------------------

#: The longest period one forecast point is taken to cover, where the next
#: point is further away than this or there is no next point. Every forecast
#: integration this reads publishes hourly Wh.
FORECAST_POINT_MAX = timedelta(hours=1)

#: How finely the allowance is searched for, in kW. Well below any bin's draw.
_ALLOWANCE_TOLERANCE_KW = 0.005


def solar_points_from_forecast(
    forecast: Mapping[str, Any] | None, default_tz: tzinfo
) -> list[tuple[datetime, float]]:
    """Points from one source's energy-platform answer, as (start, Wh).

    The answer is `{"wh_hours": {iso timestamp: Wh}}`, the shape Home
    Assistant's Energy dashboard reads (`energy/types.py`, SolarForecastType).
    A timestamp without an offset is taken as local time. Anything that does
    not parse is skipped, not guessed at; an answer with nothing usable is an
    empty list.
    """
    if not isinstance(forecast, Mapping):
        return []
    raw = forecast.get("wh_hours")
    if not isinstance(raw, Mapping):
        return []
    points: list[tuple[datetime, float]] = []
    for stamp, value in raw.items():
        try:
            start = datetime.fromisoformat(str(stamp))
            wh = float(value)
        except (TypeError, ValueError):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=default_tz)
        if math.isfinite(wh):
            points.append((start, wh))
    return sorted(points)


def energy_dashboard_solar_forecasts(data: Mapping[str, Any] | None) -> list[str]:
    """The solar forecast config entries the Energy dashboard is set up with.

    From the energy manager's data: every solar source's
    `config_entry_solar_forecast` list, in order, without repeats.
    """
    if not isinstance(data, Mapping):
        return []
    found: list[str] = []
    for source in data.get("energy_sources") or []:
        if not isinstance(source, Mapping) or source.get("type") != "solar":
            continue
        for entry_id in source.get("config_entry_solar_forecast") or []:
            if isinstance(entry_id, str) and entry_id not in found:
                found.append(entry_id)
    return found


def forecast_slices(
    points: Sequence[tuple[datetime, float]],
    now: datetime,
    until: datetime,
) -> list[tuple[float, float]]:
    """Forecast solar energy from now until `until`, as (hours, kWh) slices.

    `points` are (period start, Wh produced in that period), as the Energy
    dashboard's solar forecast publishes them, summed across sources by the
    caller. A period runs to the next point's start, never longer than
    `FORECAST_POINT_MAX`. Energy is taken as spread evenly through its period,
    so the slice now is the part of the current period still to come.

    Time from now to `until` that no point covers is a slice with no energy:
    a forecast that does not reach is not a forecast of sun.
    """
    if until <= now:
        return []
    ordered = sorted(points, key=lambda point: point[0])
    periods: list[tuple[datetime, datetime, float]] = []
    for index, (start, wh) in enumerate(ordered):
        end = start + FORECAST_POINT_MAX
        if index + 1 < len(ordered):
            end = min(end, ordered[index + 1][0])
        if end > start:
            periods.append((start, end, max(wh, 0.0)))

    slices: list[tuple[float, float]] = []
    cursor = now
    for start, end, wh in periods:
        if end <= cursor or start >= until:
            continue
        if start > cursor:
            gap = (min(start, until) - cursor).total_seconds() / 3600.0
            slices.append((gap, 0.0))
            cursor = start
        overlap_end = min(end, until)
        share = (overlap_end - cursor) / (end - start)
        hours = (overlap_end - cursor).total_seconds() / 3600.0
        if hours > 0:
            slices.append((hours, wh * share / 1000.0))
        cursor = overlap_end
        if cursor >= until:
            break
    if cursor < until:
        slices.append(((until - cursor).total_seconds() / 3600.0, 0.0))
    return slices


def forecast_allowance_kw(
    *,
    available_kwh: float,
    headroom_kwh: float,
    slices: Sequence[tuple[float, float]],
    other_house_kw: float,
    plant_kw: float | None,
) -> float:
    """The steady kW one room may draw until the window clears, without import.

    0.9.3, DR-061. Walks the window slice by slice with the battery's energy:
    each slice adds the forecast solar and takes away the rest of the house
    and the room. The battery may not fall below the reserve at any point —
    that is the dawn case, where the battery has to carry the room until the
    sun arrives — and cannot hold more than `headroom_kwh` above where it is
    now, so solar the battery cannot store is not credited twice. The answer
    is the largest steady draw that keeps the battery at or above the
    reserve throughout, held to `plant_kw`, what the battery and the sun can
    deliver right now. Found by bisection: the walk is not linear in the
    draw once the battery fills.

    `available_kwh` is the energy above the reserve now; negative is treated
    as none. `plant_kw` None means no discharge limit is configured.
    """
    if not slices:
        return 0.0
    start = max(available_kwh, 0.0)
    ceiling_kwh = start + max(headroom_kwh, 0.0)

    def holds(draw_kw: float) -> bool:
        level = start
        for hours, solar_kwh in slices:
            level = min(level + solar_kwh - (other_house_kw + draw_kw) * hours, ceiling_kwh)
            if level < -1e-9:
                return False
        return True

    upper = plant_kw if plant_kw is not None else math.inf
    if upper <= 0 or not holds(0.0):
        return 0.0
    if math.isinf(upper):
        hours_total = sum(hours for hours, _ in slices)
        upper = (ceiling_kwh + sum(kwh for _, kwh in slices)) / max(hours_total, 1e-6)
    if holds(upper):
        return upper
    low, high = 0.0, upper
    while high - low > _ALLOWANCE_TOLERANCE_KW:
        middle = (low + high) / 2.0
        if holds(middle):
            low = middle
        else:
            high = middle
    return low


def plant_allowance_kw(
    max_discharge_kw: float | None, solar_now_kw: float, other_house_kw: float
) -> float | None:
    """What the battery and the sun can deliver to one room right now.

    The battery's rated discharge plus whatever solar is producing, less the
    rest of the house. None where no discharge limit is configured.
    """
    if max_discharge_kw is None:
        return None
    return max(max_discharge_kw + solar_now_kw - other_house_kw, 0.0)


# ---- hysteresis on the ceiling (0.9.3, DR-062) ------------------------------

#: How far the allowance must clear a more permissive bin's draw, in kW,
#: before the ceiling moves up to it. A judgement, not a measurement.
BIN_RAISE_MARGIN_KW = 0.3

#: How long the ceiling stays where it is before it may move up again. Moving
#: down is never delayed: that protects the budget. A judgement, matching the
#: compressor's minimum run.
BIN_RAISE_HOLD = timedelta(minutes=10)

#: `ceiling_bin` returns None when even the cheapest bin does not fit. Held
#: here as -1 so bins can be compared.
_NO_BIN = -1


def held_ceiling_bin(
    allowance_kw: float,
    draw_kw_by_bin: list[float],
    *,
    previous: int | None,
    previous_since: datetime | None,
    now: datetime,
) -> int | None:
    """The ceiling bin with hysteresis: down at once, up only when it is clear.

    0.9.3, DR-062. `ceiling_bin` alone flipped the setpoint between the room
    and the bottom of the unit's range about twenty times in forty minutes as
    the allowance crossed a bin's draw. A move to a less permissive bin, or
    to no bin, happens the cycle it is needed. A move to a more permissive
    bin needs the allowance to clear that bin's draw by `BIN_RAISE_MARGIN_KW`
    and the ceiling to have stayed put for `BIN_RAISE_HOLD`; otherwise the
    previous bin stands.

    `previous` None with `previous_since` None means no earlier ceiling, and
    the plain answer is taken.
    """
    candidate = ceiling_bin(allowance_kw, draw_kw_by_bin)
    if previous_since is None:
        return candidate
    held = _NO_BIN if previous is None else previous
    wanted = _NO_BIN if candidate is None else candidate
    if wanted <= held:
        return candidate
    if now - previous_since < BIN_RAISE_HOLD:
        return previous
    clear = [
        index
        for index, kw in enumerate(draw_kw_by_bin)
        if index > held and kw + BIN_RAISE_MARGIN_KW <= allowance_kw
    ]
    if not clear:
        return previous
    return max(clear)
