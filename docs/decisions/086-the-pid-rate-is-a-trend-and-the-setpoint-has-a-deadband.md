# DR-086: The room loop's rate is a 15-minute trend, and a setpoint sent stands through three quarters of a step

| | |
|---|---|
| Status | Accepted; amends DR-054 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Review of the living room night, 2026-10-15: 196 setpoint changes overnight. Instruction, 2026-10-15: "where is my pid control?", and "yes all changes and give me a script" |
| Related | DR-051, DR-054, DR-065, DR-075 |

## Decision

The derivative term uses the least-squares slope of the room's readings over the last 15 minutes, with no rate until five minutes are held. A new setpoint is sent only when the loop's value has moved at least three quarters of the unit's setpoint step from the one already sent.

## Why

The Eve Room reports in 0.2 C steps. The rate was filtered from step to step, so each report was a spike and the setpoint moved with it. Rounding to whole degrees then flipped the setpoint each time the loop's value crossed a half degree.

## Rejected

- **Limit how often the setpoint may change.** Rejected by the owner: "where is my pid control?" A limit stops the loop answering; a trend and a deadband keep it answering without the noise.

## Consequences

Fewer setpoint changes for the same control. A real change of a degree still goes through.

## In the code

Checked against: 0.9.7. **Conforms.**

- `regulate.py:104` - `RATE_WINDOW = timedelta(minutes=15)` - the trend window
- `regulate.py:543` - `def track_room_rate` - the least-squares slope
- `regulate.py:113` - `QUANTISE_DEADBAND_STEPS = 0.75` - the deadband
- `coordinator.py:1653` - `deadband = QUANTISE_DEADBAND_STEPS * limits.rounding_step` - the setpoint sent stands
