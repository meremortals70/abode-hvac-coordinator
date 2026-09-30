# DR-012: An unoccupied room is off

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4 |
| Origin | Design v0.4, superseding DR-011 |
| Related | DR-011, DR-013, DR-033 |

## Decision

An unoccupied room is off. Not a wider envelope. Only a heading-home request
(DR-013) or a precool window (DR-033) brings it back on.

## Why

The v0.3 wide band spent energy on empty rooms to avoid a restart cost the
thermal model can predict. With the model, the controller knows when to start
so the room is comfortable on arrival.

## Rejected

- **A wide unoccupied band (DR-011).** Energy spent on nobody.

## Consequences

Presence has to be right. A presence grace period stops a room switching off
because someone left for a minute.

## In the code

Checked against: 0.8.13. **Conforms.**

- `modes.py:350-354` - `Mode.UNOCCUPIED` - off
- `coordinator.py:1410` - `_graced_presence` - the grace period
