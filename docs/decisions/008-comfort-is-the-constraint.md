# DR-008: Comfort is the constraint, not the variable

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4 |
| Origin | Design v0.4 |
| Related | DR-009, DR-018, DR-035, DR-041, DR-044 |

## Decision

Cost never narrows a room's band. Price decides when energy is banked ahead of
need and which actuator delivers comfort. It never decides whether the room
gets it. The only exception is one the occupant chooses per room (DR-041).

## Why

A controller that saves money by quietly letting rooms get uncomfortable will
be switched off. The occupant has to be able to trust that the band holds.

## Rejected

- **Trade comfort against price at runtime.** Makes the band a suggestion.
- **Wider bands in expensive periods.** The same trade, hidden in
  configuration.

## Consequences

Every guard that cannot compute its answer fails toward comfort: a missing
reading, an unconverged model, a missing price (DR-018, DR-035, DR-044).

## In the code

Checked against: 0.8.13. **Conforms.**

- `modes.py:421-425` - `within band` - inside the band nothing is changed
- `coordinator.py:2369-2372` - `POWER_MANAGEMENT_OFF` - the default leaves comfort untouched
