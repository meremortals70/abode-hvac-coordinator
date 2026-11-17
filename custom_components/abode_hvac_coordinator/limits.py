"""Module 7, Limits: what may reach the unit (Section 3, Module 7).

Limits applies the user's controls and the equipment's limits to the step
Delivery proposed, the same way whichever controller is in charge, and
never trades them [D034]. It decides nothing about comfort or cost: the
plan already works inside these limits (4.4), and Limits makes sure of it.

Rules 1-7 choose the step; the first that applies wins. Rules 8-10 then
apply to whatever was chosen: what the unit can do, the short-cycle guard
(per outdoor unit, across rooms, so it is the house's) and the tariff's
constraints. Stops the user makes, lockout, Automatic control switched off
and Leave, are not held by the guard [D088][D089].
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from .capabilities import round_to_step
from .const import CYCLE_S, KNOWN_CONSTRAINTS, MIN_REST_S, MIN_RUN_S, RANGE_HALF_WIDTH_C
from .model.unit import seen_running
from .models import (
    BandRule,
    BlindCommand,
    CapabilityProfile,
    ComfortResult,
    CompressorState,
    DeliveryResult,
    DueWarning,
    HeadCommand,
    LimitRule,
    LimitsResult,
    RoomChoices,
    RoomConfig,
    RoomEconomics,
    Snapshot,
    StepKind,
    ValueTag,
    WarningKind,
)

RUNNING_KINDS = frozenset({StepKind.COOL, StepKind.HEAT, StepKind.DRY})  # dry counts as running [D016]
_OFF_MODES = frozenset({"off", None})
_MIN_RUN = timedelta(seconds=MIN_RUN_S)
_MIN_REST = timedelta(seconds=MIN_REST_S)


@dataclass(slots=True)
class RoomStep:
    """A room's step after rules 1-8, before the house's guard."""

    room_id: str
    kind: StepKind
    heads: tuple[HeadCommand, ...]
    blinds: tuple[BlindCommand, ...]
    rest_heads: tuple[HeadCommand, ...]
    warnings: list[DueWarning] = field(default_factory=list)
    changed: list[LimitRule] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    user_stop: bool = False
    lockout: bool = False
    automatic_off: bool = False
    constraints: tuple[str, ...] = ()
    unknown_constraints: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    opening_grace: bool = False  # a door or window open, inside its grace [D107]
    # the rule that set the step as it stands now, and the sentence it gave:
    # last writer wins, the same way kind does, so the recorder can say which
    # rule the unit was actually stopped by (defect of 17 November 2026)
    rule: LimitRule | None = None
    reason: str | None = None

    def to(self, rule: LimitRule, kind: StepKind, reason: str, heads: tuple[HeadCommand, ...] = ()) -> None:
        """Set the step to kind, recording the rule and why."""
        self.kind, self.heads = kind, heads
        self.rule, self.reason = rule, reason
        if kind in (StepKind.OFF, StepKind.NONE):
            self.blinds = ()
        self.changed.append(rule)
        self.reasons.append(reason)


@dataclass(slots=True)
class RoomLimits:
    """One room's Limits, with what it remembers between cycles."""

    room: RoomConfig
    _automatic: bool | None = None
    _empty_warned_at: datetime | None = None
    _can_speak: bool = True
    _opening_since: datetime | None = None
    _opening_warned: set[WarningKind] = field(default_factory=set)
    last_running: tuple[HeadCommand, ...] = ()

    def step(
        self,
        *,
        now: datetime,
        proposed: DeliveryResult,
        snapshot: Snapshot,
        comfort: ComfortResult,
        economics: RoomEconomics | None,
        choices: RoomChoices,
        shared_climate: bool,
        can_speak: bool = True,
    ) -> RoomStep:
        """Rules 1-8 and 10 for this room; rule 9 is the house's guard.
        can_speak: a speech engine is there to say a warning [D069]."""
        s = RoomStep(self.room.room_id, proposed.kind, proposed.heads, proposed.blinds, proposed.rest_heads)
        self._can_speak = can_speak
        self._choose(s, now, snapshot, comfort, economics, choices, shared_climate)
        self._abilities(s)
        self._tariff(s, snapshot)
        return s

    # rules 1-7: the first that applies wins -------------------------------

    def _choose(
        self,
        s: RoomStep,
        now: datetime,
        snapshot: Snapshot,
        comfort: ComfortResult,
        economics: RoomEconomics | None,
        choices: RoomChoices,
        shared_climate: bool,
    ) -> None:
        switched_off = self._automatic is True and not choices.automatic_control
        self._automatic = choices.automatic_control
        empty = comfort.off and comfort.rule is BandRule.UNOCCUPIED
        if not empty:
            self._empty_warned_at = None

        # 1. LOCKOUT [D021]
        if self.room.lockout_reason or shared_climate:
            why = self.room.lockout_reason or "another room uses the same air conditioner"
            s.to(LimitRule.LOCKOUT, StepKind.OFF, f"Locked out: {why}.")
            s.lockout = s.user_stop = True
            return
        # 2. AUTOMATIC CONTROL [D049][D089]
        if switched_off:
            s.to(LimitRule.AUTOMATIC_CONTROL, StepKind.OFF, "Automatic control was switched off: the unit is sent off once.")
            s.automatic_off = s.user_stop = True
            return
        if not choices.automatic_control:
            s.to(LimitRule.AUTOMATIC_CONTROL, StepKind.NONE, "Automatic control is off: the room's controls are the user's.")
            s.automatic_off = s.user_stop = True
            return
        # 3. LEAVE [D088]
        if comfort.rule is BandRule.LEFT:
            s.to(LimitRule.LEAVE, StepKind.OFF, "Leave: off at once, until someone comes back or a choice changes.")
            s.user_stop = True
            return
        # 4. EMPTY ROOM [D012][D082]
        if empty:
            self._empty(s, now, snapshot, comfort, choices)
            return
        # 5. AN OPEN DOOR OR WINDOW [D050]
        if self._opening(s, now, snapshot):
            return
        # 6. MISSING READING [D018]
        missing = tuple(
            r.entity_id or name
            for name, r in (("room temperature", snapshot.room_temperature), ("room humidity", snapshot.room_humidity))
            if r.tag is ValueTag.MISSING
        )
        if comfort.room_index is None or (not comfort.off and not comfort.points):
            s.missing = missing
            what = ", ".join(missing) if missing else "the band"
            s.to(LimitRule.MISSING_READING, StepKind.NONE, f"Missing: {what}. The unit is left as it is.")
            return
        # 7. ENFORCED POWER [D063][D067]
        if economics is not None and economics.turn_off:
            s.to(LimitRule.ENFORCED_POWER, StepKind.OFF, f"Enforced power management: off until it fits. {economics.reason}")
            return

    def _empty(self, s: RoomStep, now: datetime, snapshot: Snapshot, comfort: ComfortResult,
               choices: RoomChoices) -> None:
        """An empty room is off; where a warning can be heard it is given
        first, and the unit stops one warning grace later [D012][D071][D082]."""
        unit_on = any(h.mode not in _OFF_MODES for h in snapshot.heads)
        heard = (
            self.room.announce and bool(self.room.announce_through) and self._can_speak
            and choices.automatic_control and not comfort.sleeping and unit_on
        )
        if heard and self._empty_warned_at is None:
            self._empty_warned_at = now
            s.warnings.append(DueWarning(WarningKind.EMPTY_ROOM, self.room.warning_grace_s))
        if heard or self._empty_warned_at is not None:
            assert self._empty_warned_at is not None
            left = self.room.warning_grace_s - (now - self._empty_warned_at).total_seconds()
            if left > 0:
                s.to(LimitRule.EMPTY_ROOM, StepKind.NONE, f"The room is empty: off in {math.ceil(left)} s, after the warning.")
                return
        s.to(LimitRule.EMPTY_ROOM, StepKind.OFF, "The room is empty: off.")

    def _opening(self, s: RoomStep, now: datetime, snapshot: Snapshot) -> bool:
        """An open door or window: nothing new is started; after the
        room's grace, with warnings, the unit stops [D050].

        An opening opened while the unit is cooling stops it through the
        plan, not here: the model's forecast carries the opening as it is
        (a + v o), so the next plan rests when the room is being ventilated
        better than the unit can cool it [D105]. The stop is held to the
        minimum run like any other, which is the short-cycle guard."""
        # a door or window: to the outdoors, or to another room. Either being
        # open holds the unit to its grace and then stops it [D050]; only the
        # ones to the outdoors carry air, which is the vent term's business and
        # not this rule's [D111]
        open_now = [f for f in (*snapshot.openings, *snapshot.interior_openings) if f.value is True]
        if not open_now:
            return False
        if any(f.since is None for f in open_now):
            s.to(LimitRule.OPENING, StepKind.NONE, "A door or window is open and when it opened cannot be read: "
                 "the unit is held as it is.")
            return True
        opened = min(f.since for f in open_now if f.since is not None)
        if self._opening_since != opened:
            self._opening_since, self._opening_warned = opened, set()
        grace = self.room.opening_grace_s
        elapsed = (now - opened).total_seconds()
        if elapsed >= grace:
            s.to(LimitRule.OPENING, StepKind.OFF, "A door or window has been open past its grace: off.")
            return True
        # a door or window is open and the unit is still inside the opening
        # grace: the mode sensor says so, not the band rule [D107]
        s.opening_grace = True
        for kind, at in ((WarningKind.OPENING_FIRST, grace - self.room.warning_grace_s),
                         (WarningKind.OPENING_FINAL, grace - CYCLE_S)):
            if elapsed >= at and kind not in self._opening_warned:
                self._opening_warned.add(kind)
                s.warnings.append(DueWarning(kind, grace - elapsed))
        if s.kind in RUNNING_KINDS and not self._compressor_running(snapshot):
            if s.rest_heads:
                s.to(LimitRule.OPENING, StepKind.REST, "A door or window is open: nothing new is started.",
                     s.rest_heads)
            else:
                s.to(LimitRule.OPENING, StepKind.OFF, "A door or window is open: nothing new is started.")
        return True

    @staticmethod
    def _compressor_running(snapshot: Snapshot) -> bool:
        # a unit running unseen keeps its grace: it is not "something new
        # started" [D050]
        return any(seen_running(h) for h in snapshot.heads)

    # rule 8 --------------------------------------------------------------

    def _abilities(self, s: RoomStep) -> None:
        """8. WHAT THE UNIT CAN DO: anything not in the stored profile is
        removed; setpoints are kept within its range [D024][D048][D051]."""
        profile = self.room.profile
        if profile is None or not s.heads:
            return
        cleaned = tuple(self._clean(h, profile, s) for h in s.heads)
        if cleaned != s.heads:
            s.heads = cleaned
            s.changed.append(LimitRule.UNIT_ABILITIES)

    @staticmethod
    def _clean(h: HeadCommand, profile: CapabilityProfile, s: RoomStep) -> HeadCommand:
        out = h
        if h.mode is not None and h.mode not in profile.hvac_modes:
            out = replace(out, mode=None)
            s.reasons.append(f"{h.climate}: mode {h.mode} is not one the unit offers.")
        if h.fan_mode is not None and h.fan_mode not in profile.fan_modes:
            out = replace(out, fan_mode=None)
            s.reasons.append(f"{h.climate}: fan speed {h.fan_mode} is not one the unit offers.")
        if h.vertical_vane is not None and h.vertical_vane not in profile.vertical_vanes:
            out = replace(out, vertical_vane=None)
            s.reasons.append(f"{h.climate}: vane {h.vertical_vane} is not one the unit offers.")
        if profile.range_only and h.setpoint is not None:
            # a unit that takes only a range gets setpoint -1.0 to +1.0 C [D024]
            out = replace(out, setpoint=None,
                          setpoint_low=round_to_step(h.setpoint - RANGE_HALF_WIDTH_C, profile),
                          setpoint_high=round_to_step(h.setpoint + RANGE_HALF_WIDTH_C, profile))

        def within(v: float | None) -> float | None:
            return None if v is None else min(max(v, profile.min_setpoint), profile.max_setpoint)

        kept = replace(out, setpoint=within(out.setpoint), setpoint_low=within(out.setpoint_low),
                       setpoint_high=within(out.setpoint_high))
        if kept != out:
            s.reasons.append(f"{h.climate}: setpoint kept within the unit's range.")
        return kept

    # rule 10 -------------------------------------------------------------

    @staticmethod
    def _tariff(s: RoomStep, snapshot: Snapshot) -> None:
        """10. TARIFF CONSTRAINTS: every constraint declared enforceable for
        now is recorded and held; none of those this component knows forbids
        running the unit (no_grid_import is met by the power budget, rule 7).
        One it does not know is reported, never dropped [D034]."""
        now = snapshot.tariff_now
        if now is None:
            return
        s.constraints = now.constraints
        s.unknown_constraints = tuple(c for c in now.constraints if c not in KNOWN_CONSTRAINTS)
        if s.unknown_constraints:
            s.changed.append(LimitRule.TARIFF_CONSTRAINTS)
            s.reasons.append("A tariff constraint this component does not know is in force: "
                             + ", ".join(s.unknown_constraints) + ". It is reported.")

    def remember(self, result: LimitsResult) -> None:
        """What was allowed this cycle; a running step is what the guard
        keeps the room on if a stop is refused."""
        if result.kind in RUNNING_KINDS and result.heads:
            self.last_running = result.heads
        elif result.kind in (StepKind.OFF, StepKind.REST) and not result.held_off:
            self.last_running = ()


# --------------------------------------------------------------------------
# 9. SHORT-CYCLE GUARD, per outdoor unit, across rooms [D016][D020]
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Outdoor:
    running: bool
    since: datetime | None  # None: not known since start-up, so not held
    direction: int = 0  # +1 cooling or dry, -1 heating, 0 not known


def band_distances(index: float | None, low: float | None, high: float | None) -> tuple[float, float]:
    """How far an index is outside its band (0 inside), and from its
    centre: the order in which an outdoor unit serves rooms wanting
    opposite ways (decision of 6 November 2026). Nothing known: 0, 0."""
    if index is None or low is None or high is None:
        return 0.0, 0.0
    return max(low - index, index - high, 0.0), abs(index - (low + high) / 2.0)


def _wanted_way(e: GuardEntry, unit: str) -> int:
    """The way a room wants its outdoor unit: its step's way, or for a room
    left as it is (NONE) and seen running, the way its heads run, read from
    their modes; 0 for none [D016][D089]."""
    if e.step.kind in RUNNING_KINDS:
        return _direction(e.step.kind)
    if e.step.kind is StepKind.NONE and e.observed_running.get(unit, False) and e.observed_heads:
        return _direction(_running_kind(e.observed_heads))
    return 0


def _direction(kind: StepKind) -> int:
    """The way a running step moves heat: cooling and dry one way, heating
    the other."""
    if kind is StepKind.HEAT:
        return -1
    if kind in (StepKind.COOL, StepKind.DRY):
        return 1
    return 0


@dataclass(frozen=True, slots=True)
class GuardEntry:
    """One room's step for the guard, and the outdoor units its heads use."""

    limits: RoomLimits
    step: RoomStep
    outdoor_units: tuple[str, ...]
    observed_running: Mapping[str, bool]  # per outdoor unit, from the snapshot
    # the heads seen running, each kept in the mode it runs in: what the
    # guard keeps a room on when a start made by hand is not yet 10
    # minutes old and the plan would stop it [D016][D089]
    observed_heads: tuple[HeadCommand, ...] = ()
    # how far the room's index is outside its band now (0 inside), and from
    # its centre: which room an outdoor unit serves when rooms on it want
    # opposite ways (decision of 6 November 2026)
    outside: float = 0.0
    from_centre: float = 0.0
    # per outdoor unit, the compressor as its heads see it [D110]: whether
    # it is running, stopped, or not known. A run is timed from this, not
    # from the command, so a command that never started the compressor is
    # not a run
    observed_state: Mapping[str, CompressorState] = field(default_factory=dict)


@dataclass(slots=True)
class HouseGuard:
    """The short-cycle guard: each outdoor unit runs at least 10 minutes
    and rests at least 5. An outdoor unit is wanted running if any room on
    it wants it; dry counts as running [D016][D020]."""

    _units: dict[str, _Outdoor] = field(default_factory=dict)

    def apply(self, now: datetime, entries: Sequence[GuardEntry]) -> dict[str, LimitsResult]:
        """Rule 9 for every room, then each room's allowed step."""
        by_unit: dict[str, list[GuardEntry]] = {}
        for e in entries:
            for u in e.outdoor_units:
                by_unit.setdefault(u, []).append(e)
        for u, rooms in by_unit.items():
            if u not in self._units:
                seen = self._seen(u, rooms)
                self._units[u] = _Outdoor(
                    any(e.observed_running.get(u, False) for e in rooms) if seen is None else seen, None)
        held_off: set[str] = set()
        for u, rooms in by_unit.items():
            self._one_way(u, rooms)
        for u, rooms in by_unit.items():
            state = self._units[u]
            # what the compressor is seen to do now: the unit's own word
            # (a frequency sensor, a meter on its circuit, an hvac_action),
            # then the house-load step, and where nothing can say, the state
            # latched last cycle [D110]
            seen = self._seen(u, rooms)
            running_now = state.running if seen is None else seen
            ways = {_wanted_way(e, u) for e in rooms} - {0}
            if running_now and state.direction and ways and state.direction not in ways:
                # a reversal is a stop and a start: the run must have lasted
                # its 10 minutes, and the rest its 5, before the other way
                # runs [D016]. Inside the run, the rooms that were running
                # the unit's way keep running whatever their step asks
                # (unless the user stopped them), and the rooms wanting the
                # other way rest
                # running since start-up (since not known): its run is taken
                # as served, and the rest still holds [D016]
                ran_for = now - state.since if state.since is not None else _MIN_RUN
                for e in rooms:
                    if e.step.kind not in RUNNING_KINDS and e.step.kind not in (StepKind.REST, StepKind.OFF):
                        continue
                    kept_way = (e.limits.last_running
                                and _direction(_running_kind(e.limits.last_running)) == state.direction)
                    if ran_for < _MIN_RUN and kept_way and not e.step.user_stop:
                        # an OFF that is not the user's is held to the run as
                        # it is without a reversal
                        if e.step.kind is StepKind.OFF:
                            held_off.add(e.step.room_id)
                        self._keep_running(e, (_MIN_RUN - ran_for).total_seconds())
                    elif e.step.kind in RUNNING_KINDS:
                        wait = (_MIN_RUN - ran_for) + _MIN_REST if ran_for < _MIN_RUN else _MIN_REST
                        self._hold_rest(e.step, wait.total_seconds())
                continue
            # a room left as it is wants the unit as it is seen to run,
            # the same rule that records the unit's state below [D016][D020]
            wanted = any(
                e.step.kind in RUNNING_KINDS
                or (e.step.kind is StepKind.NONE and e.observed_running.get(u, False))
                for e in rooms
            )
            since = now - state.since if state.since is not None else None
            # a stop the meter says already happened is not one to hold: the
            # run is timed from the compressor, not from the command [D110]
            if wanted and not running_now and since is not None and since < _MIN_REST:
                for e in rooms:
                    if e.step.kind in RUNNING_KINDS:
                        self._hold_rest(e.step, (_MIN_REST - since).total_seconds())
            elif not wanted and running_now and since is not None and since < _MIN_RUN:
                for e in rooms:
                    if not e.step.user_stop and e.limits.last_running and e.step.kind is not StepKind.NONE:
                        if e.step.kind is StepKind.OFF:
                            held_off.add(e.step.room_id)
                        self._keep_running(e, (_MIN_RUN - since).total_seconds())
        results: dict[str, LimitsResult] = {}
        for e in entries:
            results[e.step.room_id] = _result(e.step, e.step.room_id in held_off)
            e.limits.remember(results[e.step.room_id])
            if results[e.step.room_id].kind is StepKind.NONE and e.observed_heads:
                # left as it is and seen running: that is the running command
                e.limits.last_running = e.observed_heads
        for u, rooms in by_unit.items():
            # a room whose unit is left as it is (Automatic control off, an
            # opening that cannot be timed) runs as it is seen to run, so a
            # start made by hand counts when control comes back [D016][D089]
            # The run is the compressor's, not the command's [D110]: a
            # frequency sensor, a meter on its circuit or an hvac_action is
            # the unit's own word; the house-load step is the fallback where
            # none of those exists; where the compressor cannot be told
            # either way the command is the best effort there is
            running = seen if seen is not None else any(
                results[e.step.room_id].kind in RUNNING_KINDS
                or (results[e.step.room_id].kind is StepKind.NONE and e.observed_running.get(u, False))
                for e in rooms
            )
            state = self._units[u]
            ways = {
                _direction(results[e.step.room_id].kind) if results[e.step.room_id].kind in RUNNING_KINDS
                else (_direction(_running_kind(e.observed_heads))
                      if results[e.step.room_id].kind is StepKind.NONE and e.observed_running.get(u, False)
                      and e.observed_heads else 0)
                for e in rooms
            } - {0}
            way = next(iter(ways)) if len(ways) == 1 else state.direction
            if running != state.running:
                state.running, state.since = running, now
            if running:
                state.direction = way
        return results

    @staticmethod
    def _seen(unit: str, rooms: Sequence[GuardEntry]) -> bool | None:
        """Whether the unit's compressor is running, from what the rooms
        observe [D110]: yes if any head on it is seen running, no only if
        every head on it is seen stopped, and not known where the heads
        disagree or one of them cannot say.

        The heads on one outdoor unit share its compressor, so the room that
        cannot say does not overrule the one that can. Where no room can say,
        None leaves the guard on the command as the best effort there is.
        """
        states = [e.observed_state[unit] for e in rooms if unit in e.observed_state]
        if any(s is CompressorState.RUNNING for s in states):
            return True
        if states and all(s is CompressorState.STOPPED for s in states):
            return False
        return None

    @staticmethod
    def _one_way(unit: str, rooms: Sequence[GuardEntry]) -> None:
        """An outdoor unit runs one way at a time: a multi-split cannot heat
        one room and cool another. Where rooms on it want opposite ways, the
        room furthest outside its band (then furthest from its centre) has
        it, and the rooms wanting the other way rest [D020] (decision of 6
        November 2026)."""
        wanting = [e for e in rooms if _wanted_way(e, unit) != 0]
        if len({_wanted_way(e, unit) for e in wanting}) < 2:
            return
        # a room left as it is (run by hand, Automatic control off, an
        # empty-room warning counting down) is never changed, so its way
        # stands; otherwise the room furthest outside its band, then from
        # its centre, has it
        left = [e for e in wanting if e.step.kind is StepKind.NONE]
        first = left[0] if left else max(wanting, key=lambda e: (e.outside, e.from_centre))
        way = _wanted_way(first, unit)
        reason = (f"The outdoor unit {unit} runs one way at a time; another room on it is "
                  + ("left as it is, running the other way." if left else "further from its band."))
        for e in wanting:
            if e.step.kind in RUNNING_KINDS and _wanted_way(e, unit) != way:
                if e.step.rest_heads:
                    e.step.to(LimitRule.SHORT_CYCLE_GUARD, StepKind.REST, reason, e.step.rest_heads)
                else:
                    e.step.to(LimitRule.SHORT_CYCLE_GUARD, StepKind.OFF, reason)

    @staticmethod
    def _hold_rest(s: RoomStep, wait_s: float) -> None:
        reason = f"Short-cycle guard: the compressor rested less than 5 minutes; start held {math.ceil(wait_s)} s."
        if s.rest_heads:
            s.to(LimitRule.SHORT_CYCLE_GUARD, StepKind.REST, reason, s.rest_heads)
        else:
            s.to(LimitRule.SHORT_CYCLE_GUARD, StepKind.OFF, reason)

    @staticmethod
    def _keep_running(e: GuardEntry, wait_s: float) -> None:
        """A stop refused: keep running on the last running command, its fan
        included. The step refused here is a stop or a rest, and a rest
        step's fan is the resting fan, so it has no fan choice of its own to
        stand: writing it onto the running command would slow the head that
        is being kept running [D016][D110]."""
        heads = e.limits.last_running
        e.step.to(LimitRule.SHORT_CYCLE_GUARD, _running_kind(heads), "Short-cycle guard: the compressor ran less "
                  f"than 10 minutes; stop held {math.ceil(wait_s)} s.", heads)


def _running_kind(heads: Sequence[HeadCommand]) -> StepKind:
    """The kind of the running command being kept, from its mode."""
    modes = {h.mode for h in heads}
    if "heat" in modes:
        return StepKind.HEAT
    if "dry" in modes:
        return StepKind.DRY
    return StepKind.COOL


def _result(s: RoomStep, held_off: bool) -> LimitsResult:
    """NOT DELIVERED: any rule that changed, held or refused the step
    tells Control, so its integral holds [D054]. OFF commands the unit
    off; NONE sends nothing [D017]."""
    changed = tuple(dict.fromkeys(s.changed))
    not_delivered = any(r is not LimitRule.TARIFF_CONSTRAINTS for r in changed)
    return LimitsResult(
        kind=s.kind,
        heads=s.heads,
        blinds=s.blinds,
        warnings=tuple(s.warnings),
        changed=changed,
        not_delivered=not_delivered,
        held_off=held_off,
        lockout=s.lockout,
        automatic_off=s.automatic_off,
        constraints=s.constraints,
        unknown_constraints=s.unknown_constraints,
        missing=s.missing,
        reasons=tuple(r for r in s.reasons if r),
        opening_grace=s.opening_grace,
        rule=s.rule,
        reason=s.reason or None,
    )
