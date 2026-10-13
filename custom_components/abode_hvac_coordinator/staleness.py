"""Feed staleness.

Pure. No Home Assistant imports.

WHAT THIS IS FOR
----------------
Home Assistant distinguishes an entity that is `unavailable` from one that is
reporting. It does not distinguish one that is reporting from one that reported
four hours ago and has said nothing since.

A battery-powered Zigbee sensor that has dropped off the mesh keeps its last
state indefinitely. `hass.states.get` returns 23.4, the coordinator reads 23.4,
the comfort index is computed from 23.4, and the room is regulated against a
number from before lunch. Nothing anywhere reports a fault, because from Home
Assistant's point of view nothing is faulty.

Every reading this controller acts on therefore carries an age, and a reading
older than its feed's tolerance is treated as absent rather than as a value.
Absent is already handled everywhere: the room holds, the trace says why, and
no actuator moves on a guess.

WHERE THE AGE COMES FROM
------------------------
0.9.3, DR-066. The coordinator passes the later of `State.last_updated` and
`State.last_reported`. `last_updated` does not move while a sensor keeps
reporting the same value, so a sensor reporting an unchanged reading every
minute was judged stale on it alone. `last_reported` moves on every report.
A sensor that has stopped reporting is still caught: neither time moves.

TOLERANCES
----------
One number per feed class, not per entity. A tolerance is a property of what
the feed measures and how it reports, not of the particular device, and making
it a setting would mean asking the user a question they have no way to answer.

They are deliberately generous. The purpose is to catch a feed that has died,
not to police reporting intervals.

ROOM TEMPERATURE AND HUMIDITY HAVE NO AGE LIMIT
-----------------------------------------------
0.9.6, DR-070. A room's own temperature and humidity are trusted for as long
as Home Assistant shows them as available. Many room sensors report only when
the reading changes: the living room's Eve Room went 3 h 23 min without a
report on 13 October 2026 because the room did not change, and the two-hour
limit left the room with no reading and its air conditioner idle. A room
sensor that really has dropped out is shown as unavailable by its own
integration, and that is caught as no reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

#: Room temperature and humidity. None: no age limit (DR-070). Many report
#: only on change, and a steady room legitimately says nothing for hours.
INDOOR_TOLERANCE: timedelta | None = None

#: Outdoor temperature. Often a weather integration polling every fifteen or
#: thirty minutes, so the tolerance has to clear a couple of missed polls.
OUTDOOR_TOLERANCE = timedelta(hours=3)

#: Presence. An mmWave sensor in a room nobody has entered legitimately reports
#: nothing for a long time, so this is the loosest of the three. Occupancy
#: grace already absorbs short gaps; this only catches a dead sensor.
PRESENCE_TOLERANCE = timedelta(hours=6)

#: Openings and covers. Contact sensors are quiet by nature — a window nobody
#: opens for a week is normal — but they heartbeat daily.
CONTACT_TOLERANCE = timedelta(hours=26)

#: Battery charge, solar output and house load, for the power-aware
#: compressor decision. These describe *right now*, re-checked every cycle
#: rather than forecast, so the tolerance is tight: a battery reading from
#: half an hour ago is not "the battery", it is a different number.
POWER_TOLERANCE = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class Freshness:
    """Whether a reading may be acted on, and why not when it may not."""

    fresh: bool
    age: timedelta | None
    reason: str | None


def assess(
    changed_at: datetime | None, now: datetime, tolerance: timedelta | None
) -> Freshness:
    """Judge one reading's age against its feed's tolerance.

    A tolerance of None means the feed has no age limit (DR-070): the
    reading is fresh whatever its age, and only an unavailable state, which
    the caller checks, makes it absent.

    A reading with no timestamp is treated as fresh. Home Assistant always
    supplies one for a real entity, so the absence of it means a test fixture
    or a synthetic state, and refusing those would break the harness rather
    than catch a fault.

    A timestamp in the future is also treated as fresh. Clock skew between a
    device and the host is common, produces a negative age, and is not a
    staleness fault — refusing the reading would take a working room offline
    over a few seconds of drift.
    """
    if changed_at is None:
        return Freshness(fresh=True, age=None, reason=None)

    age = now - changed_at
    if tolerance is None or age <= tolerance:
        return Freshness(fresh=True, age=age, reason=None)

    hours = age.total_seconds() / 3600.0
    limit = tolerance.total_seconds() / 3600.0
    return Freshness(
        fresh=False,
        age=age,
        reason=(
            f"last reported {hours:.1f} h ago, beyond the {limit:.0f} h "
            "tolerance for this feed — treated as no reading"
        ),
    )
