# DR-068: A room's occupancy is kept across a restart

| | |
|---|---|
| Status | Accepted; amends DR-031 |
| Since | 0.9.4 (2026-10-12) |
| Origin | Incident, Office, 12 October 2026, 16:04 |
| Related | DR-012, DR-018, DR-031, DR-064 |

## Decision

Whether each room counts as occupied, after its grace, is kept in the store.
After a restart or reload, a room starts from that, so a presence sensor that is
still coming up holds the room as it was.

## Why

At 16:04 the 0.9.3 update reloaded the integration. The Office's presence
sensor read unavailable for a minute. Unknown presence holds the last known
occupancy, but after a reload nothing was known, and every room started
unoccupied: the Office was turned off with someone in it, and its trace said
"presence unknown, holding last known occupancy".

## Rejected

- **Treat unknown at startup as occupied.** A room that was empty would be
  started for no one.
- **Leave the unit alone until presence is known.** Correct for that minute,
  but a sensor that stays down would leave the room unmanaged.

## Consequences

The occupancy is written with the learned state and flushed on unload
(DR-064). After a long outage the kept value may be stale; the first real
reading then starts the usual grace.

## In the code

Checked against: 0.9.5. **Conforms.**

- `store.py:216` - `def occupancy` - read at startup
- `store.py:229` - `def set_occupancy` - written when it changes
- `coordinator.py:1937` - `occupied=self.store.occupancy().get(room.room_id, False)` - the grace starts from it
