# DR-003: Build the regulation layer here instead of adopting a regulator

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.8; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.8, replacing the v0.5 plan |
| Related | DR-001, DR-015, DR-016 |

## Decision

Layer 2 is `regulate.py`, written for this project. It does two things and no
others: an integral outer loop that brings the room sensor to target, and
compressor short-cycle protection.

## Why

The v0.5 plan was to wrap an existing over-climate regulator (Versatile
Thermostat). What was needed is one narrow thing: close the loop around the
unit's own thermostat so the room sensor, not the unit's return-air sensor,
reaches the target. Adopting a full regulator would have brought presence
handling, interlocks and presets that Layer 3 already owns. That makes two
controllers on one actuator.

## Rejected

- **Adopt Versatile Thermostat as Layer 2.** Rejected for the duplication
  above. Its centralised load shedding was also unsuitable: energy decisions
  depend on tariff state and stored energy, which a regulator cannot see.

## Consequences

The project owns the regulation code and its tuning constants. Interlocks,
presence and presets stay in Layer 3 only.

## In the code

Checked against: 0.8.12 (`50e6abf`). **Conforms** in code. `ATTRIBUTION.md` still describes Versatile
Thermostat as "the regulation layer this design assumes at Layer 2", and its
paths still use the pre-0.8 domain `hvac_coordinator`.

- `regulate.py:137` - `integrate` - the outer loop
- `regulate.py:201` - `permit_transition` - short-cycle protection
