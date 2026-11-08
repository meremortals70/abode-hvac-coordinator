"""Module 9, Evidence: the trace, the scorecard, warnings and repairs.

Evidence says why each room is doing what it does [D006], and shows as
numbers whether the room is being learned and kept comfortable [Prop]. It
builds everything without Home Assistant: the coordinator puts the trace on
each room's mode sensor, speaks the warnings and raises the repairs.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .const import NEAR_CENTRE, PLAN_POINTS_PER_STEP, SILENT_FILTER_REPAIR_S
from .models import (
    BandRule,
    ComfortResult,
    ControlResult,
    DayScore,
    DeliveryResult,
    DueWarning,
    Issue,
    LimitsResult,
    ModelAnswers,
    RoomChoices,
    RoomConfig,
    RoomEconomics,
    RoomModeState,
    Snapshot,
    Speech,
    StepKind,
)

_LOGGER = logging.getLogger(__package__)
_EPS = 1e-9  # so a value exactly 0.3 from the centre counts as within it

_RULE_STATE = {
    BandRule.LEFT: RoomModeState.LEFT,
    BandRule.UNOCCUPIED: RoomModeState.UNOCCUPIED,
    BandRule.PRECOOL: RoomModeState.PRECOOL,
    BandRule.SLEEP: RoomModeState.SLEEP,
    BandRule.OCCUPIED: RoomModeState.OCCUPIED,
}


def mode_state(limits: LimitsResult | None, comfort: ComfortResult) -> RoomModeState:
    """The Mode sensor's state: the band rule in force, with lockout and
    Automatic control off first (Section 7)."""
    if limits is not None and limits.lockout:
        return RoomModeState.LOCKOUT
    if limits is not None and limits.automatic_off:
        return RoomModeState.AUTOMATIC_OFF
    return _RULE_STATE[comfort.rule]


def _r(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def trace(
    *,
    snapshot: Snapshot,
    answers: ModelAnswers,
    comfort: ComfortResult,
    economics: RoomEconomics | None,
    control: ControlResult | None,
    other_proposal: float | None,
    delivery: DeliveryResult | None,
    limits: LimitsResult | None,
    choices: RoomChoices,
) -> dict[str, Any]:
    """TRACE, every cycle, per room: the mode sensor's attributes [D006].

    Plain values only, rounded, so the trace stays small.
    """
    band = None
    if comfort.band_low is not None and comfort.band_high is not None:
        lo, hi = comfort.band_low, comfort.band_high
        index = comfort.room_index
        where = None if index is None else ("below" if index < lo else "above" if index > hi else "in")
        band = {"low": lo, "high": hi, "centre": round((lo + hi) / 2.0, 2), "centre_c": _r(comfort.centre_c),
                "position": where}
    out: dict[str, Any] = {
        "action": (limits.kind if limits is not None else StepKind.NONE).value,
        "band_rule": comfort.rule.value,
        "band_reason": comfort.reason,
        "band": band,
        "comfort_index": _r(comfort.room_index),
        "occupied": comfort.occupied,
        "sleeping": comfort.sleeping,
        "waking": comfort.waking,
        "precool": comfort.precool_on,
        "automatic_control": choices.automatic_control,
    }
    if control is not None:
        out["in_charge"] = control.in_charge.value
        out["other_proposal"] = _r(other_proposal)
        out["control_reason"] = control.reason
        if control.plan is not None:
            p = control.plan
            out["plan"] = {
                "made_at": p.made_at.isoformat(),
                "actions": [[a.kind.value, round(a.rate, 2), a.blinds_closed] for a in p.actions],
                "index": [round(pt.hci, 2) for pt in p.points],
                "minutes_outside": round(p.minutes_outside, 1),
                "comfort_cost": round(p.comfort_cost, 4),
                "energy_cost": round(p.energy_cost, 4),
                "start_cost": round(p.start_cost, 4),
                "starts": p.starts,
                "w": round(p.w, 4),
                "c_start": round(p.c_start, 4),
                "not_taken": [[a.kind.value, round(a.rate, 2), why] for a, why in p.alternatives],
            }
            # kept in the recorder, unlike the full plan: why each decision
            # was made, every half-hour of the plan, so a night can be read
            # back from history (decision of 8 November 2026) [D006]
            out["decision"] = {
                "made_at": p.made_at.isoformat(),
                "first": [p.first.kind.value, round(p.first.rate, 2)],
                "actions": [a.kind.value for a in p.actions],
                "index": [round(pt.hci, 2) for pt in p.points[PLAN_POINTS_PER_STEP - 1::PLAN_POINTS_PER_STEP]],
                "sigma": [round(pt.sigma_hci, 2) for pt in p.points[PLAN_POINTS_PER_STEP - 1::PLAN_POINTS_PER_STEP]],
                "minutes_outside": round(p.minutes_outside, 1),
                "costs": [round(p.comfort_cost, 4), round(p.energy_cost, 4), round(p.start_cost, 4)],
            }
        if control.pid is not None:
            d = control.pid
            out["pid"] = {"u": round(d.u, 3), "e": round(d.error, 3), "p": round(d.p, 3), "i": round(d.i, 3),
                          "d": round(d.d, 3), "kp": round(d.kp, 3), "ti_min": round(d.ti_h * 60.0, 1),
                          "td_min": round(d.td_h * 60.0, 1)}
    out["model"] = {
        "learning": answers.learning,
        "room": _r(answers.t),
        "mass": _r(answers.mass),
        "figures": {k: round(v, 4) for k, v in answers.figures.items()},
        "uncertainty": {k: round(v, 4) for k, v in answers.uncertainty.items()},
        "sensor_noise": _r(answers.sensor_noise, 3),
        "one_hour_error": _r(answers.one_hour_error, 3),
        "trusted": answers.trusted,
        "six_hour_trusted": answers.six_hour_trusted,
        "delay_min": _r(answers.delay_min, 1),
        "lag_min": _r(answers.lag_min, 1),
        "last_reason": answers.last_reason,
    }
    if economics is not None:
        # "no limit" where none binds [D096]
        out["power"] = {
            "allowance_kw": _r(economics.allowance_kw, 3),
            "ceiling": "no limit" if economics.ceiling is None else round(economics.ceiling, 2),
            "guidance_ceiling": _r(economics.guidance_ceiling),
            "turn_off": economics.turn_off,
            "reason": economics.reason,
        }
    if delivery is not None:
        out["delivery"] = {
            "wanted_setpoints": {h.climate: _r(h.wanted) for h in delivery.heads},
            "free_cooling_advised": delivery.free_cooling_advised,
            "free_cooling_reason": delivery.free_cooling_reason,
            "condensation_risk": delivery.condensation_risk,
            "reasons": list(delivery.reasons),
        }
    if limits is not None:
        out["limits"] = {
            "changed": [r.value for r in limits.changed],
            "not_delivered": limits.not_delivered,
            "held_off": limits.held_off,
            "constraints": list(limits.constraints),
            "unknown_constraints": list(limits.unknown_constraints),
            "missing": list(limits.missing),
            "reasons": list(limits.reasons),
        }
    out["compressors"] = {h.climate: {"state": h.compressor.value, "source": h.compressor_source.value}
                          for h in snapshot.heads}
    out["stale"] = list(snapshot.stale)
    return out


# --------------------------------------------------------------------------
# SCORECARD, per room [Prop]
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RoomScorecard:
    """A room's day being counted, and yesterday's result.

    learning is the latest week's average one-hour forecast error, from
    the model (trust.py); the rest are counted every cycle."""

    yesterday: DayScore | None = None
    learning: float | None = None
    _occupied_s: float = 0.0
    _in_band_s: float = 0.0
    _near_s: float = 0.0
    _run_s: float = 0.0
    _starts: int = 0
    _cost: float = 0.0
    _priced: bool = True
    _running: bool | None = None
    _way: int = 0

    def observe(
        self,
        *,
        dt_s: float,
        occupied: bool,
        index: float | None,
        band: tuple[float, float] | None,
        running: bool,
        draw_kw: float | None,
        price: float | None,
        way: int = 0,
    ) -> None:
        """One cycle of dt_s seconds. way: 1 cooling or drying, -1 heating,
        0 not running or not known; a running unit turned the other way is a
        stop and a start, so it counts as a start [D016]."""
        if occupied and index is not None and band is not None:
            lo, hi = band
            self._occupied_s += dt_s
            if lo <= index <= hi:
                self._in_band_s += dt_s
            if abs(index - (lo + hi) / 2.0) <= NEAR_CENTRE + _EPS:
                self._near_s += dt_s
        if running and (self._running is False or (self._running and way * self._way < 0)):
            self._starts += 1
        self._running = running
        if running and way != 0:
            self._way = way
        if running:
            self._run_s += dt_s
            if draw_kw is None or price is None:
                self._priced = False
            else:
                self._cost += draw_kw * dt_s / 3600.0 * price

    def today_dict(self, day: str) -> dict[str, Any]:
        """The day so far, for Memory, so a restart does not drop it."""
        return {"day": day, "occupied_s": self._occupied_s, "in_band_s": self._in_band_s, "near_s": self._near_s,
                "run_s": self._run_s, "starts": self._starts, "cost": self._cost, "priced": self._priced,
                "running": self._running, "way": self._way}

    def restore_today(self, data: Mapping[str, Any] | None, today: str, yesterday: str) -> None:
        """Restore the day so far. A day stored before a restart that crossed
        midnight is finished as yesterday's; an older one is dropped."""
        if not data or data.get("day") not in (today, yesterday):
            return
        try:
            nums = {k: float(data[k]) for k in ("occupied_s", "in_band_s", "near_s", "run_s", "cost")}
            starts = int(data["starts"])
        except (KeyError, TypeError, ValueError):
            return
        if not all(math.isfinite(v) and v >= 0.0 for v in nums.values()) or starts < 0:
            return
        self._occupied_s, self._in_band_s, self._near_s = nums["occupied_s"], nums["in_band_s"], nums["near_s"]
        self._run_s, self._cost, self._starts = nums["run_s"], nums["cost"], starts
        self._priced = bool(data.get("priced", True))
        running = data.get("running")
        self._running = running if isinstance(running, bool) else None
        way = data.get("way")
        self._way = way if isinstance(way, int) and way in (-1, 0, 1) else 0
        if data.get("day") == yesterday:
            self.end_of_day()

    def end_of_day(self) -> DayScore:
        """At midnight: the day's scores, kept as yesterday's; a new day starts."""
        hours = self._run_s / 3600.0
        score = DayScore(
            comfort_in_band=100.0 * self._in_band_s / self._occupied_s if self._occupied_s > 0 else None,
            near_centre=100.0 * self._near_s / self._occupied_s if self._occupied_s > 0 else None,
            starts_per_hour=self._starts / hours if hours > 0 else None,
            cost=self._cost if self._priced else None,
        )
        self.yesterday = score
        running = self._running
        self._occupied_s = self._in_band_s = self._near_s = self._run_s = self._cost = 0.0
        self._starts, self._priced, self._running = 0, True, running
        return score

    def as_dict(self) -> dict[str, Any]:
        """What Memory keeps: yesterday's scores."""
        y = self.yesterday
        return {} if y is None else {"comfort_in_band": y.comfort_in_band, "near_centre": y.near_centre,
                                     "starts_per_hour": y.starts_per_hour, "cost": y.cost}


# --------------------------------------------------------------------------
# WARNINGS [D069][D071][D077]
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RoomWarnings:
    """Which due warnings are said. One held back because the minimum run
    holds the off is kept until the off is actually sent [D077]."""

    _held: list[DueWarning] = field(default_factory=list)

    def to_say(
        self,
        due: Sequence[DueWarning],
        *,
        room: RoomConfig,
        engine: str | None,
        automatic: bool,
        waiting_for_unit: bool,
        asleep: bool,
        unit_on_at_start: bool,
        held_off: bool,
        off_sent: bool,
    ) -> list[Speech]:
        """The warnings to say this cycle.

        Dropped, and nothing said, while Automatic control is off, the room
        is waiting for its unit to report, or the room is asleep [D071]; and
        when the unit was not on at the start of this cycle, since every
        warning says the unit is turning off [D077]. Nothing is said with no
        speech engine or no speakers set, or with announcements off [D069].
        """
        if not automatic or waiting_for_unit or asleep or not unit_on_at_start:
            self._held = []
            return []
        if not (room.announce and room.announce_through) or engine is None:
            self._held = []
            return []
        if held_off:
            self._held.extend(due)
            return []
        # said with the off it warned of; dropped if that off is no longer coming
        say = [*self._held, *due] if off_sent else [*due]
        self._held = []
        return [
            Speech(engine, room.announce_through, w.kind.value,
                   {"room": room.name, "minutes": f"{math.ceil(max(w.seconds_to_off, 0.0) / 60.0)}"})
            for w in say
        ]


# --------------------------------------------------------------------------
# REPAIRS [R18]
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HouseFacts:
    """What the house's repairs are judged on, each cycle."""

    tariff_configured: bool
    tariff_error: str | None
    unknown_constraints: frozenset[str]
    speech_wanted: bool  # any room announces
    speech_engine: str | None
    speech_engines_available: int
    solar_forecast_configured: bool
    solar_forecast_error: str | None
    grid_sign_contradicted: bool
    grid_import_kwh: float | None
    shared_climates: Mapping[str, tuple[str, ...]]  # climate entity -> the rooms (names) on it


@dataclass(frozen=True, slots=True)
class RoomFacts:
    """What a room's repairs are judged on."""

    room: RoomConfig
    entities_missing: tuple[str, ...]  # its temperature or humidity sensor, not in Home Assistant at all
    last_correction: datetime | None
    learning_since: datetime
    last_reason: str | None


def house_issues(facts: HouseFacts) -> list[Issue]:
    """The house's repairs: each names what to fix; none is raised for
    something the user cannot act on [R18]."""
    out: list[Issue] = []
    if facts.unknown_constraints:  # [D034]
        out.append(Issue("unknown_constraint", "unknown_constraint",
                         {"constraints": ", ".join(sorted(facts.unknown_constraints))}))
    if facts.tariff_configured and facts.tariff_error is not None:  # [D004]
        out.append(Issue("tariff_unreadable", "tariff_unreadable", {"error": facts.tariff_error}))
    if facts.speech_wanted and facts.speech_engine is None:  # [D069]
        if facts.speech_engines_available == 0:
            out.append(Issue("speech_engine", "no_speech_engine", {}))
        elif facts.speech_engines_available > 1:
            out.append(Issue("speech_engine", "speech_engine_not_chosen",
                             {"count": str(facts.speech_engines_available)}))
    if facts.solar_forecast_configured and facts.solar_forecast_error is not None:  # [D061]
        out.append(Issue("solar_forecast_unreadable", "solar_forecast_unreadable",
                         {"error": facts.solar_forecast_error}))
    if facts.grid_sign_contradicted:  # [D038]
        out.append(Issue("grid_sign_contradicted", "grid_sign_contradicted", {}))
    if facts.grid_import_kwh is not None:  # [D043]
        out.append(Issue("grid_import", "grid_import", {"kwh": f"{facts.grid_import_kwh:.2f}"}))
    for climate, rooms in sorted(facts.shared_climates.items()):  # [D021]
        if len(rooms) > 1:
            out.append(Issue(f"shared_climate_{climate}", "shared_climate",
                             {"entity": climate, "rooms": ", ".join(rooms)}))
    return out


def room_issues(facts: RoomFacts, now: datetime) -> list[Issue]:
    """A room's repairs: a sensor it needs is gone [D046]; its filter has
    made no correction for 24 hours, naming the reading that stopped it
    [Prop]."""
    room = facts.room
    out: list[Issue] = []
    for entity in facts.entities_missing:
        out.append(Issue(f"room_sensor_missing_{room.room_id}_{entity}", "room_sensor_missing",
                         {"room": room.name, "entity": entity}))
    since = max(facts.learning_since, facts.last_correction) if facts.last_correction else facts.learning_since
    if (now - since).total_seconds() >= SILENT_FILTER_REPAIR_S:
        out.append(Issue(f"filter_silent_{room.room_id}", "filter_silent",
                         {"room": room.name, "reason": facts.last_reason or "no reading has been usable"}))
    return out


# --------------------------------------------------------------------------
# LOST AND REGAINED [R18]
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Availability:
    """A room's required readings: logged once when lost, once when back,
    nothing in between."""

    room: RoomConfig
    _lost: set[str] = field(default_factory=set)
    log: Callable[..., None] = field(default=_LOGGER.info)

    def update(self, present: Mapping[str, bool]) -> list[tuple[str, bool]]:
        """present: each required entity (room temperature and humidity,
        each head's climate entity) and whether it can be read. Returns the
        changes as (entity, now lost)."""
        changes: list[tuple[str, bool]] = []
        for entity, ok in present.items():
            if not ok and entity not in self._lost:
                self._lost.add(entity)
                changes.append((entity, True))
                self.log("%s: %s cannot be read; a stand-in is used where there is one", self.room.name,
                         entity)
            elif ok and entity in self._lost:
                self._lost.discard(entity)
                changes.append((entity, False))
                self.log("%s: %s can be read again", self.room.name, entity)
        return changes

    @property
    def lost(self) -> frozenset[str]:
        return frozenset(self._lost)

    def keep_only(self, required: set[str]) -> None:
        """WHEN the room is edited: forget a lost reading it no longer needs."""
        self._lost &= required
