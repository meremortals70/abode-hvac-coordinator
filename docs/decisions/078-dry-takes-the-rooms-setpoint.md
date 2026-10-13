# DR-078: Dry mode is sent with the room's setpoint and held to it

| | |
|---|---|
| Status | Accepted; amends DR-024 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Operational review, 13–14 October 2026. Instruction, 2026-10-14: "i want you to roll everything into 0.9.6" |
| Related | DR-024, DR-026, DR-056, DR-059 |

## Decision

A dry step that is bringing a room down is sent the same setpoint a cooling step would be, and a drying unit inside its band has the room loop's setpoint walked onto it. A coasting room's dry step keeps the unit's own setpoint.

## Why

Dry was sent without a setpoint, so the unit dried toward whatever it had last been given, 18 C or 26 C, and nothing held it once the room was in band.

## Rejected

- **The solved target, not the loop's setpoint.** The loop's setpoint is what accounts for the unit's sensor sitting away from the room's (DR-054).

## Consequences

Units that ignore the setpoint in dry mode are unaffected. A coasting dry step is not a pull-down and is left alone.

## In the code

Checked against: 0.9.6. **Conforms.**

- `actuator.py:408` - `room, trace, DRY_MODES, temperature, active=False` - the setpoint sent
- `actuator.py:444` - `HVACMode.DRY,` - and walked in band
