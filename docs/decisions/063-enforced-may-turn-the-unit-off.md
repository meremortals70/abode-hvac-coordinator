# DR-063: Enforced power management may turn the unit off; guidance never does

| | |
|---|---|
| Status | Accepted; amends DR-037 and DR-041; amended by DR-067 in 0.9.4 |
| Since | 0.9.3 (2026-10-12) |
| Origin | Instruction, 2026-10-12: "it should be based on if the aircon is set to strictly follow the power or use it as guidance" |
| Related | DR-037, DR-041, DR-044, DR-057, DR-059 |

## Decision

Where even holding the room at its own temperature would exceed the budget, a
room set to enforced is turned off until the budget allows it, and the trace
says so. A room set to guidance is never turned off or throttled for power; the
ceiling is reported only. A coasting room the budget can afford is left alone.
Precool is not rationed.

## Why

DR-037 met a no-import window with a ceiling that floored at the room's own
temperature and never stopped the unit. A unit held there still draws, so an
enforced room still imported. The user's instruction ties stopping to the
room's setting.

## Rejected

- **Never stop (DR-037 unchanged).** Enforced would not mean enforced.
- **Stop guidance rooms too.** Guidance is advice by definition.
- **Stop a coasting room whatever the budget.** A fan or dry coast the budget
  covers costs nothing worth stopping.

## Consequences

An enforced room can get warm in a long no-import window with little battery.
DR-044 still holds: missing power data never stops a unit.

## In the code

Checked against: 0.9.7. **Conforms**, as amended by DR-067.

- `coordinator.py:3819` - `enforced = room.power_management == POWER_MANAGEMENT_ENFORCED` - the setting decides
- `coordinator.py:3874` - `"off until the budget allows it"` - enforced turns the unit off
- `coordinator.py:3877` - `trace.power_ceiling_c = 0.0` - guidance reports only
- `coordinator.py:3889` - `"room while it coasts"` - a coasting room the budget affords is left alone
