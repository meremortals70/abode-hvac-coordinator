# DR-043: A no-import window's actual grid import is measured and reported afterwards

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.10 (2026-08-23) |
| Origin | Finding 16 |
| Related | DR-038, DR-041 |

## Decision

With a grid sensor configured, grid import during a `no_grid_import` window
is integrated across the window. When the window closes having imported more
than 0.05 kWh, a repair issue names the kWh. This runs whatever each room's
power management setting is. Live grid flow is used for this measurement and
never to size the ceiling: the constraint is met by projection beforehand,
not by reaction to measured import.

## Why

A projection nobody can check is not evidence. The measured figure says
whether the answer is the reserve margin, the battery size or the tariff rule.

## Rejected

- **Size the ceiling from live grid flow.** Reactive, not what was intended.
- **Three shortfall nights in fourteen (finding 16's proposal).** As built, a
  single window over 0.05 kWh raises the issue. The reason for the change is
  not recorded.

## Consequences

Without a grid sensor, no breach can be measured.

## In the code

Checked against: 0.8.12 (`50e6abf`). **Does not conform.** Three defects:

- `coordinator.py:2205` - `EVALUATION_INTERVAL` - each evaluation adds a fixed 30 s, but evaluations also run on every watched state change, so import is overcounted
- `coordinator.py:2253` - `ir.async_create_issue` - the issue is created and never deleted
- `strings.json:325` - `power_shortfall` - text still describes the setting as on or off
- `coordinator.py:2244` - `_close_breach_window` - 0.05 kWh floor
