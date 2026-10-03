# DR-011: An unoccupied room holds a wide band

| | |
|---|---|
| Status | Superseded by DR-012 |
| Since | Design v0.3 |
| Origin | Design v0.3 |
| Related | DR-012 |

## Decision

An unoccupied room held its own wide band, so it never drifted far from
comfort and came back quickly.

## Why

To avoid the cost and delay of restarting a room from a long way out.

## Rejected

- **Off.** At the time, the restart cost could not be predicted, so off looked
  risky.

## Consequences

It spent energy on empty rooms. Once the thermal model could predict how long
a restart takes (DR-025), the reason for it was gone.

## In the code

Checked against: 0.9.0. **Superseded; no remnant.** Unoccupied has no band.

- `const.py:221` - `DEFAULT_BANDS` - no unoccupied entry
