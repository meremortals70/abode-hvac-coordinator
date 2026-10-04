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

Checked against: 0.9.1. **Conforms.** Three defects found at 0.8.12 were fixed
in 0.8.13: import was overcounted when evaluations ran more often than every
30 s; the window was judged per 30-minute tariff interval instead of as a
whole; and the issue was never cleared. The issue text now describes the
three power management settings.

- `coordinator.py:2553` - `_breach_window_start is None` - the window opens once and runs until the constraint ends
- `coordinator.py:2563` - `EVALUATION_INTERVAL` - each sample weighted by measured time, capped at one period
- `coordinator.py:2550` - `_close_breach_window` - 0.05 kWh floor
- `coordinator.py:2620` - `ir.async_create_issue` - raised for a window over the floor
- `coordinator.py:2631` - `elif window_was_open` - cleared when a later window closes clean
- `strings.json:370` - `power_shortfall` - the issue text
