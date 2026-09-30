# DR-036: Refuse the compressor when the battery cannot cover a no-import window

| | |
|---|---|
| Status | Superseded by DR-037 |
| Since | 0.8.5 (2026-08-20); retired in 0.8.10 (2026-08-23) |
| Origin | 0.8.5 |
| Related | DR-037, DR-044 |

## Decision

During a `no_grid_import` window, a room whose projected energy need exceeded
what the battery could spare had its compressor refused.

## Why

It was the only lever available: with one sensible coefficient and one
assumed draw, there was no operating point at which "gentler" had a rate or a
cost.

## Rejected

Nothing recorded.

## Consequences

Three defects followed. It failed closed on every unknown, including an
unconverged model, so turning it on stopped occupied rooms for a whole
evening (finding 3, fixed 0.8.6). It counted a running unit's draw twice
(finding 6, fixed 0.8.6). And until 0.8.7 its refusal never reached the
hardware (finding 20).

## In the code

Checked against: 0.8.12 (`50e6abf`). **Partly retired.** The refusal branches are still in `select_actuator`,
guarded by a field nothing sets, so they can never run.

- `models.py:289` - `power_available` - defaults True, never set
- `modes.py:473-478` - `power_available` - dead branch, heating
- `modes.py:515-520` - `power_available` - dead branch, cooling
