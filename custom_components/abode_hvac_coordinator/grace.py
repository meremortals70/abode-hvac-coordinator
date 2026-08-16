"""Occupancy grace.

Pure. No Home Assistant imports.

THE PROBLEM
-----------
Raw presence is the wrong signal for a compressor.

**Arriving:** someone puts a laptop on the desk and leaves again. Starting the
air conditioning for that costs a compressor start and achieves nothing. So
presence has to be *sustained* before the room is treated as occupied.

**Leaving:** someone goes to the front door to sign for a delivery, or to make
a coffee. They are coming back in two minutes. Switching the room off and on
again is worse than leaving it running — for the compressor and for comfort. So
vacancy has to be sustained before the room is treated as empty.

Those two delays are different lengths and want to be, which is why they are
two settings rather than one.

**Warnings.** Before finally shutting a room down after a long absence, the
controller can announce it. Two announcements: one when the vacancy grace
expires, and a second some minutes later immediately before shutting off. That
gives someone still in the house a chance to come back or countermand it.

WHAT THIS MODULE IS
-------------------
The state machine only. It takes the raw presence reading and how long it has
been that way, and answers: is this room occupied for our purposes, and is an
announcement due? Turning an announcement into speech belongs elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

#: Presence must hold this long before the room counts as occupied. Filters out
#: grab-and-go visits that would otherwise cycle the compressor.
DEFAULT_OCCUPIED_AFTER = timedelta(minutes=2)

#: Vacancy must hold this long before the room counts as empty. This is the
#: delivery-at-the-front-door allowance.
DEFAULT_VACANT_AFTER = timedelta(minutes=10)

#: How long after the first warning before the room is actually shut down.
DEFAULT_WARNING_GRACE = timedelta(minutes=3)


class Announcement(StrEnum):
    """What, if anything, should be said this evaluation."""

    NONE = "none"
    #: Vacancy grace has expired and the room is still conditioning.
    FIRST_WARNING = "first_warning"
    #: The warning grace has expired; the room is being shut down now.
    FINAL_WARNING = "final_warning"


@dataclass(frozen=True, slots=True)
class GraceSettings:
    """Per-room timings. Seeded with defaults, changed in the room form."""

    occupied_after: timedelta = DEFAULT_OCCUPIED_AFTER
    vacant_after: timedelta = DEFAULT_VACANT_AFTER
    warning_grace: timedelta = DEFAULT_WARNING_GRACE
    #: Whether to announce before shutting a room down. Off by default: a house
    #: that suddenly starts talking is a surprise, not a feature.
    announce: bool = False

    @classmethod
    def from_minutes(
        cls,
        occupied_after: float | None = None,
        vacant_after: float | None = None,
        warning_grace: float | None = None,
        announce: bool = False,
    ) -> GraceSettings:
        """Build from the minute values the config form collects."""
        return cls(
            occupied_after=timedelta(minutes=occupied_after)
            if occupied_after is not None
            else DEFAULT_OCCUPIED_AFTER,
            vacant_after=timedelta(minutes=vacant_after)
            if vacant_after is not None
            else DEFAULT_VACANT_AFTER,
            warning_grace=timedelta(minutes=warning_grace)
            if warning_grace is not None
            else DEFAULT_WARNING_GRACE,
            announce=announce,
        )


@dataclass(slots=True)
class GraceState:
    """What the grace machine remembers about one room."""

    #: Whether the room currently counts as occupied, after grace.
    occupied: bool = False
    #: When the raw presence reading last changed.
    changed_at: datetime | None = None
    #: The raw reading at that moment.
    raw: bool | None = None
    #: Whether the first warning has already been announced this absence.
    warned_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GraceResult:
    """The outcome of one evaluation."""

    occupied: bool
    announcement: Announcement
    reason: str


def evaluate_grace(
    state: GraceState,
    raw_presence: bool | None,
    now: datetime,
    settings: GraceSettings,
) -> GraceResult:
    """Advance the grace machine one step. Mutates `state`.

    `raw_presence` None means the sensor cannot say. That is not absence: a
    failed sensor must never empty an occupied room, so an unknown reading
    holds whatever the room was last known to be.
    """
    if raw_presence is None:
        return GraceResult(
            occupied=state.occupied,
            announcement=Announcement.NONE,
            reason="presence unknown, holding last known occupancy",
        )

    if raw_presence != state.raw:
        state.raw = raw_presence
        state.changed_at = now
        if raw_presence:
            # Back in the room. Any pending shutdown is abandoned.
            state.warned_at = None

    held_for = now - state.changed_at if state.changed_at else timedelta(0)

    if raw_presence:
        if state.occupied:
            return GraceResult(True, Announcement.NONE, "occupied")
        if held_for >= settings.occupied_after:
            state.occupied = True
            state.warned_at = None
            return GraceResult(
                True,
                Announcement.NONE,
                f"presence sustained {_minutes(settings.occupied_after)}",
            )
        return GraceResult(
            state.occupied,
            Announcement.NONE,
            f"presence detected, waiting {_minutes(settings.occupied_after)} "
            "before starting",
        )

    # Vacant.
    if not state.occupied:
        return GraceResult(False, Announcement.NONE, "unoccupied")

    if held_for < settings.vacant_after:
        return GraceResult(
            True,
            Announcement.NONE,
            f"vacant {_minutes(held_for)} of {_minutes(settings.vacant_after)}, "
            "holding in case they return",
        )

    if settings.announce:
        if state.warned_at is None:
            state.warned_at = now
            return GraceResult(
                True,
                Announcement.FIRST_WARNING,
                f"vacant {_minutes(settings.vacant_after)}, warning before "
                "shutting down",
            )
        if now - state.warned_at < settings.warning_grace:
            return GraceResult(
                True,
                Announcement.NONE,
                "warned, waiting before shutting down",
            )
        state.occupied = False
        state.warned_at = None
        return GraceResult(
            False,
            Announcement.FINAL_WARNING,
            f"vacant {_minutes(held_for)}, shutting down",
        )

    state.occupied = False
    return GraceResult(
        False,
        Announcement.NONE,
        f"vacant {_minutes(settings.vacant_after)}, room is now unoccupied",
    )


def _minutes(span: timedelta) -> str:
    """A span in whole minutes, for the decision trace."""
    total = int(span.total_seconds() // 60)
    return f"{total} min"
