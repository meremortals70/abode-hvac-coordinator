# DR-024: Send the unit only what it says it can do

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.4 |
| Related | DR-001, DR-020, DR-022 |

## Decision

Every command is resolved against what the climate entity advertises: its
HVAC modes, whether it takes a single target or a range, and its fan and swing
lists. A decision never picks a mode the unit does not have.

## Why

Unit capabilities were assumed at v0.3. A cooling-only unit, a unit without
dry mode, or one that takes only a temperature range would be sent commands
it rejects or half-applies.

## Rejected

- **Assume a standard feature set.** The v0.3 position.

## Consequences

A unit that advertises less gets fewer steps. A range-only unit is given the
target plus and minus 1.0 C.

## In the code

Checked against: 0.8.13. **Conforms.**

- `actuator.py:110` - `resolve_hvac_mode` - mode resolved against the entity
- `actuator.py:349` - `_async_set_temperature` - single or range
- `actuator.py:72` - `RANGE_DEADBAND_C` - 1.0 C either side
- `coordinator.py:2100` - `_capabilities` - fed into the decision
