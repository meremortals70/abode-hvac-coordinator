# DR-071: A room speaks only while the coordinator runs it and the room is awake

| | |
|---|---|
| Status | Accepted; amends DR-012 and DR-069; amended by DR-077 in 0.9.6 |
| Since | 0.9.6 (2026-10-13) |
| Origin | Incident, Office, 13 October 2026: warnings given with Automatic control off. Instruction, 2026-10-13: "No announcements in sleep mode and no fucking announcements when the fucking component isn't actually in autonomy mode" |
| Related | DR-012, DR-048, DR-049, DR-058, DR-069 |

## Decision

No warning is spoken for a room whose Automatic control is off, a room still
waiting for its air conditioner to report what it can do, or a room that is
asleep, by its Comfort mode or by its sleep schedule. Presence and free cooling
are still tracked for such a room, so its warnings resume once it is run and
awake again.

## Why

The empty-room warnings and the free-cooling advice were queued before the
Automatic control check, so a room the coordinator was not running still said
"Turning the Office air conditioning off", which was not true: nothing was sent
to it. A sleeping room was spoken to at night.

## Rejected

- **Stop tracking presence while Automatic control is off.** Turning control
  back on would then start from a stale picture of whether the room is empty.
- **Silence only the empty-room warnings.** Free-cooling advice spoken into a
  sleeping room or a room nobody handed over is the same fault.
- **Silence Guidance rooms.** Guidance is still the coordinator running the
  room; only the power budget is advisory.

## Consequences

A room asleep by its schedule is never warned that it has emptied before it
turns off. The warning is dropped, not deferred: switching Automatic control
back on does not replay what was missed.

## In the code

Checked against: 0.9.7. **Conforms**, as amended by DR-077.

- `coordinator.py:2489` - `if room.announce_target_entity_ids and not self._silenced(room)` - every warning passes through the check
- `coordinator.py:2567` - `def _silenced` - switched off, waiting for its unit, or asleep
