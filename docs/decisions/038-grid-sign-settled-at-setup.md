# DR-038: The grid sensor's sign is settled at setup and never inferred again

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.10 (2026-08-23); zero-flow correction in 0.8.11 (2026-08-24) |
| Origin | Finding 11 |
| Related | DR-043 |

## Decision

A grid power reading is optional. If configured, the user confirms at setup
which direction a positive reading means, with a default offered from live
evidence where the evidence is decisive. The stored answer is never inferred
or corrected at runtime. A persistent run of live readings that contradict it
raises a repair issue. A reading within 5 W of zero is no evidence either
way.

## Why

Getting the sign wrong moves the derived battery figure by twice the grid flow
and can report a breach that never happened.

Until 0.8.11 a reading of exactly zero counted as "exporting". A no-import
window on battery overnight reads zero for hours, the most common reading the
feature sees, and every one of them was counted as a contradiction.

## Rejected

- **Infer the sign at runtime.** A wrong inference is silent.
- **Auto-correct on disagreement.** Same failure, later.

## Consequences

Setup at night on battery offers no default, because there is no flow to read.

## In the code

Checked against: 0.9.1. **Conforms.**

- `config_flow.py:890` - `async_step_power_grid_sign` - settled at setup
- `power.py:67` - `implied_sign` - the offered default
- `power.py:54` - `NO_FLOW_BELOW_W` - zero is no evidence
- `coordinator.py:2546` - `_check_grid_sign` - contradiction counted, never corrected
- `coordinator.py:262` - `_GRID_SIGN_DISAGREEMENT_THRESHOLD` - ten in a row
