# DR-098: A room the unit is working on coasts only once it reaches its target

| | |
|---|---|
| Status | Accepted; amends DR-032 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Root-and-branch review, 2026-10-15, finding F1. Instruction, 2026-10-15: "go ahead and then review again" |
| Related | DR-032, DR-053, DR-057, DR-097 |

## Decision

A room whose unit is cooling, drying or heating may coast only once its comfort index is within 0.25 of its target, the middle of its band. Until then the unit keeps working.

## Why

A room coasted as soon as it was anywhere inside its band and projected to stay. The compressor stopped at the band's edge, the room was back above it within a quarter of an hour, and the unit restarted at its coldest setpoint: 7 to 8 starts a night in simulation, and 11 in a nine-hour Office day.

## Rejected

- **A longer minimum run.** It stops the compressor later, still at the edge, still restarting hard.

## Consequences

With all of 0.9.7, simulated starts fell from 29 to 7 over three nights and from 11 to 3 in an Office day. A coast that ends early, because the room warms faster than the model expected, still restarts the unit.

## In the code

Checked against: 0.9.7. **Conforms.**

- `modes.py:283` - `COAST_AT_TARGET_HCI = 0.25` - the margin
- `modes.py:1004` - `f"coast: not yet, the unit keeps working until the room reaches "` - a working unit keeps working
