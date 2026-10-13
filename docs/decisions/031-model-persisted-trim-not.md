# DR-031: The learned model is persisted; the regulation trim is not

| | |
|---|---|
| Status | Accepted; amended by DR-064 in 0.9.3; amended by DR-068 in 0.9.4 |
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

Checked against: 0.9.4. **Conforms**, as amended by DR-064 and DR-068.

- `store.py:62` - `ModelStore` - storage
- `store.py:54` - `SAVE_DELAY_SECONDS` - 300
- `coordinator.py:2384` - `_persist_models` - handed to the store every cycle; one delayed save pending at a time (DR-064)
- `regulate.py:124` - `RegulatorState` - not persisted, by design
- `thermal.py:917` - `is_finite` - non-finite values rejected
