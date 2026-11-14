"""Module 6, Delivery: what to ask of the blinds and the unit (Section 4.6).

Delivery turns the action and rate Control chose into the commands that
carry it out. It adds no decision of its own [I2]: the plan chose the
action and the blinds, the PID the rate, and the unit's learned curve
turns the rate into a setpoint [D054][D095]. Limits decides afterwards
what may actually reach the unit.

Every head of a room is sent the same action. Each head's setpoint is
worked out from its own reading, so every head is asked for the same
effort, which is what the model learns from: the mean effort of the heads
working (model/filter.py) [D020][D095].
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .capabilities import round_to_step
from .const import (
    BLIND_MOVE_POINTS,
    FAN_RESTING_ORDER,
    FAN_WORKING_ORDER,
    PID_FINE_STEP_C,
    RANGE_HALF_WIDTH_C,
    SETPOINT_MIN_HOLD_S,
    VANE_SETTLED_ORDER,
    WHOLE_DEGREE_CYCLE_S,
)
from .model.filter import HeatFilter
from .model.forecast import setpoint_for_rate
from .models import (
    BlindCommand,
    CapabilityProfile,
    ComfortResult,
    ControlResult,
    DeliveryResult,
    HeadCommand,
    HeadSnapshot,
    RoomChoices,
    RoomConfig,
    Snapshot,
    StepKind,
)
from .psychro import condensation_risk, dew_point

_CYCLE = timedelta(seconds=WHOLE_DEGREE_CYCLE_S)
_HOLD = timedelta(seconds=SETPOINT_MIN_HOLD_S)
_FAN_ONLY = "fan_only"
_DRY = "dry"
_COOL = "cool"
_HEAT = "heat"
_EPS = 1e-9


def pick(offered: Sequence[str], preferred: Sequence[str]) -> str | None:
    """The first preferred value the unit offers, matched without regard
    to case, as the unit spells it; None if it offers none [Set]."""
    lowered = {value.lower(): value for value in offered}
    return next((lowered[name] for name in preferred if name in lowered), None)


@dataclass(slots=True)
class _Alternation:
    """One head's whole-degree alternation: when its 30-minute cycle began,
    and the value last sent and since when."""

    cycle_start: datetime
    last: float | None = None
    since: datetime | None = None


def whole_degree_value(
    wanted: float, step: float, profile: CapabilityProfile, now: datetime, state: _Alternation
) -> float:
    """WHOLE DEGREES (4.6) [Prop][Der].

    low = the step at or below the wanted setpoint, high = low + step,
    f = (wanted - low) / step. High is sent for f x 30 minutes of each 30,
    low for the rest, so the average is the setpoint wanted. Each value is
    held at least 10 minutes: where f x 30 or (1 - f) x 30 minutes would be
    shorter, the nearer value is sent for the whole 30 minutes. A value
    sent is kept until it has stood 10 minutes, whichever pair the wanted
    setpoint moves to, so no value is ever held for less. Never outside
    the unit's range [D051].
    """
    low = step * math.floor(wanted / step + _EPS)
    high = low + step
    f = min(max((wanted - low) / step, 0.0), 1.0)
    cycle_min = WHOLE_DEGREE_CYCLE_S / 60.0
    hold_min = SETPOINT_MIN_HOLD_S / 60.0
    if f * cycle_min < hold_min:
        target = low
    elif (1.0 - f) * cycle_min < hold_min:
        target = high
    else:
        while now - state.cycle_start >= _CYCLE:
            state.cycle_start += _CYCLE
        into = (now - state.cycle_start).total_seconds() / 60.0
        target = high if into < f * cycle_min else low
    target = round_to_step(target, profile)
    if (
        state.last is not None
        and state.since is not None
        and abs(target - state.last) >= _EPS
        and now - state.since < _HOLD
    ):
        # every value sent stands 10 minutes, whichever pair the wanted
        # setpoint has moved to (decision of 8 November 2026: a hold only
        # within the pair let the living room go 20, 21, 19 in two minutes)
        return state.last
    if state.last is None or abs(target - state.last) >= _EPS:
        state.last, state.since = target, now
    return target


@dataclass(slots=True)
class RoomDelivery:
    """One room's Delivery. The only state it keeps is each head's place
    in its whole-degree alternation."""

    room: RoomConfig
    _alternation: dict[str, _Alternation] = field(default_factory=dict)

    def deliver(
        self,
        *,
        now: datetime,
        control: ControlResult,
        snapshot: Snapshot,
        comfort: ComfortResult,
        choices: RoomChoices,
        heat: HeatFilter | None,
        free_cooling: tuple[bool, str | None] = (False, None),
    ) -> DeliveryResult:
        """EVERY CYCLE, per room, given the action, the rate u and the
        plan's blinds (delivery.py). free_cooling is the plan's own
        counterfactual on opening up [D073][D105]: carried through as the
        model gave it."""
        reasons: list[str] = []
        profile = self.room.profile

        if comfort.off or control.kind not in (StepKind.COOL, StepKind.HEAT, StepKind.DRY):
            # no setpoint goes out: the next run starts its own hold and cycle
            self._alternation.clear()
        if comfort.off:
            return self._only(StepKind.OFF, control.rate, f"Comfort said off: {comfort.reason}.")
        if control.kind in (StepKind.OFF, StepKind.NONE):
            return self._only(control.kind, control.rate, control.reason)
        if profile is None:
            return self._only(StepKind.NONE, control.rate, "The units' abilities have not been read.")

        blinds, left_alone = self._blinds(control, snapshot, reasons)

        kind = control.kind
        has_fan_only = pick(profile.hvac_modes, (_FAN_ONLY,)) is not None
        rest = (
            tuple(self._resting(h, profile, choices) for h in snapshot.heads) if has_fan_only else ()
        )
        if kind is StepKind.REST and not has_fan_only:
            # a unit with no fan-only mode rests off [D057]
            reasons.append("The unit has no fan-only mode, so resting is off.")
            advised, advice_reason = free_cooling
            return DeliveryResult(StepKind.OFF, 0.0, (), blinds, left_alone, advised, advice_reason, False,
                                  tuple(reasons))

        heads: list[HeadCommand] = []
        sent: list[float] = []
        for head in snapshot.heads:
            cmd = self._head(kind, control.rate, head, profile, choices, heat, now, reasons)
            if cmd is None:
                return DeliveryResult(StepKind.NONE, control.rate, (), blinds, left_alone, False, None, False,
                                      tuple(reasons), rest)
            heads.append(cmd)
            if cmd.setpoint is not None:
                sent.append(cmd.setpoint)
            elif cmd.setpoint_low is not None:
                sent.append(cmd.setpoint_low)

        advised, advice_reason = free_cooling
        risk = self._condensation(kind, sent, snapshot)
        if risk:
            reasons.append("The setpoint is within 2.5 C of the room's dew point (reported only).")
        reasons.append(control.reason)
        return DeliveryResult(kind, control.rate, tuple(heads), blinds, left_alone, advised, advice_reason, risk,
                              tuple(reasons), rest)

    # ------------------------------------------------------------------

    def _only(self, kind: StepKind, rate: float, reason: str) -> DeliveryResult:
        return DeliveryResult(kind, rate, (), (), (), False, None, False, (reason,))

    def _blinds(
        self, control: ControlResult, snapshot: Snapshot, reasons: list[str]
    ) -> tuple[tuple[BlindCommand, ...], tuple[str, ...]]:
        """BLINDS, those that report a position, where blind control is
        allowed: moved to the plan's position if more than 5 points away
        [D022][Set]. A blind whose position is unknown is left alone."""
        if control.blinds_closed is None or not self.room.allow_blind_control:
            return (), ()
        target = 0.0 if control.blinds_closed else 100.0
        moves: list[BlindCommand] = []
        alone: list[str] = []
        for reading in snapshot.blind_positions:
            if reading.entity_id is None:
                continue
            if reading.value is None:
                alone.append(reading.entity_id)
                reasons.append(f"{reading.entity_id}: its position is unknown, so it is left alone.")
                continue
            if abs(reading.value * 100.0 - target) > BLIND_MOVE_POINTS:
                moves.append(BlindCommand(reading.entity_id, target))
        return tuple(moves), tuple(alone)

    def _head(
        self,
        kind: StepKind,
        rate: float,
        head: HeadSnapshot,
        profile: CapabilityProfile,
        choices: RoomChoices,
        heat: HeatFilter | None,
        now: datetime,
        reasons: list[str],
    ) -> HeadCommand | None:
        """One head's command; None, with the reason, when it cannot be
        worked out (the unit is then left alone) [D018]."""
        if kind is StepKind.REST:
            return self._resting(head, profile, choices)
        fan = pick(profile.fan_modes, FAN_WORKING_ORDER)  # [Set]
        vane = pick(profile.vertical_vanes, VANE_SETTLED_ORDER) if choices.automatic_vane_control else None
        mode_name = {StepKind.DRY: _DRY, StepKind.COOL: _COOL, StepKind.HEAT: _HEAT}[kind]
        mode = pick(profile.hvac_modes, (mode_name,))
        if mode is None:
            reasons.append(f"The unit does not offer {mode_name}.")  # [D024]
            return None
        if heat is None:
            reasons.append("The room's model has not started, so no setpoint can be worked out.")
            return None
        if not head.available or head.own_temperature is None:
            reasons.append(f"{head.climate}: its own reading is missing, so no setpoint can be worked out.")
            return None
        # positive cools, negative heats; DRY is sent with the setpoint that
        # asks for its own rate on dry's curve [D078][D109]
        if kind is StepKind.DRY:
            wanted = setpoint_for_rate(heat, head.own_temperature, abs(rate), dry=True)
        elif kind is StepKind.COOL:
            wanted = setpoint_for_rate(heat, head.own_temperature, abs(rate))
        else:
            wanted = setpoint_for_rate(heat, head.own_temperature, -abs(rate))
        wanted = min(max(wanted, profile.min_setpoint), profile.max_setpoint)  # [D051]
        value = self._stepped(head.climate, wanted, profile, now)
        if profile.range_only:
            low = round_to_step(value - RANGE_HALF_WIDTH_C, profile)
            high = round_to_step(value + RANGE_HALF_WIDTH_C, profile)
            return HeadCommand(head.climate, mode, None, low, high, wanted, fan, vane)  # [D024]
        return HeadCommand(head.climate, mode, value, None, None, wanted, fan, vane)

    def _resting(self, head: HeadSnapshot, profile: CapabilityProfile, choices: RoomChoices) -> HeadCommand:
        """REST: compressor off; fan-only at its quietest fan speed [D057][Set]."""
        fan = pick(profile.fan_modes, FAN_RESTING_ORDER)
        vane = pick(profile.vertical_vanes, VANE_SETTLED_ORDER) if choices.automatic_vane_control else None
        return HeadCommand(head.climate, pick(profile.hvac_modes, (_FAN_ONLY,)), None, None, None, None, fan, vane)

    def _stepped(self, climate: str, wanted: float, profile: CapabilityProfile, now: datetime) -> float:
        """The value to send: rounded to a fine step, or alternated between
        the two nearest values of a coarse one (4.6)."""
        step = profile.setpoint_step
        if step is None or step <= PID_FINE_STEP_C + _EPS:
            return round_to_step(wanted, profile)
        state = self._alternation.setdefault(climate, _Alternation(now))
        return whole_degree_value(wanted, step, profile, now, state)

    def _condensation(self, kind: StepKind, sent: Sequence[float], snapshot: Snapshot) -> bool:
        """CONDENSATION: the setpoint within 2.5 C of the room's dew point,
        reported only [Set]."""
        t, rh = snapshot.room_temperature.value, snapshot.room_humidity.value
        if kind not in (StepKind.COOL, StepKind.DRY) or not sent or t is None or rh is None or rh <= 0.0:
            return False
        return condensation_risk(min(sent), dew_point(t, rh))
