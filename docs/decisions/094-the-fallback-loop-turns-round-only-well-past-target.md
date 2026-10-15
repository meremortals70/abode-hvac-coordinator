# DR-094: The fallback loop changes direction only half a degree past its target

| | |
|---|---|
| Status | Accepted; amends DR-075 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Root-and-branch review, 2026-10-15, finding F5. Instruction, 2026-10-15: "go ahead and then review again" |
| Related | DR-015, DR-054, DR-075 |

## Decision

Before a room has learned enough for the model-based loop, its loop changes between cooling and heating only once the room is half a degree past its target.

## Why

DR-075 gave this hysteresis to the learned loop and not the fallback. The fallback switched between the cooling trim and the heating trim each time the room crossed its target: 19 and 21 C forty times in one simulated night, 22 and 24 C every five minutes on a hot afternoon.

## Rejected

- **Use the rate margin of DR-075.** The fallback has no learned rate to judge by.

## Consequences

One setpoint for a room hovering at its target.

## In the code

Checked against: 0.9.7. **Conforms.**

- `regulate.py:143` - `FALLBACK_DIRECTION_MARGIN_C = 0.5` - the margin
- `regulate.py:420` - `def loop_direction` - the hysteresis
- `coordinator.py:1521` - `direction = loop_direction(` - used by the room loop
