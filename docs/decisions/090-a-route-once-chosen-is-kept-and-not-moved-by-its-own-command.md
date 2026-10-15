# DR-090: A route, once chosen, is kept for 20 minutes, and the comparison is not moved by the component's own command

| | |
|---|---|
| Status | Accepted; amends DR-076 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Incident, living room, 2026-10-15 05:15-05:38: 13 mode changes. Root-and-branch review, 2026-10-15, finding F2. Instruction, 2026-10-15: "go ahead and then review again" |
| Related | DR-027, DR-028, DR-072, DR-076 |

## Decision

The cooling rate used to compare drying with cooling is read at the room's approach to its target, not the unit's current setpoint. Once a unit is drying or cooling a room above its band, or drying or on its fan while coasting, it keeps that route for 20 minutes.

## Why

The rate was read at the unit's setpoint against the room, and that setpoint was the one the component had just sent. Each command moved the comparison: the cooling rate read 1.61, then 0.29 ten seconds after heat at 24 C was sent. The living room went dry, cool, dry, cool every 2-4 minutes.

## Rejected

- **Only widen DR-076's hysteresis.** A jump from 1.61 to 0.29 crosses any margin.

## Consequences

A wrong route is kept for up to 20 minutes before it is reconsidered. A unit first seen after a restart carries no hold.

## In the code

Checked against: 0.9.7. **Conforms.**

- `coordinator.py:3316` - `target = self._last_target.get(room.room_id)` - the rate at the room's target
- `modes.py:276` - `ROUTE_HOLD = timedelta(minutes=20)` - the hold
- `modes.py:424` - `def _route_kept` - the route kept
- `coordinator.py:3051` - `unit_mode_minutes=unit_mode_minutes,` - how long the unit has been in its mode
