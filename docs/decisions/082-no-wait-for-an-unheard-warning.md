# DR-082: An empty room waits for its warning only where a warning can be said

| | |
|---|---|
| Status | Accepted; amends DR-012 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Review of the component against its intent, 14 October 2026. Instruction, 2026-10-14: "yes" (approving the review fixes) |
| Related | DR-012, DR-071, DR-077 |

## Decision

The extra warning period before an empty room is turned off applies only where a warning can be said: the room has announcements on and speakers, is run and awake, and its unit is on. Otherwise the room is turned off when the vacancy period ends.

## Why

With announcements on, every empty room waited the warning period before it was turned off, including rooms where 0.9.6 now says nothing (DR-071, DR-077): three more minutes of cooling an empty room for a warning nobody heard.

## Rejected

- **Always wait.** It conditions an empty room for nothing.

## Consequences

A room with announcements on but nothing to say is turned off three minutes sooner than before.

## In the code

Checked against: 0.9.7. **Conforms.**

- `coordinator.py:2214` - `and bool(room.announce_target_entity_ids)` - only with speakers
- `coordinator.py:2216` - `and self._unit_mode(room) not in (None, "off")` - and a unit that is on
