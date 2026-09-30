# DR-005: One writer per actuator; never write the battery

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4 |
| Origin | Design v0.4 |
| Related | DR-001, DR-037 |

## Decision

This integration writes climate entities and covers, and nothing else. It
never writes to the battery. For whoever does own the battery, it publishes a
vendor-neutral demand forecast: projected energy over a horizon, plus the
constraint windows in force.

## Why

Two writers fail silently: if this sets a battery reserve and another
automation overwrites it minutes later, nothing errors and the battery just
behaves oddly. Battery control is also vendor-specific, so coding one in ties
the project to one manufacturer.

## Rejected

- **Control the battery to support the air conditioning.** Rejected for both
  reasons above.

## Consequences

Power management can only shape how the air conditioning runs (DR-037). It
cannot move energy.

## In the code

Checked against: 0.8.12 (`50e6abf`). **Conforms.** The only write services called are climate, cover and TTS.

- `actuator.py:322` - `SERVICE_SET_HVAC_MODE` - climate writes
- `actuator.py:439` - `_async_move_covers` - cover writes
- `forecast.py:204` - `build_forecast` - the published demand forecast
