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

Checked against: 0.9.1. **Superseded; no remnant.** The dead refusal
branches and the `power_available` field were removed in 0.8.13.

- `modes.py:501` - `compressor: heating` - heating reaches the compressor with no power check
- `modes.py:537` - `compressor: cooling` - cooling likewise
