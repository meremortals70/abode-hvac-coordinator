# DR-095: The unit's own sensor is measured against the room's and allowed for

| | |
|---|---|
| Status | Accepted; amends DR-054 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Root-and-branch review, 2026-10-15, finding F6. Instruction, 2026-10-15: "go ahead and then review again" |
| Related | DR-015, DR-054, DR-074 |

## Decision

While a unit's fan is moving room air, the difference between its own reading and the room's is measured, filtered over half an hour, and added to the setpoint the fallback loop sends. A difference over 4 C is not believed. The learned curve already includes it.

## Why

The unit holds its own reading at its setpoint. A unit reading 3 C low therefore holds the room 3 C warm. The trim found that alone, and in a simulation it took over two hours, with the room above its Sleep band for 143 minutes. The unit reports its own reading every minute.

## Rejected

- **Faster trim.** A faster integral oscillates with whole-degree setpoints; the difference can simply be measured.

## Consequences

A unit whose sensor reads differently from the room is right within the first evaluations. The trim corrects what is left.

## In the code

Checked against: 0.9.7. **Conforms.**

- `regulate.py:389` - `def track_sensor_offset` - measured and filtered
- `regulate.py:153` - `MAX_SENSOR_OFFSET_C = 4.0` - not believed beyond this
- `regulate.py:385` - `offset = state.sensor_offset_c or 0.0` - added to the fallback setpoint
- `coordinator.py:1528` - `        track_sensor_offset(` - only while the unit's fan runs
