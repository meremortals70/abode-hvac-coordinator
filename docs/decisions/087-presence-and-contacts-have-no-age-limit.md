# DR-087: Presence and door or window sensors have no age limit

| | |
|---|---|
| Status | Accepted |
| Since | 0.9.7 (2026-10-15) |
| Origin | Review, 2026-10-15. Instruction, 2026-10-15: "yes all changes and give me a script" |
| Related | DR-018, DR-070 |

## Decision

A presence sensor or a door or window contact is believed until it reports unavailable or unknown, however long ago it last changed. Outdoor and power feeds keep their limits.

## Why

These sensors report only on change. A door shut all night or a room occupied all evening is not a stale reading. DR-070 made the same decision for room temperature and humidity.

## Rejected

- **A longer limit.** Any limit is wrong for a door that stays shut.

## Consequences

A sensor that has failed silently without going unavailable is believed. Home Assistant marks most failed sensors unavailable.

## In the code

Checked against: 0.9.7. **Conforms.**

- `staleness.py:68` - `PRESENCE_TOLERANCE: timedelta` - no limit on presence
- `staleness.py:72` - `CONTACT_TOLERANCE: timedelta` - no limit on contacts
