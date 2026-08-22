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

WHAT IT DOES NOT DO
-------------------
No derivative term. The room sensor is noisy and the loop is slow; a derivative
term on a thirty-second sample of a thermal mass with an hour time constant is
an amplifier for sensor noise and nothing else.

No proportional term on the setpoint either. The unit's own thermostat *is* the
proportional loop. Adding a second one produces two controllers fighting over
the same actuator, which is the failure this project refuses everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

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
    """

    #: Degrees added to the solved target to produce the commanded setpoint.
    #: Negative means the unit is being asked for colder air than the room
    #: target, which is the normal direction when cooling.
    trim_c: float = 0.0
    #: When the trim was last integrated, so the interval is measured rather
    #: than assumed to be the evaluation period.
    updated_at: datetime | None = None
    #: Reasons produced by the last update, for the trace.
    notes: list[str] = field(default_factory=list)


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
) -> None:
    """Fold this interval's error into the trim.

    `regulating` is the anti-windup gate and it is the whole reason this is
    not a textbook PI loop. The trim is only meaningful while the compressor
    is actually working toward the target. Integrating while the room is off,
    coasting, or held by an open window would wind the trim to its limit
    against an error no actuator was addressing, and the first thing the room
    did on coming back would be a three-degree overshoot.
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
    if abs(error) < DEADBAND_C:
        state.notes.append(f"regulation: within {DEADBAND_C:.1f} C, trim held")
        return

    step = -INTEGRAL_GAIN_PER_HOUR * error * elapsed
    proposed = state.trim_c + step

    if abs(proposed) > MAX_TRIM_C:
        clamped = MAX_TRIM_C if proposed > 0 else -MAX_TRIM_C
        if abs(clamped - state.trim_c) < 1e-9:
            # Already at the stop and the error pushes further into it. Stop
            # integrating rather than accumulating a number that can only be
            # unwound by an equally long error in the other direction.
            state.notes.append(
                f"regulation: trim at its {MAX_TRIM_C:.0f} C limit with "
                f"{error:+.1f} C still uncorrected — the unit is not keeping up"
            )
            return
        state.trim_c = clamped
    else:
        state.trim_c = proposed

    state.notes.append(
        f"regulation: room {error:+.1f} C from target, trim now "
        f"{state.trim_c:+.2f} C"
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
    state: RegulatorState, target_c: float | None
) -> float | None:
    """The setpoint to send: the solved target plus the accumulated trim."""
    if target_c is None:
        return None
    return round(target_c + state.trim_c, 1)
