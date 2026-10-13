# DR-066: A reading's age is measured from when it was last reported

| | |
|---|---|
| Status | Accepted; amends DR-030; amended by DR-070 in 0.9.6 |
| Since | 0.9.3 (2026-10-12) |
| Origin | Living room overnight review, 11/12 October 2026, finding 10 |
| Related | DR-018, DR-030 |

## Decision

A reading's age is measured from the later of `last_updated` and
`last_reported`.

## Why

`last_updated` does not move while a sensor keeps reporting the same value, so a
sensor reporting an unchanged reading every minute was judged stale and its
reading treated as absent. `State.last_reported` moves on every report.

## Rejected

- **A longer tolerance.** It would hide sensors that really have gone silent.
- **`last_reported` alone.** Taking the later of the two keeps the result right
  where an integration sets one and not the other.

## Consequences

A sensor that has stopped reporting is still caught: neither time moves.

## In the code

Checked against: 0.9.6. **Conforms**, as amended by DR-070.

- `coordinator.py:3761` - `max(state.last_updated, state.last_reported), dt_util.utcnow(), tolerance` - the age
