# DR-013: Preconditioning drives to the occupied band, and starts when the model says

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4 (target), v0.8 (start time); in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.4 and v0.8 |
| Related | DR-009, DR-012, DR-025 |

## Decision

A heading-home request drives the room to its occupied band, with no separate
target. It does not start immediately: it starts when the thermal model says
the pull needs to start to be comfortable by the deadline.

## Why

A separate target is a second comfort definition (DR-009). Starting at once
cools an empty house for hours to reach a moment the model can reach from
cold.

## Rejected

- **An explicit target plus deadline (v0.3).** A second definition of comfort.
- **Start immediately (v0.5).** Wastes the hours before it is needed.

## Consequences

A request with no deadline starts at once. So does one whose model cannot yet
estimate the pull, or whose deadline is 30 minutes away or less: an early
start wastes energy, a late one misses the deadline the request existed for.

## In the code

Checked against: 0.8.14. **Conforms.**

- `scheduling.py:58` - `plan_precondition` - when to start
- `scheduling.py:89` - `hours_needed is None` - unconverged model starts now
- `modes.py:384-392` - `precondition_ready` - waits, unit off
- `modes.py:82-84` - `heading_home` - overrides presence
