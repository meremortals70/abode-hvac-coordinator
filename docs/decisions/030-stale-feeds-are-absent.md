# DR-030: A reading that is too old is treated as absent

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.8; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.8 |
| Related | DR-018, DR-044 |

## Decision

Every reading carries an age. Past a tolerance set per kind of feed, it is
treated exactly as a missing reading, and the room lists it as stale.

## Why

A battery sensor that drops off the mesh keeps its last value in Home
Assistant indefinitely, and nothing reports a fault. A controller that trusts it acts on a room as it was hours ago.

## Rejected

- **Trust the last value.** The failure above.
- **One tolerance for everything.** A contact sensor that reports only on
  change is legitimately quiet for a day; a power reading is not.
- **A tolerance per entity, as a setting.** A tolerance is a property of what
  the feed measures, and it asks the user a question they cannot answer.

## Consequences

Tolerances are constants: indoor 2 h, outdoor 3 h, presence 6 h, contacts
26 h, power 15 min.

## In the code

Checked against: 0.8.13. **Conforms.**

- `staleness.py:39-58` - `INDOOR_TOLERANCE` - the tolerances
- `staleness.py:70` - `assess` - the test
- `coordinator.py:2532` - `_fresh` - applied to every read
