# DR-075: The room loop changes the way it works only when the rate it needs has clearly changed sign

| | |
|---|---|
| Status | Accepted; amends DR-054; amended by DR-094 in 0.9.7 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Living room, 13 October 2026, 21:35–23:25. Instruction, 2026-10-14: "i want you to roll everything into 0.9.6" |
| Related | DR-054, DR-056, DR-065 |

## Decision

The way the room loop works, cooling or heating, follows the rate the unit must supply (the room's drift against the rate it is asked to move at), and changes only once that rate is 0.5 C per hour the other way. The setpoint is rounded the way the loop is working.

## Why

The way was taken from the room against its target. With the target about 20.8 C and the room 0.1 C either side, the term that holds the room against its drift went from about 1.1 C to nothing and back, and the setpoint sent to the unit swung between 18 and 23 C about forty times in two hours. The unit cooled more than it should and the room fell to 19.4 C, below its band.

## Rejected

- **A deadband on the room against its target.** The discontinuity is in the hold term, not the error; a deadband only moves it.
- **Send the walked setpoint less often.** The swings were whole degrees, past any step check.

## Consequences

`DIRECTION_MARGIN_C_PER_HOUR` is a judgement. A room has to be about a quarter of a degree past its target, with nothing else moving it, before the loop turns round.

## In the code

Checked against: 0.9.7. **Conforms**, as amended by DR-094 in 0.9.7.

- `regulate.py:136` - `DIRECTION_MARGIN_C_PER_HOUR = 0.5` - the margin
- `regulate.py:636` - `previous = state.pid_direction or direction` - the way is kept
- `coordinator.py:1643` - `way = pid.direction if pid is not None else direction` - and the rounding follows it
