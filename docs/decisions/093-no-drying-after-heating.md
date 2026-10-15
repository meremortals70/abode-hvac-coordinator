# DR-093: A coast after heating does not dry

| | |
|---|---|
| Status | Accepted; amends DR-059 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Incident, living room, 2026-10-15 05:16-05:21. Root-and-branch review, 2026-10-15, finding F8. Instruction, 2026-10-15: "go ahead and then review again" |
| Related | DR-057, DR-059 |

## Decision

A room coasting after its unit has been heating goes to the fan, whatever its humidity.

## Why

DR-059 dries a humid coasting room so the water on a cold coil is not blown back into it. After heating the coil is warm and dry. On 15 October the living room was coasted from heat into dry.

## Rejected

- **Leave it: drying a humid room is harmless.** Drying is cooling, and the room had just been heated.

## Consequences

None beyond the case.

## In the code

Checked against: 0.9.7. **Conforms.**

- `modes.py:786` - `trace.rejected.append("dry: the unit has been heating, its coil is dry")` - after heat, the fan
