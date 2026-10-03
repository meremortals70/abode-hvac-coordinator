"""Layer 2 — outer-loop regulation.

Pure. No Home Assistant imports.

WHAT THIS IS FOR
----------------
Layer 3 decides what the room should feel like and solves that into a dry-bulb
target. The unit's own thermostat then regulates — but not against the room. It
regulates against its own return-air sensor, sitting high on a wall inside the
head, reading the air the unit has just pulled over its own coil.

Those two temperatures are not the same, and the difference is not a constant.
It moves with fan speed, with stratification, with how long the unit has been
running and with where the room sensor is. Commanding 24.0 and getting 24.0 at
the head routinely leaves the occupied part of the room at 25.5.

This module closes the outer loop. It trims the commanded setpoint until the
**room sensor** reads the target, and it is the only thing in the project that
is allowed to command a temperature different from the one Layer 3 solved.

WHY AN INTEGRATOR AND NOT A TABLE
---------------------------------
The obvious alternative is a per-room calibration offset. It fails on the same
day it is measured: the offset that is right at 3 kW is wrong at idle, and the
offset that is right in still air is wrong with the vanes swinging.

An integrator needs no calibration, adapts as conditions change, and converges
on whatever offset is true right now. What it costs is a tuning discipline —
integrate slowly, never wind up, never fight a loop that is not running.

A PID, BUILT ON WHAT THE ROOM HAS LEARNED (0.9.0, DR-054)
---------------------------------------------------------
Until 0.9.0 this loop was integral-only, on the reasoning that a second
proportional loop would fight the unit's thermostat. That reasoning does not
hold for a cascade: the unit's thermostat is the fast inner loop and this is
the slow outer one. Integral-only had no way to ease off near target and no
sense of when the room would arrive.

The setpoint is now built from four terms, in the room's own units:

* **Feed-forward.** The approach that just cancels the room's predicted drift,
  found by inverting the learned rate against approach (`k_sensible` bins).
* **Proportional.** The room is asked to close its error over
  `ARRIVAL_HOURS`; the learned curve turns that rate into an approach.
* **Derivative, on the measurement.** The error is led by `LEAD_HOURS` of the
  room's own rate of change, so the setpoint eases off before the room reaches
  target and it arrives in band without overshoot.
* **Integral, per direction.** One trim for cooling and one for heating. A
  trim learned while cooling is never applied to heating.

Where a learned coefficient has not converged the loop falls back to the
solved target plus the trim, exactly as it did before, and says so.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import ActuatorStep

#: How far the outer loop may move the commanded setpoint away from the solved
#: target, in either direction. Beyond this the fault is not calibration — the
#: unit is undersized, the sensor is in the wrong place, or a door is open —
#: and winding further would hide it.
MAX_TRIM_C = 3.0

#: Integral gain, in degrees of trim per degree of error per hour. Deliberately
#: slow: a full degree of correction takes roughly three hours of steady
#: one-degree error. The loop it is correcting has an hour-scale time constant,
#: so anything faster overshoots and hunts.
INTEGRAL_GAIN_PER_HOUR = 0.35

#: Error below which nothing is integrated. Room sensors quantise at 0.1 and
#: drift more than that with air movement alone; integrating inside this band
#: chases noise and leaves the setpoint permanently wandering.
DEADBAND_C = 0.3

#: The longest interval that may be integrated in one step. A coordinator that
#: was blocked, or a Home Assistant that was restarted, must not deliver an
#: hour of accumulated error in a single update.
MAX_INTEGRATION_HOURS = 0.25

#: The time over which the room is asked to close its error to target. The
#: unit is asked for the rate that closes the error in this long, plus
#: whatever cancels the room's drift. Half an hour: slow enough that the last
#: degree is approached gently, fast enough that a degree of error is not
#: tolerated for long. A judgement, not a measurement.
ARRIVAL_HOURS = 0.5

#: How far ahead the room's own rate of change is projected when judging how
#: much error is really left. Six minutes: the derivative term eases the
#: setpoint off by this much of the room's current rate.
LEAD_HOURS = 0.1

#: Time constant of the filter on the room's rate of change, and of its decay
#: while the sensor reports no change. A sensor that reports every few minutes
#: gives a stepped signal; this keeps one step from reading as a rate.
RATE_FILTER_HOURS = 0.1

#: A room sensor reading must move at least this much to count as a new
#: reading. Below it the value is quantisation, not movement.
RATE_MIN_CHANGE_C = 0.05

#: The largest room rate of change believed. Beyond this the sensor jumped.
RATE_LIMIT_C_PER_HOUR = 10.0

#: The setpoint step assumed where a unit advertises none: tenths of a degree,
#: which is how the setpoint has always been rounded.
DEFAULT_STEP_C = 0.1

#: Minimum time the compressor stays on once started, and off once stopped.
#: Short cycling is the single most damaging thing a controller can do to a
#: split system: every start draws locked-rotor current and floods the
#: compressor with liquid refrigerant, and neither is metered anywhere the
#: user will see it.
MIN_RUN = timedelta(minutes=10)
MIN_OFF = timedelta(minutes=5)


@dataclass(slots=True)
class RegulatorState:
    """One room's outer-loop state. Held by the coordinator, not persisted.

    Not persisted deliberately. The trim is only valid for the conditions that
    produced it, and restoring a six-hour-old trim after a restart would apply
    yesterday evening's correction to this morning's room.

    **Per room, and it stays per room.** The trim corrects for where that
    room's sensor sits relative to its head's return air, which is a property
    of the room. Cycling state is not: that belongs to the compressor, and
    from 0.8.8 lives in `CompressorState`.

    **One trim per direction (0.9.0, DR-054).** `trim_c` is the cooling trim
    and `heat_trim_c` the heating one. The offset between the unit's sensor
    and the room's is not the same quantity in both directions, and a cooling
    trim carried into heating commanded a heat setpoint below the room's own
    target.
    """

    #: Degrees added to the solved target to produce the commanded setpoint
    #: while cooling. Negative means the unit is being asked for colder air
    #: than the room target, which is the normal direction when cooling.
    trim_c: float = 0.0
    #: The same, while heating.
    heat_trim_c: float = 0.0
    #: When the trim was last integrated, so the interval is measured rather
    #: than assumed to be the evaluation period.
    updated_at: datetime | None = None
    #: Reasons produced by the last update, for the trace.
    notes: list[str] = field(default_factory=list)
    #: The last distinct room reading, when it was seen, and the room's
    #: filtered rate of change in degrees per hour. The derivative term works
    #: on the measurement, never on the error.
    room_c: float | None = None
    room_at: datetime | None = None
    room_rate_c_per_hour: float = 0.0

    def trim_for(self, direction: str) -> float:
        """The trim that applies to a direction of travel."""
        return self.heat_trim_c if direction == "heat" else self.trim_c

    def set_trim(self, direction: str, value: float) -> None:
        """Set the trim for one direction."""
        if direction == "heat":
            self.heat_trim_c = value
        else:
            self.trim_c = value


@dataclass(slots=True)
class CompressorState:
    """One outdoor unit's cycling state.

    Keyed by outdoor unit, not by room. `MIN_RUN` and `MIN_OFF` protect a
    compressor, and two rooms with a head each on one outdoor unit share one.
    Keyed by room, as it was before 0.8.8, starting the second room's head
    while the first was already running was refused as a compressor start, and
    stopping one while the other still called was held as a compressor stop.
    Both directions wrong, on hardware that exists.

    A head with no declared outdoor unit group is its own compressor, so a
    house that declares nothing behaves exactly as it did.
    """

    #: Whether the compressor is currently commanded on, and since when. Both
    #: are needed: the guard has to know which minimum applies.
    running: bool = False
    changed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Regulation:
    """The outcome of one regulation step."""

    #: What to command the unit, or None when there is nothing to command.
    commanded_c: float | None
    #: The trim that produced it, for the trace.
    trim_c: float
    #: Whether the compressor may change state this cycle.
    transition_permitted: bool
    reason: str


def integrate(
    state: RegulatorState,
    *,
    target_c: float | None,
    room_c: float | None,
    now: datetime,
    regulating: bool,
    direction: str = "cool",
    deadband_c: float = DEADBAND_C,
) -> None:
    """Fold this interval's error into the trim for `direction`.

    `regulating` is the anti-windup gate and it is the whole reason this is
    not a textbook PI loop. The trim is only meaningful while the compressor
    is actually working toward the target. Integrating while the room is off,
    coasting, or held by an open window would wind the trim to its limit
    against an error no actuator was addressing, and the first thing the room
    did on coming back would be a three-degree overshoot.

    `deadband_c` is the error below which nothing is integrated. It is the
    larger of `DEADBAND_C` and half the unit's setpoint step: a unit that can
    only be set in whole degrees cannot resolve an error smaller than half a
    degree, and integrating one would flip the setpoint by a degree each time.
    """
    state.notes.clear()
    previous = state.updated_at
    state.updated_at = now

    if not regulating or target_c is None or room_c is None:
        # Not an error condition. There is simply nothing to learn this cycle.
        return

    if previous is None:
        # First cycle since the loop started. There is no interval to
        # integrate over yet, only an anchor for the next one.
        return

    elapsed = (now - previous).total_seconds() / 3600.0
    if elapsed <= 0:
        return
    elapsed = min(elapsed, MAX_INTEGRATION_HOURS)

    error = room_c - target_c
    if abs(error) < deadband_c:
        state.notes.append(f"regulation: within {deadband_c:.1f} C, trim held")
        return

    current = state.trim_for(direction)
    step = -INTEGRAL_GAIN_PER_HOUR * error * elapsed
    proposed = current + step

    if abs(proposed) > MAX_TRIM_C:
        clamped = MAX_TRIM_C if proposed > 0 else -MAX_TRIM_C
        if abs(clamped - current) < 1e-9:
            # Already at the stop and the error pushes further into it. Stop
            # integrating rather than accumulating a number that can only be
            # unwound by an equally long error in the other direction.
            state.notes.append(
                f"regulation: trim at its {MAX_TRIM_C:.0f} C limit with "
                f"{error:+.1f} C still uncorrected — the unit is not keeping up"
            )
            return
        state.set_trim(direction, clamped)
    else:
        state.set_trim(direction, proposed)

    state.notes.append(
        f"regulation: room {error:+.1f} C from target, trim now "
        f"{state.trim_for(direction):+.2f} C"
    )


def permit_transition(
    state: CompressorState, *, want_running: bool, now: datetime
) -> tuple[bool, str | None]:
    """Whether the compressor may start or stop this cycle.

    Returns the verdict and, when refused, the reason for the trace. A refusal
    is not a failure: it is the guard doing its job, and it must be visible or
    the room will appear to ignore its own decision.
    """
    if want_running == state.running:
        return True, None

    if state.changed_at is None:
        return True, None

    held = now - state.changed_at
    minimum = MIN_RUN if state.running else MIN_OFF
    if held >= minimum:
        return True, None

    remaining = (minimum - held).total_seconds() / 60.0
    verb = "stopping" if state.running else "starting"
    return False, (
        f"short-cycle guard: {verb} refused, "
        f"{remaining:.0f} min of the {minimum.seconds // 60} min minimum left"
    )


def note_transition(state: CompressorState, *, running: bool, now: datetime) -> None:
    """Record that the compressor actually changed state."""
    if running != state.running:
        state.running = running
        state.changed_at = now


def commanded_setpoint(
    state: RegulatorState, target_c: float | None, direction: str = "cool"
) -> float | None:
    """The setpoint to send: the solved target plus the accumulated trim.

    This is the fallback form, used until the room has learned enough for
    `pid_setpoint`. The trim is the one for `direction`.
    """
    if target_c is None:
        return None
    return round(target_c + state.trim_for(direction), 1)


# ---- the unit's own limits -------------------------------------------------


@dataclass(frozen=True, slots=True)
class SetpointLimits:
    """What a unit can be set to: its step and its range.

    `step` is None where the unit advertises none, in which case the setpoint
    is rounded to tenths as it always was. `minimum` and `maximum` are None
    where not advertised.
    """

    step: float | None = None
    minimum: float | None = None
    maximum: float | None = None

    @property
    def rounding_step(self) -> float:
        """The step the setpoint is actually rounded to."""
        return self.step if self.step else DEFAULT_STEP_C


def combine_limits(
    limits: Sequence[SetpointLimits],
) -> tuple[SetpointLimits, str | None]:
    """The limits a room with several heads can honour on all of them.

    The coarser step and the narrower range. That is only valid where one step
    is a whole multiple of the other (1.0 and 0.5): a value on the coarser
    step is then also on the finer one. Any other combination is left
    unrounded and the second element says why.
    """
    if not limits:
        return SetpointLimits(), None
    steps = [limit.step for limit in limits if limit.step]
    minimums = [limit.minimum for limit in limits if limit.minimum is not None]
    maximums = [limit.maximum for limit in limits if limit.maximum is not None]
    minimum = max(minimums) if minimums else None
    maximum = min(maximums) if maximums else None
    if not steps:
        return SetpointLimits(None, minimum, maximum), None
    coarse = max(steps)
    for step in steps:
        ratio = coarse / step
        if abs(ratio - round(ratio)) > 1e-6:
            listed = ", ".join(f"{x:g}" for x in sorted(set(steps)))
            return (
                SetpointLimits(None, minimum, maximum),
                (
                    f"setpoint not rounded: this room's heads have steps of "
                    f"{listed} C, and one is not a whole multiple of the other"
                ),
            )
    return SetpointLimits(coarse, minimum, maximum), None


def quantise_setpoint(
    value: float,
    limits: SetpointLimits,
    *,
    toward: str | None = None,
) -> float:
    """Round a setpoint to what the unit can hold.

    Nearest multiple of the step, counted from the unit's minimum where it has
    one. With `toward` set to \"up\" or \"down\" the rounding goes that way
    instead, which is how a binding power ceiling is never carried past by up
    to half a step. The result is then held inside the unit's range.
    """
    step = limits.rounding_step
    anchor = limits.minimum if limits.minimum is not None else 0.0
    position = (value - anchor) / step
    if toward == "up":
        count = math.ceil(position - 1e-9)
    elif toward == "down":
        count = math.floor(position + 1e-9)
    else:
        count = math.floor(position + 0.5 + 1e-9)
    result = anchor + count * step
    if limits.minimum is not None:
        result = max(result, limits.minimum)
    if limits.maximum is not None:
        result = min(result, limits.maximum)
    return round(result, 1)


def effective_deadband(limits: SetpointLimits) -> float:
    """The integral deadband for a unit: half its step, never less than 0.3 C."""
    if not limits.step:
        return DEADBAND_C
    return max(DEADBAND_C, limits.step / 2.0)


# ---- the PID ---------------------------------------------------------------


def track_room_rate(state: RegulatorState, room_c: float | None, now: datetime) -> None:
    """Keep the room's filtered rate of change, from distinct readings only.

    A sensor that reports every few minutes is a staircase. The rate is only
    updated when the reading actually moves; between readings it decays, so a
    stale rate does not keep easing the setpoint off after the room has stopped
    moving.
    """
    if room_c is None:
        return
    if state.room_c is None or state.room_at is None:
        state.room_c, state.room_at = room_c, now
        return
    elapsed = (now - state.room_at).total_seconds() / 3600.0
    if elapsed <= 0:
        return
    if abs(room_c - state.room_c) >= RATE_MIN_CHANGE_C:
        raw = (room_c - state.room_c) / elapsed
        raw = max(-RATE_LIMIT_C_PER_HOUR, min(RATE_LIMIT_C_PER_HOUR, raw))
        alpha = 1.0 - math.exp(-elapsed / RATE_FILTER_HOURS)
        state.room_rate_c_per_hour += alpha * (raw - state.room_rate_c_per_hour)
        state.room_c, state.room_at = room_c, now
    else:
        # No new reading. Decay by the time since the last cycle, not since
        # the last reading, so the decay is the same however often this runs.
        state.room_rate_c_per_hour *= math.exp(
            -min(elapsed, 0.01) / RATE_FILTER_HOURS
        )


@dataclass(frozen=True, slots=True)
class PidSetpoint:
    """The PID's answer for one cycle, and the numbers behind it."""

    setpoint_c: float
    #: How far from the room's own reading the unit is being asked to work.
    approach_c: float
    #: The approach that just holds the room against its drift.
    feed_forward_c: float
    #: The rate the room is being asked to move at, degrees per hour toward
    #: target, and the rate the unit is asked to supply to achieve it.
    wanted_rate_c_per_hour: float
    unit_rate_c_per_hour: float
    reason: str


def pid_setpoint(
    state: RegulatorState,
    *,
    target_c: float,
    room_c: float,
    direction: str,
    drift_c_per_hour: float | None,
    approach_for_rate: Callable[[float], float | None],
) -> PidSetpoint | None:
    """The commanded setpoint from feed-forward, P, D and the per-direction I.

    `approach_for_rate(rate)` is the learned curve, inverted: the approach at
    which the unit moves the room at `rate` degrees per hour, or None where
    the room has not learned enough to say.

    Returns None where the loop cannot be built from what is known - no
    drift estimate, or no usable learned curve - and the caller falls back to
    the solved target plus the trim. An unconverged coefficient is not a zero.
    """
    if direction not in ("cool", "heat"):
        return None
    if drift_c_per_hour is None:
        return None

    error = room_c - target_c  # positive: the room is warmer than target
    led_error = error + LEAD_HOURS * state.room_rate_c_per_hour
    wanted = -led_error / ARRIVAL_HOURS  # degrees per hour, toward target

    # The unit has to supply what the room is asked to do, less what the room
    # does by itself. For cooling that is a negative rate; magnitude and sign
    # are handled here once so the learned curve only ever sees a magnitude.
    if direction == "cool":
        needed = drift_c_per_hour - wanted
    else:
        needed = wanted - drift_c_per_hour

    hold = 0.0
    if drift_c_per_hour != 0.0:
        hold_needed = drift_c_per_hour if direction == "cool" else -drift_c_per_hour
        if hold_needed > 0:
            held = approach_for_rate(hold_needed)
            if held is None:
                return None
            hold = held

    if needed <= 0:
        approach = 0.0
    else:
        found = approach_for_rate(needed)
        if found is None:
            return None
        approach = found

    trim = state.trim_for(direction)
    if direction == "cool":
        setpoint = room_c - approach + trim
    else:
        setpoint = room_c + approach + trim

    return PidSetpoint(
        setpoint_c=round(setpoint, 1),
        approach_c=round(approach, 2),
        feed_forward_c=round(hold, 2),
        wanted_rate_c_per_hour=round(wanted, 2),
        unit_rate_c_per_hour=round(max(needed, 0.0), 2),
        reason=(
            f"regulation: asking {approach:.1f} C {'below' if direction == 'cool' else 'above'} "
            f"the room (holds {hold:.1f}, closes {error:+.1f} C in "
            f"{ARRIVAL_HOURS * 60:.0f} min)"
        ),
    )


def wants_running(
    step: ActuatorStep, *, previous_want: bool | None, running_now: bool
) -> bool:
    """Whether a step asks for the compressor to be running.

    DRY counts: dry mode energises the compressor. NONE is not a stop. It
    means the unit keeps what it was last given, so the room asks for whatever
    it asked for last cycle (or what is running now, if it never asked).
    """
    if step is ActuatorStep.NONE:
        return running_now if previous_want is None else previous_want
    return step in (ActuatorStep.COMPRESSOR, ActuatorStep.DRY)


@dataclass(frozen=True, slots=True)
class CycleVerdict:
    """What the short-cycle guard decided for one room this cycle."""

    #: The reason for the trace, when any outdoor unit refused a transition.
    refusal: str | None
    #: A stop was refused: the compressor is to be held on.
    holding: bool
    #: Each outdoor unit and whether it is to be recorded as running. Units
    #: that refused are absent: nothing changed there.
    record: tuple[tuple[str, bool], ...]


def arbitrate_cycling(
    compressors: Mapping[str, CompressorState],
    *,
    wants: bool,
    neighbours_want: Mapping[str, bool],
    now: datetime,
) -> CycleVerdict:
    """Refuse a compressor transition inside its minimum on or off time.

    DR-002, DR-016. Moved here from the coordinator in 0.8.14 with the logic
    unchanged. `compressors` holds the state of each outdoor unit this room's
    heads are on. `neighbours_want[group]` is whether any other room on that
    unit wants it running: a room reaching its band does not stop a
    compressor its neighbour still calls on.

    """
    refusal: str | None = None
    holding = False
    record: list[tuple[str, bool]] = []
    for group, compressor in compressors.items():
        wanted_by_group = wants or neighbours_want.get(group, False)
        permitted, reason = permit_transition(
            compressor, want_running=wanted_by_group, now=now
        )
        if not permitted and reason is not None:
            refusal = reason
            holding = holding or compressor.running
            continue
        record.append((group, wanted_by_group))
    return CycleVerdict(refusal=refusal, holding=holding, record=tuple(record))

