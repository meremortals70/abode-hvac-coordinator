# DR-031: The learned model is persisted; the regulation trim is not

| | |
|---|---|
| Status | Accepted |
| Since | In the code from 0.6.0 (2026-08-16); non-finite guard after 0.8.11 |
| Origin | Design v0.8; architecture review 2026-08-23 |
| Related | DR-015, DR-025 |

## Decision

Learned coefficients for each room and each outdoor unit group are saved to
Home Assistant storage, written at most every five minutes, loaded at setup
and removed with the room. The regulation trim is held in memory only. A
stored coefficient that is not a finite number is discarded and learned
again.

## Why

Losing weeks of learning on every restart would put the house back on
hysteresis. The trim is different: it corrects for conditions at the time,
and restoring last evening's trim would apply it to this morning's room.

A stored "nan" or "inf" parses through `float()` silently and would poison
every prediction built on it.

## Rejected

- **Persist the trim.** Stale correction applied to a different situation.

## Consequences

After a restart the trim starts from zero and re-converges.

## In the code

Checked against: 0.9.1. **Conforms.**

- `store.py:51` - `ModelStore` - storage
- `store.py:43` - `SAVE_DELAY_SECONDS` - 300
- `coordinator.py:2039` - `_persist_models` - written every cycle, delayed
- `regulate.py:124` - `RegulatorState` - not persisted, by design
- `thermal.py:860` - `is_finite` - non-finite values rejected
