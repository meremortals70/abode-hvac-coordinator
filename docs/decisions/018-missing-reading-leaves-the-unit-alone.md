# DR-018: A missing reading leaves the unit alone

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.7 (2026-08-22) |
| Origin | Finding 20, on finding 3's reasoning |
| Related | DR-008, DR-017, DR-030, DR-044 |

## Decision

When a room has no comfort reading or no band, the step is `NONE`, not
`OFF`. The unit keeps running as last commanded, and the trace names the
entity that is not reporting.

## Why

Comfort is a hard constraint, and a reading the controller cannot take is not
grounds for withdrawing it. Stopping here would be a guard failing closed
against comfort, which is the fault finding 3 was spent reversing in the
power path.

## Rejected

- **Stop the unit.** Fails closed against comfort.

## Consequences

A dead sensor does not turn the room off. It also means the room runs on its
last command without supervision until the sensor returns, so the trace line
naming the entity is how the fault gets noticed.

## In the code

Checked against: 0.9.1. **Conforms.**

- `modes.py:394-419` - `ActuatorStep.NONE` - missing reading or band, named in the trace
