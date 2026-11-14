"""Module 4, Economics: when to spend, and how much (Section 4.7, economics.py).

Economics supplies the prices and the power limits the plan works with. It
never decides whether the band holds [D008]. It reads nothing itself: the
coordinator hands it the tariff response, the house's readings and what the
model has learned.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .const import (
    ALLOWANCE_PRECISION_KW,
    CEILING_RATE_STEP,
    CEILING_RISE_HOLD_S,
    CEILING_RISE_MARGIN_KW,
    CONSTRAINT_NO_GRID_IMPORT,
    DRAW_WITHOUT_LOAD_SENSOR_KW,
    GRID_EVIDENCE_BATTERY_WINDOW_S,
    GRID_EVIDENCE_NET_LOAD_KW,
    GRID_SIGN_CONTRADICTIONS,
    GRID_ZERO_FLOW_W,
    IMPORT_REPORTED_ABOVE_KWH,
    KNOWN_CONSTRAINTS,
    LOAD_AVERAGE_S,
    PLAN_STEP_H,
    PLAN_STEPS,
)
from .models import (
    Plan,
    PlanAction,
    PowerManagement,
    RoomEconomics,
    RoomPower,
    StepKind,
    TariffInterval,
)

# --------------------------------------------------------------------------
# TARIFF
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TariffRead:
    """One read of Abode Power Tariffs' get_intervals response.

    error is set when the response could not be read; intervals are then
    empty and every half-hour is priced the same [D004][Prop]. unknown names
    every constraint this component does not know, for a repair [D034].
    """

    intervals: tuple[TariffInterval, ...]
    unknown: frozenset[str]
    error: str | None = None


def parse_tariff(response: Mapping[str, Any] | None) -> TariffRead:
    """Read the forward intervals from Abode Power Tariffs' get_intervals
    response: {"intervals": [{"start_time", "end_time", "per_kwh", "rate",
    "constraints", "enforceable_constraints", ...}]} (its intervals.py,
    Interval.as_dict, commit 34d71d5). Prices stay in $/kWh [D004].

    A rule the tariff declares enforceable is held absolutely [D034]; one
    declared as information only is carried as a hint. Every rule, either
    kind, that this component does not know is reported, never dropped.
    """
    if response is None:
        return TariffRead((), frozenset(), "no response")
    raw = response.get("intervals")
    if not isinstance(raw, list):
        return TariffRead((), frozenset(), "the response has no intervals")
    out: list[TariffInterval] = []
    unknown: set[str] = set()
    try:
        for item in raw:
            start = datetime.fromisoformat(str(item["start_time"]))
            end = datetime.fromisoformat(str(item["end_time"]))
            price = float(item["per_kwh"])
            if start.tzinfo is None or end.tzinfo is None or not math.isfinite(price) or end <= start:
                return TariffRead((), frozenset(), "an interval could not be read")
            every = tuple(str(c) for c in item.get("constraints") or ())
            enforced = tuple(str(c) for c in item.get("enforceable_constraints") or ())
            unknown.update(c for c in (*every, *enforced) if c not in KNOWN_CONSTRAINTS)
            out.append(
                TariffInterval(
                    start=start,
                    end=end,
                    rate_name=str(item.get("rate", "")),
                    price=price,
                    constraints=enforced,
                    hints=tuple(c for c in every if c not in enforced),
                )
            )
    except (KeyError, TypeError, ValueError):
        return TariffRead((), frozenset(), "an interval could not be read")
    out.sort(key=lambda i: i.start)
    return TariffRead(tuple(out), frozenset(unknown))


def prices_ahead(
    intervals: Sequence[TariffInterval],
    now: datetime,
    steps: int = PLAN_STEPS,
    step_h: float = PLAN_STEP_H,
) -> tuple[list[float], float]:
    """The price of each half-hour ahead, and p_max [D004][D035].

    Each half-hour gets the price of the tariff interval its start falls
    in; one beyond the last interval gets the last interval's price. With no
    tariff every half-hour gets the same price, 1, and p_max is 1 [Prop].
    """
    if not intervals:
        return [1.0] * steps, 1.0
    p_max = max(i.price for i in intervals)
    prices: list[float] = []
    for j in range(steps):
        t = now + timedelta(hours=j * step_h)
        hit = next((i for i in intervals if i.start <= t < i.end), None)
        if hit is None:
            hit = intervals[-1] if t >= intervals[-1].end else intervals[0]
        prices.append(hit.price)
    return prices, p_max


def no_import_window_end(intervals: Sequence[TariffInterval], now: datetime) -> datetime | None:
    """The end of the no-grid-import window in force now, following
    back-to-back intervals that carry it; None when none is in force
    [D034][D037]."""
    end: datetime | None = None
    for i in intervals:
        if CONSTRAINT_NO_GRID_IMPORT not in i.constraints:
            if end is not None and i.start <= end:
                break
            continue
        if end is None:
            if i.start <= now < i.end:
                end = i.end
        elif i.start <= end:
            end = max(end, i.end)
    return end


def next_tariff_change(intervals: Sequence[TariffInterval], now: datetime) -> datetime | None:
    """When the tariff next changes: the start of the first interval after
    now whose rate, price or constraints differ from those in force; None
    when no interval is in force or none ahead differs (decision of 29
    October 2026) [D005]."""
    current = next((i for i in intervals if i.start <= now < i.end), None)
    if current is None:
        return None
    for i in intervals:
        if i.start <= now:
            continue
        if (i.rate_name, i.price, i.constraints) != (current.rate_name, current.price, current.constraints):
            return i.start
    return None


# --------------------------------------------------------------------------
# POWER BUDGET
# --------------------------------------------------------------------------


@dataclass(slots=True)
class HouseLoadTracker:
    """House load and solar over the last 15 minutes [D067].

    Each sample remembers which outdoor units were running, so the rest of
    the house can be measured while a given unit was not.
    """

    samples: deque[tuple[datetime, float, float | None, frozenset[str]]] = field(default_factory=deque)

    def add(self, now: datetime, load_kw: float | None, solar_kw: float | None, running: frozenset[str]) -> None:
        if load_kw is not None:
            self.samples.append((now, load_kw, solar_kw, running))
        horizon = now - timedelta(seconds=LOAD_AVERAGE_S)
        while self.samples and self.samples[0][0] < horizon:
            self.samples.popleft()

    def rest_of_house(self, units: Collection[str], draw_now: Mapping[str, float | None]) -> float | None:
        """L: the load while none of these units was running [D067]. They
        are the room's own and every other enforced one, whose draws are
        taken from the allowance after, so L must not hold them too [I10].
        Where each minute had one running, the load less the learned draws
        now of those running then, over the minutes where those are all
        learned; None if there are none."""
        quiet = [load for _, load, _, running in self.samples if not running & set(units)]
        if quiet:
            return sum(quiet) / len(quiet)
        rest: list[float] = []
        for _, load, _, running in self.samples:
            draws = [draw_now.get(u) for u in running & set(units)]
            learned = [d for d in draws if d is not None]
            if len(learned) == len(draws):
                rest.append(load - sum(learned))
        return max(sum(rest) / len(rest), 0.0) if rest else None

    def solar(self) -> float | None:
        """Solar averaged over 15 minutes [D067]."""
        values = [s for _, _, s, _ in self.samples if s is not None]
        return sum(values) / len(values) if values else None


@dataclass(frozen=True, slots=True)
class SolarSlice:
    """Forecast solar energy over one slice of the window."""

    start: datetime
    end: datetime
    kwh: float


@dataclass(frozen=True, slots=True)
class HousePower:
    """The house's readings and settings for the budget."""

    battery_charge_pct: float | None
    battery_size_kwh: float | None
    reserve_kwh: float | None
    rated_discharge_kw: float | None
    solar_configured: bool
    load_configured: bool


def available_kwh(house: HousePower) -> float | None:
    """E_avail = SoC/100 x capacity - reserve margin [D061][Set]."""
    if house.battery_charge_pct is None or house.battery_size_kwh is None or house.reserve_kwh is None:
        return None
    return house.battery_charge_pct / 100.0 * house.battery_size_kwh - house.reserve_kwh


def window_slices(slices: Sequence[SolarSlice], now: datetime, end: datetime) -> list[SolarSlice]:
    """The forecast cut to the no-import window, from now to its end
    [D061]. A forecast hour partly inside counts for the part inside, in
    proportion; time the forecast does not cover counts as no solar. The
    forecast holds every hour it has, past ones and tomorrow's
    (forecast_solar/energy.py) [HA]."""
    out: list[SolarSlice] = []
    t = now
    for s in sorted(slices, key=lambda x: x.start):
        a, b = max(s.start, t), min(s.end, end)
        if b <= a:
            continue
        if a > t:
            out.append(SolarSlice(t, a, 0.0))
        out.append(SolarSlice(a, b, s.kwh * (b - a).total_seconds() / (s.end - s.start).total_seconds()))
        t = b
    if t < end:
        out.append(SolarSlice(t, end, 0.0))
    return out


def allowance_with_forecast(
    *,
    e_avail: float,
    headroom: float,
    rest: float,
    slices: Sequence[SolarSlice],
    plant_kw: float | None,
) -> float:
    """P*: the highest steady draw for which the battery, walked slice by
    slice, never falls below 0 [D061][Der].

    E_{j+1} = min(E0 + headroom, E_j + PV_j - (L + P) dt_j). More draw never
    leaves the battery fuller, so the allowable draws run from 0, and
    halving finds the top to 0.005 kW.
    """
    e0 = max(e_avail, 0.0)
    cap = e0 + headroom
    total_h = sum((s.end - s.start).total_seconds() / 3600.0 for s in slices)
    if total_h <= 0.0:
        return plant_kw if plant_kw is not None else 0.0

    def fits(p: float) -> bool:
        e = e0
        for s in slices:
            dt = (s.end - s.start).total_seconds() / 3600.0
            e = min(cap, e + s.kwh - (rest + p) * dt)
            if e < 0.0:
                return False
        return True

    # no draw above this can leave the battery at or above zero at the end
    hi = max((e0 + sum(s.kwh for s in slices)) / total_h - rest, 0.0)
    if plant_kw is not None:
        hi = min(hi, plant_kw)
    if not fits(0.0):
        return 0.0
    if fits(hi):
        return hi
    lo = 0.0
    while hi - lo > ALLOWANCE_PRECISION_KW:
        mid = (lo + hi) / 2.0
        if fits(mid):
            lo = mid
        else:
            hi = mid
    return lo


def allowance_without_forecast(
    *,
    e_avail: float,
    rest: float,
    solar_now: float,
    worst_clear_sky: float,
    hours_left: float,
    rated_kw: float | None,
) -> float:
    """P* without a solar forecast [D039][D067][D042].

    Solar pays down the rest of the house first; the battery binds only on
    the shortfall.
    """
    sustained = solar_now * worst_clear_sky
    l_net = max(rest - sustained, 0.0)
    credit = max(sustained - rest, 0.0)
    battery = max(e_avail, 0.0) / hours_left - l_net if hours_left > 0.0 else math.inf
    if rated_kw is not None:
        battery = min(battery, rated_kw - l_net)
    return credit + max(0.0, battery)


def highest_rate(draw_for_rate: Callable[[float], float | None], allowance: float, top: float) -> float | None:
    """The highest rate whose learned draw fits within an allowance, found
    to 0.05 C/h; None when even the smallest rate does not fit [D037]."""
    best: float | None = None
    steps = max(int(top / CEILING_RATE_STEP), 1)
    for i in range(1, steps + 1):
        rate = min(i * CEILING_RATE_STEP, top)
        draw = draw_for_rate(rate)
        if draw is not None and draw <= allowance:
            best = rate
    return best


@dataclass(slots=True)
class _Ceiling:
    rate: float | None = None
    since: datetime | None = None


@dataclass(slots=True)
class PowerBudget:
    """The house's one power allowance, shared among its outdoor units [I10]."""

    loads: HouseLoadTracker = field(default_factory=HouseLoadTracker)
    _ceilings: dict[str, _Ceiling] = field(default_factory=dict)
    last_allowance: float | None = None
    last_reason: str = ""

    def evaluate(
        self,
        *,
        now: datetime,
        window_end: datetime | None,
        house: HousePower,
        rooms: Sequence[RoomPower],
        draw_for_rate: Mapping[str, Callable[[float], float | None]],
        running_draw: Mapping[tuple[str, str], float | None],
        solar_slices: Sequence[SolarSlice] | None,
        worst_clear_sky: float,
    ) -> dict[str, tuple[float | None, bool, float | None, str]]:
        """Every cycle, once for the whole house: each room's ceiling, a
        turn-off request (enforced only), its allowance, and the reason.

        draw_for_rate maps each room to its outdoor unit's learned draw at
        a rate of that room; running_draw maps (outdoor unit, room) to that
        room's learned draw on that unit now, for every room running. An
        outdoor unit shared by rooms is one draw on the battery: a room's
        allowance is what is left after the other enforced outdoor units
        and the other rooms on its own unit, whatever their setting. A
        ceiling is given whatever the room's setting; RoomEconomics applies
        it only where ENFORCED [D041][D097].
        """
        out: dict[str, tuple[float | None, bool, float | None, str]] = {}
        managed = [r for r in rooms if r.management is not PowerManagement.OFF]
        if window_end is None or now >= window_end:
            self._reset()
            self.last_allowance, self.last_reason = None, "no no-grid-import window in force: no limit"
            return {r.room_id: (None, False, None, self.last_reason) for r in rooms}
        missing = [
            name
            for name, ok in (
                ("battery charge", house.battery_charge_pct is not None),
                ("battery size", house.battery_size_kwh is not None),
                ("reserve margin", house.reserve_kwh is not None),
                ("solar", house.solar_configured and self.loads.solar() is not None),
                ("house load", house.load_configured and bool(self.loads.samples)),
            )
            if not ok
        ]
        if missing:
            # no limit while a figure is missing [D044], but the ceilings'
            # memory stands: when the figures return, a ceiling still rises
            # only after it has stood 10 minutes [D062]
            self.last_allowance = None
            self.last_reason = "no limit: " + ", ".join(missing) + " not available"  # [D044]
            return {r.room_id: (None, False, None, self.last_reason) for r in rooms}
        e_avail = available_kwh(house)
        solar_now = self.loads.solar()
        assert e_avail is not None and solar_now is not None and house.battery_size_kwh is not None
        assert house.battery_charge_pct is not None
        headroom = house.battery_size_kwh * (1.0 - house.battery_charge_pct / 100.0)
        hours_left = (window_end - now).total_seconds() / 3600.0
        in_window = window_slices(solar_slices, now, window_end) if solar_slices else []
        enforced_units = {r.outdoor_unit for r in managed if r.management is PowerManagement.ENFORCED}
        unit_draw: dict[str, float] = {}
        unit_known: dict[str, bool] = {}
        for (u, _rid), d in running_draw.items():
            unit_draw[u] = unit_draw.get(u, 0.0) + (d or 0.0)
            unit_known[u] = unit_known.get(u, True) and d is not None
        for room in rooms:
            if room.management is PowerManagement.OFF:
                out[room.room_id] = (None, False, None, "power management off")
                continue
            if room.precool_on:
                out[room.room_id] = (None, False, None, "precool is not rationed")  # [D033]
                continue
            known_draw = {u: (d if unit_known.get(u) else None) for u, d in unit_draw.items()}
            rest = self.loads.rest_of_house(enforced_units | {room.outdoor_unit}, known_draw)
            if rest is None:
                # the reserve still holds while the rest of the house is
                # unmeasured: an Enforced room running is turned off there
                # [D063][D067] (Jason, 6 November 2026)
                if e_avail <= 0.0:
                    turn_off = room.management is PowerManagement.ENFORCED and room.compressor_running
                    out[room.room_id] = (0.0, turn_off, None, "the battery is down to its reserve: off")
                    continue
                out[room.room_id] = (None, False, None, "no limit: the rest of the house is not yet measured")
                continue
            plant = (
                max(house.rated_discharge_kw + solar_now - rest, 0.0)
                if house.rated_discharge_kw is not None
                else None
            )
            if solar_slices:
                p_star = allowance_with_forecast(
                    e_avail=e_avail, headroom=headroom, rest=rest,
                    slices=in_window, plant_kw=plant,
                )
            else:
                p_star = allowance_without_forecast(
                    e_avail=e_avail,
                    rest=rest,
                    solar_now=solar_now,
                    worst_clear_sky=worst_clear_sky,
                    hours_left=hours_left,
                    rated_kw=house.rated_discharge_kw,
                )
            others = sum(d for u, d in unit_draw.items() if u != room.outdoor_unit and u in enforced_units)
            sharing = sum(
                d or 0.0 for (u, rid), d in running_draw.items() if u == room.outdoor_unit and rid != room.room_id
            )
            allowance = p_star - others - sharing  # [I10][Der][D097]
            self.last_allowance = p_star
            out[room.room_id] = self._ceiling(room, allowance, draw_for_rate.get(room.room_id), now, e_avail, plant)
        self.last_reason = "a no-grid-import window is in force"
        return out

    def _ceiling(
        self,
        room: RoomPower,
        allowance: float,
        draw: Callable[[float], float | None] | None,
        now: datetime,
        e_avail: float,
        plant: float | None,
    ) -> tuple[float | None, bool, float | None, str]:
        """The ceiling drops at once; it rises only after it has stood 10
        minutes, and only to a rate whose draw the allowance clears by
        0.3 kW [D062].

        A room whose lowest rate cannot last to the end of the window keeps
        cooling at its lowest rate until it cannot any more: until the
        battery is down to its reserve, or cannot supply the lowest rate's
        draw. Only then, and only while running, is it asked to turn off
        [D063][D067] (decision of 29 October 2026)."""
        state = self._ceilings.setdefault(room.room_id, _Ceiling())
        if draw is None or draw(room.lowest_rate) is None:
            # Jason, 6 November 2026: with no draw learned the unit keeps
            # going until the battery reaches its reserve or the window ends
            # at the tariff change; running is the only way its draw is
            # learned
            if e_avail <= 0.0:
                turn_off = room.management is PowerManagement.ENFORCED and room.compressor_running
                return 0.0, turn_off, allowance, "the battery is down to its reserve: off"
            return None, False, allowance, (
                "no limit: the outdoor unit's draw is not yet learned; it runs until the battery "
                "reaches its reserve or the window ends")
        candidate = highest_rate(draw, allowance, room.top_rate)
        rise_to = highest_rate(draw, allowance - CEILING_RISE_MARGIN_KW, room.top_rate)
        current = state.rate
        if (current is None and state.since is None) or candidate is None or (
            current is not None and candidate < current
        ):
            # the first figure, or a drop: at once [D062]
            state.rate, state.since = candidate, now
        elif current is None or candidate > current:
            held = state.since is not None and (now - state.since).total_seconds() >= CEILING_RISE_HOLD_S
            if held and rise_to is not None and (current is None or rise_to > current):
                state.rate, state.since = min(rise_to, candidate), now
        rate = state.rate
        if rate is not None and rate >= room.top_rate:
            return None, False, allowance, "no limit"  # [D096]
        if rate is not None and rate >= room.lowest_rate:
            return rate, False, allowance, f"ceiling {rate:.2f} C/h within {allowance:.2f} kW"
        low = draw(room.lowest_rate)
        if e_avail <= 0.0:
            why = "the battery is down to its reserve"
        elif plant is not None and low is not None and low > plant:
            why = "the battery cannot supply the lowest rate's draw"
        else:
            return room.lowest_rate, False, allowance, (
                f"even the lowest rate cannot last to the end of the window within {allowance:.2f} kW: "
                "cooling at the lowest rate until the battery reaches its reserve"
            )
        turn_off = room.management is PowerManagement.ENFORCED and room.compressor_running
        return 0.0, turn_off, allowance, f"{why}: off"

    def _reset(self) -> None:
        self._ceilings.clear()


def room_economics(
    management: PowerManagement,
    prices: Sequence[float],
    p_max: float,
    budget: tuple[float | None, bool, float | None, str],
) -> RoomEconomics:
    """GIVE BACK, per room: prices, p_max, the ceiling (enforced) or the
    ceiling shown only (guidance), a turn-off request, and the reason [D041]."""
    ceiling, turn_off, allowance, reason = budget
    enforced = management is PowerManagement.ENFORCED
    return RoomEconomics(
        prices=tuple(prices),
        p_max=p_max,
        ceiling=ceiling if enforced else None,
        guidance_ceiling=ceiling if management is PowerManagement.GUIDANCE else None,
        turn_off=turn_off and enforced,
        allowance_kw=allowance,
        reason=reason,
    )


# --------------------------------------------------------------------------
# GRID CHECKS
# --------------------------------------------------------------------------


def battery_power(load_kw: float, solar_kw: float, grid_import_kw: float) -> float:
    """Battery power = house load - solar - grid, for display [Der]."""
    return load_kw - solar_kw - grid_import_kw


def grid_evidence(load_kw: float, solar_kw: float, charge_fell: bool, grid_reading_w: float) -> bool | None:
    """At a moment when house load - solar is above 0.2 kW and the battery
    charge has not fallen over 5 minutes, the house must be importing, so
    the reading's sign shows which way positive points: True for "positive
    is import". None when the moment is no evidence, including a reading
    within 5 W of zero [D038][Der][Set]."""
    if load_kw - solar_kw <= GRID_EVIDENCE_NET_LOAD_KW or charge_fell:
        return None
    if abs(grid_reading_w) <= GRID_ZERO_FLOW_W:
        return None
    return grid_reading_w > 0.0


@dataclass(slots=True)
class GridChecks:
    """The grid sign check and import accounting [D038][D043]."""

    positive_is_import: bool
    contradictions: int = 0
    agreements: int = 0
    sign_repair: bool = False
    import_kwh: float = 0.0
    import_repair_kwh: float | None = None
    _charges: deque[tuple[datetime, float]] = field(default_factory=deque)
    _last: datetime | None = None
    _in_window: bool = False

    def as_dict(self) -> dict[str, Any]:
        """What is kept across a restart: the direction it was judged
        against, the counts and the standing repairs."""
        return {"positive_is_import": self.positive_is_import, "contradictions": self.contradictions,
                "agreements": self.agreements, "sign_repair": self.sign_repair,
                "import_repair_kwh": self.import_repair_kwh}

    @classmethod
    def restore(cls, positive_is_import: bool, data: Mapping[str, Any]) -> GridChecks:
        """Fresh checks, with what was kept if it was judged against the
        same direction: a direction changed on Reconfigure starts afresh."""
        out = cls(positive_is_import)
        if data.get("positive_is_import") is not positive_is_import:
            return out

        def count(key: str) -> int:
            v = data.get(key)
            return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0

        out.contradictions, out.agreements = count("contradictions"), count("agreements")
        out.sign_repair = data.get("sign_repair") is True
        kwh = data.get("import_repair_kwh")
        if isinstance(kwh, int | float) and not isinstance(kwh, bool) and math.isfinite(float(kwh)) and kwh > 0:
            out.import_repair_kwh = float(kwh)
        return out

    def import_kw(self, grid_kw: float) -> float:
        """The grid reading as import (+) or export (-), by the sign set up."""
        return grid_kw if self.positive_is_import else -grid_kw

    def observe(
        self,
        now: datetime,
        *,
        load_kw: float | None,
        solar_kw: float | None,
        grid_kw: float | None,
        charge_pct: float | None,
        in_no_import_window: bool,
    ) -> None:
        """Each cycle: the sign check, and the window's import."""
        if charge_pct is not None:
            self._charges.append((now, charge_pct))
        horizon = now - timedelta(seconds=GRID_EVIDENCE_BATTERY_WINDOW_S)
        while len(self._charges) > 1 and self._charges[1][0] <= horizon:
            self._charges.popleft()
        if grid_kw is not None and load_kw is not None and solar_kw is not None:
            fell = bool(self._charges) and charge_pct is not None and charge_pct < self._charges[0][1]
            seen = grid_evidence(load_kw, solar_kw, fell, grid_kw * 1000.0)
            if abs(grid_kw * 1000.0) <= GRID_ZERO_FLOW_W:
                self.contradictions = 0  # no evidence, and resets the count [D038]
            elif seen is not None:
                if seen != self.positive_is_import:
                    self.contradictions += 1
                    self.agreements = 0
                    if self.contradictions >= GRID_SIGN_CONTRADICTIONS:
                        self.sign_repair = True
                else:
                    # as many readings in a row agreeing clear the repair: the
                    # direction kept on Reconfigure was right [D038]
                    self.contradictions = 0
                    self.agreements += 1
                    if self.agreements >= GRID_SIGN_CONTRADICTIONS:
                        self.sign_repair = False
        # import over each no-import window [D043]
        if in_no_import_window:
            if not self._in_window:
                self.import_kwh = 0.0
            if self._last is not None and self._in_window and grid_kw is not None:
                hours = (now - self._last).total_seconds() / 3600.0
                self.import_kwh += max(self.import_kw(grid_kw), 0.0) * hours
        elif self._in_window:
            if self.import_kwh > IMPORT_REPORTED_ABOVE_KWH:
                self.import_repair_kwh = self.import_kwh
            else:
                self.import_repair_kwh = None  # cleared by the next clean window
        self._in_window = in_no_import_window
        self._last = now


# --------------------------------------------------------------------------
# DEMAND FORECAST
# --------------------------------------------------------------------------


def demand_forecast(
    room_energy: Sequence[Sequence[tuple[datetime, datetime, float]]],
    intervals: Sequence[TariffInterval],
) -> tuple[float, list[tuple[TariffInterval, float]]]:
    """The energy expected in each tariff interval given, from each room's
    plan [D005][Prop]. The coordinator gives the intervals up to the next
    tariff change (decision of 29 October 2026).

    room_energy holds, per room, (start, end, kWh) for each step of its
    plan. Each step's energy is shared among the intervals it overlaps in
    proportion to time.
    """
    totals = [0.0] * len(intervals)
    for steps in room_energy:
        for start, end, kwh in steps:
            span = (end - start).total_seconds()
            if span <= 0.0:
                continue
            for k, iv in enumerate(intervals):
                overlap = (min(end, iv.end) - max(start, iv.start)).total_seconds()
                if overlap > 0.0:
                    totals[k] += kwh * overlap / span
    per = [(iv, kwh) for iv, kwh in zip(intervals, totals, strict=True)]
    return sum(totals), per


@dataclass(frozen=True, slots=True)
class DemandForecast:
    """The demand forecast [D005]: kWh to `until`, per tariff interval on
    the way with its price, and what it is priced at. to_change is True when
    `until` is the next tariff change, False when the plans end first."""

    kwh: float
    per_interval: tuple[tuple[TariffInterval, float], ...]
    cost: float | None
    until: datetime
    to_change: bool


_RUNS = frozenset({StepKind.COOL, StepKind.HEAT, StepKind.DRY})


def forecast_to_change(
    plans: Sequence[tuple[Plan, Callable[[PlanAction], float | None]]],
    intervals: Sequence[TariffInterval],
    now: datetime,
) -> DemandForecast | None:
    """The energy each room's plan in force will use from now until the
    next tariff change, or the plans' end where that comes first (decision
    of 29 October 2026) [D005][Prop]. plans pairs each plan with its room's
    learned draw at an action; a draw not yet learned is priced as the plan
    prices it [D028]. None without a plan."""
    if not plans:
        return None
    step = timedelta(hours=PLAN_STEP_H)
    plans_end = max(p.made_at + len(p.actions) * step for p, _ in plans)
    change = next_tariff_change(intervals, now)
    until = min(change, plans_end) if change is not None else plans_end
    room_energy: list[list[tuple[datetime, datetime, float]]] = []
    for plan, draw in plans:
        steps: list[tuple[datetime, datetime, float]] = []
        for j, action in enumerate(plan.actions):
            if action.kind not in _RUNS:
                continue
            start = max(plan.made_at + j * step, now)  # the part already past is not demand
            end = min(plan.made_at + (j + 1) * step, until)
            if end <= start:
                continue
            kw = draw(action)  # the action's kind picks the curve its rate is read on [D028][D109]
            if kw is None:
                kw = DRAW_WITHOUT_LOAD_SENSOR_KW
            steps.append((start, end, kw * (end - start).total_seconds() / 3600.0))
        room_energy.append(steps)
    to_change = change is not None and change <= plans_end
    ahead = [i for i in intervals if i.end > now and i.start < until]
    if not ahead:
        total = sum(kwh for steps in room_energy for _, _, kwh in steps)
        return DemandForecast(total, (), None, until, to_change)
    total, per = demand_forecast(room_energy, ahead)
    cost = sum(kwh * iv.price for iv, kwh in per)
    return DemandForecast(total, tuple(per), cost, until, to_change)
