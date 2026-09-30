# DR-027: Choose dry mode on a relative humidity threshold

| | |
|---|---|
| Status | Superseded by DR-029 |
| Since | Design v0.5 |
| Origin | Design v0.5 |
| Related | DR-029 |

## Decision

Dry mode was chosen when relative humidity was at or above one threshold.

## Why

It was the simplest test available before the model could measure the room.

## Rejected

Nothing recorded.

## Consequences

It is a poor test: 65% at 22 C and 65% at 30 C are different loads, because a
point of humidity moves the comfort index by 0.087 at the first and 0.140 at
the second.

## In the code

Checked against: 0.8.12 (`50e6abf`). **Superseded, kept as a fallback.** The threshold is consulted only while
the model has not converged; once it has, DR-029 decides.

- `modes.py:185` - `DRY_MODE_RH_FALLBACK` - 65%
- `modes.py:258` - `DRY_MODE_RH_FALLBACK` - used only as the unconverged fallback
