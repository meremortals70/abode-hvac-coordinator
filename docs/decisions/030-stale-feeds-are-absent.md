# DR-030: A reading that is too old is treated as absent

| | |
|---|---|
| Status | Accepted; amended by DR-066 in 0.9.3 and DR-070 in 0.9.6 |
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

Checked against: 0.9.7. **Conforms**, as amended by DR-066.

- `staleness.py:55-76` - `INDOOR_TOLERANCE` - the tolerances (indoor none since DR-070)
- `staleness.py:88` - `assess` - the test
- `coordinator.py:3958` - `_fresh` - applied to every read
