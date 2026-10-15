# DR-074: Where a room's sensor is unavailable, the unit's own reading stands in

| | |
|---|---|
| Status | Accepted; amends DR-018 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Living room, 13 October 2026 (20:41–21:05 nothing sent). Instruction, 2026-10-14: "i want you to roll everything into 0.9.6" |
| Related | DR-018, DR-046, DR-070 |

## Decision

Where a room's temperature or humidity sensor is unavailable, the air conditioner's own `current_temperature` or `current_humidity` is used in its place, the trace says so, and nothing is learned from it.

## Why

With no room reading the decision is to leave the unit as it is (DR-018), so a unit that was off stayed off for as long as the sensor was down. On 13 October the room was set to Sleep and nothing was sent for 24 minutes, while the unit itself reported 26.0 C against the room's 25.8 C.

## Rejected

- **Leave the unit as it is, as before.** That is the failure.
- **Learn from the unit's reading.** Its sensor sits in the unit, not the room; the Office's reads about 3 C low.

## Consequences

A room whose unit reads low against the room is cooled less than it needs while its sensor is down. A unit that reports no humidity cannot stand in for a missing humidity sensor, and the room is still left as it is.

## In the code

Checked against: 0.9.7. **Conforms.**

- `coordinator.py:3012` - `unit_c = self._unit_reading(room, "current_temperature")` - temperature
- `coordinator.py:3021` - `unit_rh = self._unit_reading(room, "current_humidity")` - humidity
- `coordinator.py:1944` - `temperature = None if inputs.temperature_from_unit else inputs.temperature_c` - not learned from
