# DR-028: Rate and draw are learned per operating point, and ship together

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.9 (2026-08-22) |
| Origin | Findings 9, 13 and 14 |
| Related | DR-020, DR-025, DR-029, DR-037 |

## Decision

The compressor's sensible rate is learned in four bins by approach, the gap
between commanded setpoint and room temperature: at setpoint, close, working,
pulldown. The same bins key a learned electrical draw per outdoor unit group,
replacing one assumed 1.2 kW for every unit. An unconverged bin falls back to
the pooled figure.

## Why

One sensible coefficient described only full tilt, while a room spends most
of its life near setpoint where an inverter modulates down (finding 9). Every
projection built on it (preconditioning, the demand forecast, the power
budget, dry vs cool) inherited the error.

They ship together because binning the rate alone makes the energy forecast
worse: `hold_fraction` saturates toward 1.0 as the near-setpoint rate
correctly falls, projecting full rated draw for a room barely running. A
learned draw per bin turns the corrected rate into a corrected forecast.

Draw is learned from the house load step when exactly one group's compressor
changes state and nothing else does (finding 14), keyed by outdoor unit group
(finding 13).

## Rejected

- **Bins without draw.** A more precisely wrong forecast.
- **Accept/reject gates on draw observations.** Every candidate enters the
  filter weighted by how clean it was, so a real shift is tracked and an
  outlier is absorbed.
- **Apportioning simultaneous changes across groups.** Cannot be checked
  against anything; solo observations are enough.
- **A second bin dimension for how many heads are calling.** Deferred.

## Consequences

Draw needs a house-load sensor; without one every consumer sees the assumed
constant. The bin an interval belongs to is chosen once from its mean
approach, not every cycle, to avoid the 0.8.3 anchor fault.

## In the code

Checked against: 0.8.13. **Conforms.**

- `thermal.py:90` - `BIN_NAMES` - the four bins
- `thermal.py:96` - `approach_bin` - binning
- `thermal.py:647` - `DrawModel` - learned draw per group
- `coordinator.py:1793` - `_process_draw_candidates` - house-load steps
- `forecast.py:46` - `ASSUMED_UNIT_KW` - fallback without a house-load sensor
