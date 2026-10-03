# DR-019: An open window or door stops the unit after two minutes

| | |
|---|---|
| Status | Superseded by DR-050 |
| Since | 0.8.7 (2026-08-22) |
| Origin | Finding 20 |
| Related | DR-016, DR-017, DR-050 |

## Decision

When an opening in the room is open, nothing new is actuated at once. If it
stays open for two minutes, the unit is commanded off.

## Why

Before 0.8.7 an open window only refused to *start* the unit; a unit already
running kept running with the window open, unbounded and silent. Stopping at
once has its own cost: a door held open for twenty seconds would cost a
compressor stop plus the minimum off time.

## Rejected

- **Refuse to start only.** The defect above.
- **Stop immediately.** Cycles the compressor for every door.

## Consequences

For two minutes after an opening, the room holds its current state.

## In the code

Checked against: 0.9.0. **Superseded by DR-050.** The fixed two-minute `OPENING_STOP_DEBOUNCE` is gone: the grace is a per-room setting, five minutes by default, and an opening of unknown age now holds the unit instead of stopping it.

- `modes.py:356-375` - `opening_open` - hold, then off
