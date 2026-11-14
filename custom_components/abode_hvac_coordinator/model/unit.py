"""The unit's response, outside the filter (Section 4.3, model/unit.py) [Prop].

The unit's delay and lag are learned inside the heat filter (decision of 27
October 2026: the specification's fit after each start, stop or setpoint
change could not recover them at 0.2 C sensor steps). This file holds the
unit's effort and curve, UnitModel (one room's lowest continuous rate), and
DrawLearner (the house's: each outdoor unit's electrical draw by effort,
learned from steps in house load when exactly one outdoor unit starts or
stops [D028]).
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..const import (
    DRAW_BASELINE_S,
    DRAW_STEP_WINDOW_S,
    DRAW_WITHOUT_LOAD_SENSOR_KW,
    LOAD_STEP_WINDOW_S,
    UNIT_CURVE_EFFORTS_C,
    UNIT_MIN_RATE_HOLD_MIN,
)
from ..models import CompressorSource, CompressorState, HeadSnapshot
from ..observe import LoadHistory
from .physics import Figures, curve_effort, curve_rate

COOLING_MODES = frozenset({"cool", "dry"})
HEATING_MODES = frozenset({"heat"})
RESTING_MODES = frozenset({"off", "fan_only"})


def curve_efforts() -> tuple[float, ...]:
    """The curve's fixed efforts, C of the unit's reading against its
    setpoint. A compressor-frequency sensor answers whether the compressor
    runs (4.8) and is not an effort (decision of 28 October 2026)."""
    return UNIT_CURVE_EFFORTS_C


def head_direction(mode: str | None) -> int:
    """+1 for a cooling mode (cool, dry), -1 for heat, 0 otherwise."""
    if mode in COOLING_MODES:
        return 1
    if mode in HEATING_MODES:
        return -1
    return 0


def seen_running(head: HeadSnapshot) -> bool:
    """Running for what the component decides (the power budget, the plan,
    the cost, the short-cycle guard, an opening): seen running, or in a mode
    that runs the compressor with its state unknown. The model and the draw
    learner use only a known state [D028][D074]."""
    if head.compressor is CompressorState.RUNNING:
        return True
    return head.compressor is CompressorState.UNKNOWN and head_direction(head.mode) != 0


def head_effort(head: HeadSnapshot) -> float | None:
    """EFFORT for one head [D095]: cooling, the unit's reading - setpoint;
    heating, setpoint - the unit's reading. None where it cannot be worked
    out."""
    if head.own_temperature is None or head.setpoint is None:
        return None
    d = head_direction(head.mode)
    if d > 0:
        return head.own_temperature - head.setpoint
    if d < 0:
        return head.setpoint - head.own_temperature
    return 0.0


def rate_for_effort(fig: Figures, effort: float, direction: int) -> float:
    """The steady rate at an effort, C/h, positive cooling."""
    if direction > 0:
        steps = fig.dry_steps if direction == 2 else fig.cool_steps
        return curve_rate(curve_efforts(), steps, effort)
    if direction < 0:
        return -curve_rate(curve_efforts(), fig.heat_steps, effort)
    return 0.0


def effort_for_rate(fig: Figures, rate: float, *, dry: bool = False) -> float:
    """The effort for a rate, C/h, read on the curve the mode runs on.

    dry: the rate is dry's, so it is read on dry's own curve [D109]; without
    it the sign picks cooling or heating [D056]."""
    if dry:
        steps = fig.dry_steps
    else:
        steps = fig.cool_steps if rate >= 0.0 else fig.heat_steps
    return curve_effort(curve_efforts(), steps, abs(rate))


def top_rate(fig: Figures, direction: int) -> float:
    """The curve's top rate in a direction, C/h (positive)."""
    return abs(rate_for_effort(fig, curve_efforts()[-1], direction or 1))


@dataclass(slots=True)
class UnitModel:
    """One room's unit: its lowest continuous rate."""

    u_min: float | None = None  # cooling: dry's own rate is a share of it [D108]
    u_min_heat: float | None = None  # heating: its own curve, its own floor
    _run_rates: deque[float] = field(default_factory=lambda: deque(maxlen=int(UNIT_MIN_RATE_HOLD_MIN)))
    _run_lowest: deque[float] = field(default_factory=lambda: deque(maxlen=int(UNIT_MIN_RATE_HOLD_MIN)))
    _run_heating: bool = False

    def observe_rate(self, running: bool, rate: float, lowest: float) -> None:
        """U_min: the smallest rate held for 30 minutes without a stop, at
        or below the lowest rate offered then [D100].

        rate is the steady rate the unit was asked for this minute, r(x)
        at its effort, so the lag after a start is not counted as a low
        rate held. lowest is lowest_rate at that minute: a hold above it
        says nothing about how low the unit can go, so a first long run at
        full rate never becomes U_min. Both are averaged over the run, so
        the curve moving as it is learned does not decide it."""
        if not running or abs(rate) <= 0.0:
            # a minute with no effort asked (the room at or past the
            # setpoint) is no rate held: it breaks the run, so U_min is
            # never learned as 0 or pulled down by such minutes
            self._run_rates.clear()
            self._run_lowest.clear()
            return
        heating = rate < 0.0
        if self._run_rates and self._run_heating is not heating:
            self._run_rates.clear()  # a change of direction is a new run
            self._run_lowest.clear()
        self._run_heating = heating
        self._run_rates.append(abs(rate))
        self._run_lowest.append(abs(lowest))
        if len(self._run_rates) >= UNIT_MIN_RATE_HOLD_MIN:
            held = statistics.fmean(self._run_rates)
            # a hold at or below what was offered is the unit's lowest rate
            # now, up or down: the curve it is read from moves as it is
            # learned, so a figure from an unsettled curve is not kept;
            # heating and cooling each have their own [D100]
            if held <= statistics.fmean(self._run_lowest):
                if heating:
                    self.u_min_heat = held
                else:
                    self.u_min = held

    def break_run(self) -> None:
        """A minute that is not a run of its own: the run in progress ends
        without deciding U_min. A dry minute uses it, so dry's smaller
        sensible rate is never learned as the cooling floor [D108]."""
        self._run_rates.clear()
        self._run_lowest.clear()

    def lowest_rate(self, fig: Figures, direction: int) -> float:
        """U_min, or the curve's rate at its first effort before it is
        learned [Prop]; never below that rate, since no effort asks for
        less (physics.py curve_rate), so a U_min learned on an earlier,
        lower curve cannot hold the plan below what the unit can be asked
        for [D100]."""
        first = abs(rate_for_effort(fig, curve_efforts()[0], direction or 1))
        learned = self.u_min_heat if direction < 0 else self.u_min
        if learned is not None and learned > 0.0:
            return max(learned, first)
        return first

    def as_dict(self) -> dict[str, Any]:
        """What Memory keeps."""
        return {"u_min": self.u_min, "u_min_heat": self.u_min_heat}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UnitModel:
        """Restore; any number that is not finite, or not above 0, is
        discarded and relearned [D031][D100]. A file from before heating
        had its own figure has none for it."""
        def rate(key: str) -> float | None:
            v = data.get(key)
            if isinstance(v, bool) or not isinstance(v, int | float):
                return None
            return float(v) if math.isfinite(float(v)) and v > 0 else None
        return cls(u_min=rate("u_min"), u_min_heat=rate("u_min_heat"))


# --------------------------------------------------------------------------
# Draw, per outdoor unit, for the house
# --------------------------------------------------------------------------


def _working(head: HeadSnapshot) -> bool:
    """Whether a head is working, for draw learning: its compressor reported
    running or stopped by its frequency or hvac_action, and otherwise a
    mode that runs it. A state the house-load step judged comes a minute or
    more after the change, so the mode decides there too [D028]."""
    if head.mode in RESTING_MODES:
        return False
    if head.compressor_source is not CompressorSource.LOAD_STEP:
        if head.compressor is CompressorState.RUNNING:
            return True
        if head.compressor is CompressorState.STOPPED:
            return False
    return head.mode in COOLING_MODES | HEATING_MODES


@dataclass(slots=True)
class _HeadWatch:
    outdoor_unit: str
    mode: str | None
    compressor: CompressorState
    effort: float | None
    working: bool = False


@dataclass(slots=True)
class _DrawEvent:
    outdoor_unit: str
    when: datetime
    sign: int  # +1 start, -1 stop
    effort: float


@dataclass(slots=True)
class DrawLearner:
    """Each outdoor unit's draw at each effort, from house-load steps [D028].

    A start is a head put in cool, dry or heat from off or fan-only, or a
    compressor reported starting. A stop
    is a head put in off or fan-only from cool, dry or heat, or a compressor
    reported stopping. Neither needs the unit to report its compressor: the
    house load says whether it moved. WHEN exactly one outdoor unit starts
    or stops and no other changes from 3 minutes before to 4 minutes after,
    the step in house load is its draw at that effort, kept as a running
    average at the nearest of the curve's efforts. The load before is its
    average over the 3 minutes before, after this unit's own last change;
    after a start it is the average from the first reading that rose, since
    the compressor starts up to minutes after its head; after a stop, the
    average of what follows. Each is cut at this unit's next change. A step
    under three times the house power's learned noise is its own movement,
    not a draw (decision of 8 November 2026; the whole-degree effort test it replaces refused real
    stops: a unit reading 24 at a 24 setpoint was still drawing 0.23 kW).
    """

    has_load_sensor: bool = True
    table: dict[str, dict[int, tuple[float, int]]] = field(default_factory=dict)
    _watch: dict[str, _HeadWatch] = field(default_factory=dict)
    _events: list[_DrawEvent] = field(default_factory=list)
    _changes: deque[tuple[str, datetime]] = field(default_factory=lambda: deque(maxlen=200))
    _seen_commands: set[tuple[str, datetime]] = field(default_factory=set)

    def observe(
        self,
        now: datetime,
        heads: Sequence[HeadSnapshot],
        commands: Mapping[str, datetime],
        load: LoadHistory,
    ) -> None:
        """Watch every head in the house each minute."""
        before = dict(self._watch)
        # an outdoor unit another head kept running across the minute did
        # not start or stop: a head joining or leaving it says nothing of
        # the unit's draw (one frequency sensor is often shared by every
        # head on it, and a head off or on its fan reads stopped) [D028]
        kept_running = {
            h.outdoor_unit for h in heads
            if _working(h) and (p := before.get(h.climate)) is not None and p.working
        }
        fresh: list[_DrawEvent] = []
        for h in heads:
            effort = head_effort(h)
            prev = self._watch.get(h.climate)
            now_on = _working(h)
            self._watch[h.climate] = _HeadWatch(h.outdoor_unit, h.mode, h.compressor, effort, now_on)
            if prev is None:
                continue
            was = prev.working
            sign = 0
            at_effort: float | None = None
            if now_on and not was and effort is not None:
                # the unit's own reading is often whole degrees, so an effort
                # of 0 can still start the compressor: the load decides
                sign, at_effort = 1, max(effort, 0.0)
            elif was and not now_on and prev.effort is not None:
                sign, at_effort = -1, prev.effort
            if sign != 0 or prev.compressor is not h.compressor or prev.mode != h.mode:
                self._changes.append((h.outdoor_unit, now))
            if sign != 0 and at_effort is not None and h.outdoor_unit not in kept_running:
                fresh.append(_DrawEvent(h.outdoor_unit, now, sign, at_effort))
        # one head stopping as another starts on the same outdoor unit in the
        # same minute: the unit may never have stopped, so neither is learned
        swapped = {e.outdoor_unit for e in fresh if e.sign > 0} & {e.outdoor_unit for e in fresh if e.sign < 0}
        self._events.extend(e for e in fresh if e.outdoor_unit not in swapped)
        for climate, when in commands.items():
            if (climate, when) in self._seen_commands:
                continue
            self._seen_commands.add((climate, when))
            unit = self._watch[climate].outdoor_unit if climate in self._watch else f"own:{climate}"
            self._changes.append((unit, when))
        horizon = now - timedelta(seconds=10 * LOAD_STEP_WINDOW_S)
        self._seen_commands = {c for c in self._seen_commands if c[1] >= horizon}
        self._judge(now, load)

    def _judge(self, now: datetime, load: LoadHistory) -> None:
        window = timedelta(seconds=DRAW_STEP_WINDOW_S)
        base = timedelta(seconds=DRAW_BASELINE_S)
        # a change is seen up to a minute after it was made: the minute
        # before a stop is seen may already be after it
        late = timedelta(seconds=LOAD_STEP_WINDOW_S)
        keep: list[_DrawEvent] = []
        # a step the house's own movement cannot explain; until its noise is
        # learned, events wait (and lapse once their load has gone) [Der]
        min_step = load.step_kw()
        for ev in self._events:
            if now - ev.when < window or min_step is None:
                if now - ev.when < 2 * window:
                    keep.append(ev)
                continue
            others = any(
                unit != ev.outdoor_unit and ev.when - base <= when <= ev.when + window
                for unit, when in self._changes
            )
            if others:
                continue
            own = [when for unit, when in self._changes if unit == ev.outdoor_unit and when != ev.when]
            last_own = max((w for w in own if w < ev.when), default=ev.when - base - late)
            next_own = min((w for w in own if w > ev.when), default=ev.when + window)
            start_from = max(ev.when - base - late, last_own)
            before_samples = load.between(start_from, ev.when - late if ev.sign < 0 else ev.when)
            if before_samples:
                before = statistics.fmean(kw for _, kw in before_samples)
            else:
                at = load.at_or_before(ev.when - late if ev.sign < 0 else ev.when)
                if at is None:
                    continue
                before = at
            after_samples = [kw for _, kw in load.between(ev.when, min(next_own, ev.when + window))]
            if ev.sign > 0:
                # the compressor starts after its head: from the first rise
                first = next((i for i, kw in enumerate(after_samples) if kw - before >= min_step), None)
                after_samples = after_samples[first:] if first is not None else []
            if not after_samples:
                continue
            draw = (statistics.fmean(after_samples) - before) * ev.sign
            if draw >= min_step:
                self._add(ev.outdoor_unit, ev.effort, draw)
        self._events = keep

    def _add(self, unit: str, effort: float, draw: float) -> None:
        efforts = curve_efforts()
        idx = min(range(len(efforts)), key=lambda i: abs(efforts[i] - effort))
        bins = self.table.setdefault(unit, {})
        mean, count = bins.get(idx, (0.0, 0))
        bins[idx] = (mean + (draw - mean) / (count + 1), count + 1)

    def draw_at(self, unit: str, effort: float) -> float | None:
        """The draw at an effort, kW: straight lines between known efforts,
        the nearest beyond them; 1.2 kW without a house-load sensor; None
        while nothing is known [D028]."""
        if not self.has_load_sensor:
            return DRAW_WITHOUT_LOAD_SENSOR_KW
        bins = self.table.get(unit)
        if not bins:
            return None
        efforts = curve_efforts()
        known = sorted((efforts[i], mean) for i, (mean, _) in bins.items())
        if effort <= known[0][0]:
            return known[0][1]
        if effort >= known[-1][0]:
            return known[-1][1]
        for (x0, y0), (x1, y1) in itertools.pairwise(known):
            if x0 <= effort <= x1:
                return y0 + (effort - x0) / (x1 - x0) * (y1 - y0)
        return known[-1][1]

    def as_dict(self) -> dict[str, Any]:
        """What Memory keeps."""
        return {
            "table": {u: {str(i): [m, c] for i, (m, c) in b.items()} for u, b in self.table.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], has_load_sensor: bool) -> DrawLearner:
        """Restore; any number that is not finite is discarded [D031]."""
        table: dict[str, dict[int, tuple[float, int]]] = {}
        for unit, bins in dict(data.get("table", {})).items():
            for i, (m, c) in dict(bins).items():
                if math.isfinite(float(m)) and int(c) > 0:
                    table.setdefault(str(unit), {})[int(i)] = (float(m), int(c))
        return cls(has_load_sensor=has_load_sensor, table=table)
