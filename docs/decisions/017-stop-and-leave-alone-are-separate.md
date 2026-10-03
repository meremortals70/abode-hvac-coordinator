# DR-017: Stopping the unit and leaving it alone are separate decisions

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.7 (2026-08-22) |
| Origin | Finding 20 |
| Related | DR-016, DR-018, DR-019 |

## Decision

The actuator step has two distinct values. `OFF` commands the climate entity
off: lockout, unoccupied, an opening, coasting, a deferred precondition, and a
direction the unit cannot deliver. `NONE` commands nothing, so the unit keeps
the setpoint it was last given: a room inside its band, and a room with a
missing reading or band.

## Why

They were one value, and two places downstream guessed. The actuator guessed
"stop" from the mode, which caught lockout and unoccupied and missed the
other five: an open window, a coasting room and a `no_grid_import` refusal
all left the compressor running with nothing in the log. The guard guessed
"not running", so it recorded a stop every time a room reached its band. Each
defect hid the other; neither could be fixed alone.

## Rejected

- **Infer the stop from the mode.** The defect above.

## Consequences

Every new stop path must return `OFF` explicitly, and every "leave it"
path `NONE`. The demand forecast follows the same verdict (finding 23).

## In the code

Checked against: 0.9.0. **Conforms.**

- `models.py:40` - `ActuatorStep` - OFF and NONE as distinct values
- `actuator.py:365-368` - `ActuatorStep.OFF` - commands off
- `actuator.py:370-377` - `ActuatorStep.NONE` - sends nothing
- `regulate.py:551-552` - `ActuatorStep.NONE` - NONE keeps the room's last demand
- `coordinator.py:2082-2083` - `will_run` - forecast follows the verdict
