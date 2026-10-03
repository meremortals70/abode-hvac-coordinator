# DR-052: Every mode or setpoint command the component sends is recorded

| | |
|---|---|
| Status | Accepted |
| Since | 0.9.0 (2026-10-03) |
| Origin | Incident, Office Aircon, 2026-10-03 |
| Related | DR-006, DR-051 |

## Decision

Each mode or setpoint command the actuator sends is written to the Home Assistant
log at INFO level, with the room, the climate entity, the mode, the setpoint sent,
the room's demand, the room temperature and the target. The last 200 such commands
are also kept in memory and included in the diagnostics download. A command made
by hand from a room's controls is recorded too, marked as the user's. Only
commands actually sent are recorded, not evaluations.

## Why

On 2026-10-03 the unit was set to Heat at 11:08:38. The component keeps only the
latest decision per room (DR-006), overwritten each cycle, and had no record of
what it sent. Who sent the Heat could only be established because Home Assistant's
recorder happened to hold the mode sensor's attributes. A Heat at 11:17:03 was not
matched to any command in that trace, and nothing in the component could say
whether it had sent one.

## Rejected

- **Rely on the recorder.** It holds the decision, not the command, it is purged,
  and an exclusion on the sensor removes it.
- **Log every evaluation.** Evaluations run about every 10 seconds per room and
  almost all send nothing.
- **Persist the list in the store.** It adds persistence for a diagnostic. The
  Home Assistant log already persists.

## Consequences

A change to the unit with no matching line in the log was not sent by this
component. The log shows what was sent, not who else sent something. The list is
lost on restart; the Home Assistant log is not.

## In the code

Checked against: 0.9.0. **Conforms.**

- `actuator.py:305` - `def _record_command` - the log line and the bounded list
- `actuator.py:128` - `COMMAND_LOG_SIZE` - 200
- `diagnostics.py:26` - `command_log` - the list in the diagnostics download
