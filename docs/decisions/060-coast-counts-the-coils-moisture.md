# DR-060: A coast counts the moisture the coil gives back, and the passive model does not learn from it

| | |
|---|---|
| Status | Accepted; amends DR-053 and DR-025 |
| Since | 0.9.3 (2026-10-12) |
| Origin | Living room overnight review, 11/12 October 2026, findings 6, 6a and 12 |
| Related | DR-025, DR-026, DR-053, DR-059 |

## Decision

The weather coast projects the comfort index, not dry bulb, with the room's
humidity rising for `REBOUND_HOURS` after the compressor stops cooling or
drying, at a rate the room learns (`k_rh_rebound`). Until that rate has
converged, a room that is cooling is not coasted on the weather. Intervals
inside the rebound teach the rebound rate, and never the passive loss or solar
gain.

## Why

Every coast prediction that night was wrong. The projection carried dry bulb
only, with the band converted to dry bulb once at the humidity before the stop.
The humidity then rose four to six points in every fan phase and took the
index out of band. The same minutes were learned as passive drift. That is the
likely cause (confidence 0.6) of `k_solar` converging negative, which is
physically impossible.

## Rejected

- **Project outdoor humidity into the room.** In a closed room the water came
  from the coil, not from outside.
- **A fixed rebound.** Coils, drain pans and rooms differ; the rate is learned
  per room, as every other rate is.
- **Treat an unlearned rebound as zero.** That is the defect. Not known is not
  zero.
- **Refuse the in-band hold too until it is learned.** The hold is what makes
  the stops that teach the rebound.

## Consequences

A new room cools a little longer before it coasts on the weather, until it has
seen enough stops. `REBOUND_HOURS` is a judgement constant (fifteen minutes),
listed in known limitations.

## In the code

Checked against: 0.9.5. **Conforms.**

- `thermal.py:113` - `REBOUND_HOURS: Final = 0.25` - the length of the rebound
- `thermal.py:405` - `def _observe_rebound` - the rebound rate is learned on its own
- `thermal.py:341` - `if obs.in_rebound:` - and not as passive drift
- `thermal.py:414` - `def rebound_rh_rise` - None until converged
- `coordinator.py:1779` - `def _track_stop` - when the compressor stopped cooling or drying
- `coordinator.py:2769` - `def humidity_at` - humidity projected through the rebound
- `coordinator.py:2820` - `comfort_index(` - the projection is judged on the index
- `modes.py:727` - `if inputs.unaided_refused is not None and demand == "cool":` - no weather coast on an unlearned rebound
