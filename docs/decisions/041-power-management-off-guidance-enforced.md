# DR-041: Power management per room is off, guidance or enforced

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.12 (2026-08-24) |
| Origin | Instruction, 2026-08-24, superseding DR-040 |
| Related | DR-034, DR-037, DR-040 |

## Decision

Each room chooses one of three, on the bands step:

- **Off** (default): power management does not touch the room. Comfort wins
  unconditionally, as if the feature were not configured.
- **Guidance**: the ceiling is computed and shown in the trace and reasons,
  and never applied.
- **Enforced**: the ceiling applies whether the room is inside its band or
  correcting, holding it as close to the band as the energy allows.

No state ever commands the unit off for a power reason.

## Why

The instruction: "add a check box in setup/config flow to allow the user to
make power management guidance or enforced." Guidance lets
a room be watched under the budget before it is throttled by it. Off has to
mean off: the setting is the occupant's choice between comfort and power
management for that room, not a magnitude on top of an always-on baseline.

## Rejected

- **A checkbox (DR-040).** No way to watch without applying, and "off" still
  throttled inside the band.

## Consequences

The `no_grid_import` constraint in DR-034 is now held only in rooms set to
enforced. A room set to off can import during the window, and the shortfall
report (DR-043) is how that shows.

## In the code

Checked against: 0.9.0. **Conforms.** The precool gap found at 0.8.12 was
closed in 0.8.13 (DR-037).

- `config_flow.py:299-311` - `POWER_MANAGEMENT_OFF` - the three states
- `coordinator.py:2705-2708` - `POWER_MANAGEMENT_OFF` - off returns before anything is computed
- `coordinator.py:1106-1112` - `POWER_MANAGEMENT_ENFORCED` - anti-windup only when enforced
- `coordinator.py:1106-1112` - `POWER_MANAGEMENT_ENFORCED` - clamp only when enforced
- `coordinator.py:2784-2787` - `comfort_reduction_active` - true only when enforced and correcting
