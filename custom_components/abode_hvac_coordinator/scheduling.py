"""Deadline scheduling and the sleep ramp.

Pure. No Home Assistant imports.

TWO THINGS, BOTH ABOUT TIME
---------------------------
**Preconditioning has a deadline and the model knows how long it takes.**
Before this, a heading-home request started the compressor immediately, which
is right when you are twenty minutes away and wrong when you are four hours
away — four hours of cooling an empty house to be ready at a moment the model
could have hit from a standing start in forty minutes. The model already
answers "how long from here to there"; this uses that answer to decide when to
begin, so a deadline four hours out costs nothing until it is forty minutes
out.

**The sleep band moves, so it should not step.** Occupied and sleep bands
differ by three degrees on a seeded install. Stepping between them the instant
a schedule flips asks the compressor for the whole gap at once, which is both
the loudest it will be all day and the moment the room is trying to fall
asleep. Ramping the band across the transition spreads the same change over an
hour of gentle work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .hci import ComfortBand

#: Added to the model's estimate before deciding to start. The estimate is an
#: estimate: it comes from a converged filter, but the day it is asked about
#: may not resemble the days it learned from. Arriving early is a minor cost;
#: arriving late defeats the whole feature.
DEADLINE_MARGIN = timedelta(minutes=15)

#: Started unconditionally when the deadline is inside this, whatever the model
#: says. A deadline this close leaves no room to be wrong about, and a model
#: that has not converged has no opinion to defer to.
DEADLINE_ALWAYS_START = timedelta(minutes=30)

#: How long the band takes to move between the occupied and sleep bands. One
#: hour: long enough that the compressor works gently, short enough that the
#: room is on the sleep band well before anyone is actually asleep.
SLEEP_RAMP = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class PreconditionPlan:
    """Whether to start now, and the reasoning either way."""

    start_now: bool
    #: The model's estimate of how long the pull takes, where it has one.
    hours_needed: float | None
    reason: str


def plan_precondition(
    *,
    now: datetime,
    deadline: datetime | None,
    hours_needed: float | None,
) -> PreconditionPlan:
    """Decide whether a heading-home request should be acting yet.

    With no deadline, start now. A request without a time on it means "as soon
    as you can", and deferring it would be inventing a deadline the person did
    not give.
    """
    if deadline is None:
        return PreconditionPlan(
            start_now=True,
            hours_needed=hours_needed,
            reason="precondition: no deadline given, starting now",
        )

    remaining = deadline - now
    if remaining <= DEADLINE_ALWAYS_START:
        minutes = remaining.total_seconds() / 60.0
        return PreconditionPlan(
            start_now=True,
            hours_needed=hours_needed,
            reason=(
                f"precondition: deadline is {minutes:.0f} min away, starting "
                "regardless of the estimate"
            ),
        )

    if hours_needed is None:
        # The model cannot say how long the pull takes. Starting is the safe
        # error: an early start wastes energy, a late one misses the deadline
        # the request existed to meet.
        return PreconditionPlan(
            start_now=True,
            hours_needed=None,
            reason=(
                "precondition: the model cannot estimate the pull yet, starting "
                "now rather than risk missing the deadline"
            ),
        )

    needed = timedelta(hours=hours_needed) + DEADLINE_MARGIN
    if remaining <= needed:
        return PreconditionPlan(
            start_now=True,
            hours_needed=hours_needed,
            reason=(
                f"precondition: {hours_needed * 60:.0f} min of pull needed plus "
                f"margin, deadline {remaining.total_seconds() / 60:.0f} min away "
                "— starting"
            ),
        )

    idle = (remaining - needed).total_seconds() / 60.0
    return PreconditionPlan(
        start_now=False,
        hours_needed=hours_needed,
        reason=(
            f"precondition: {hours_needed * 60:.0f} min of pull needed, deadline "
            f"{remaining.total_seconds() / 60:.0f} min away — waiting {idle:.0f} "
            "min before starting"
        ),
    )


def ramped_band(
    *,
    from_band: ComfortBand | None,
    to_band: ComfortBand | None,
    changed_at: datetime | None,
    now: datetime,
) -> tuple[ComfortBand | None, str | None]:
    """Interpolate between two bands across the ramp window.

    Returns the destination band unchanged once the ramp is over, or when
    either band is missing, so a room configured with only one of the two
    behaves exactly as it did before.
    """
    if to_band is None or from_band is None or changed_at is None:
        return to_band, None

    elapsed = now - changed_at
    if elapsed >= SLEEP_RAMP:
        return to_band, None
    if elapsed.total_seconds() < 0:
        return to_band, None

    fraction = elapsed / SLEEP_RAMP
    band = ComfortBand(
        low=from_band.low + (to_band.low - from_band.low) * fraction,
        high=from_band.high + (to_band.high - from_band.high) * fraction,
    )
    remaining = (SLEEP_RAMP - elapsed).total_seconds() / 60.0
    return band, (
        f"band ramping {band.low:.1f}-{band.high:.1f}, "
        f"{remaining:.0f} min to go"
    )
