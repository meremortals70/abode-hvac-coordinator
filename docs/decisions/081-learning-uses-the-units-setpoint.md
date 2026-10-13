# DR-081: The model learns against the setpoint the unit reports

| | |
|---|---|
| Status | Accepted; amends DR-028 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Review of the component against its intent, 14 October 2026. Instruction, 2026-10-14: "yes" (approving the review fixes) |
| Related | DR-025, DR-028, DR-054 |

## Decision

Each learning interval records the setpoint the room's unit reports (the middle of a range for a unit that holds one), not a figure the coordinator works out. A unit reporting none records none.

## Why

The interval was recorded against the solved target plus the trim, the form the room loop sent before 0.9.0. Since then the loop sends its own setpoint, so every cooling interval was filed under an approach the unit was never given, and the curve the loop uses to choose setpoints was learned from it. A setpoint changed by hand, or by another writer, was also ignored.

## Rejected

- **Record the loop's own commanded setpoint.** Right while the coordinator is the only writer; wrong for a hand change or the unit not taking the command.

## Consequences

What rooms learned before 0.9.6 is kept and corrected only as new intervals come in. Learning now follows the unit whoever sets it.

## In the code

Checked against: 0.9.6. **Conforms.**

- `coordinator.py:2486` - `def _unit_setpoint` - the setpoint the unit reports
- `coordinator.py:1777` - `commanded = self._unit_setpoint(room)` - recorded with the interval
