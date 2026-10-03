# DR-029: Dry versus cool is decided by projecting both routes

| | |
|---|---|
| Status | Accepted |
| Since | Learned rates from design v0.8; projection in 0.8.9 (2026-08-22); bin-aware in 0.8.10 |
| Origin | Design v0.8; finding 17 |
| Related | DR-026, DR-027, DR-028 |

## Decision

Both routes are projected forward one hour and the comfort index is evaluated
at each end. Cooling uses the room's learned sensible rate at its current
operating point and its measured humidity response while cooling
(`k_rh_cooling`); drying uses the learned latent rate. Cooling is chosen only
if it closes the gap at least 1.25 times faster: a tie goes to drying.

## Why

The first version multiplied the sensible rate by the index's derivative at
constant relative humidity. Sensible cooling does not hold RH constant; as dry
bulb falls, RH rises. The derivative credited that to cooling, overstating it
by up to 64% in hot, humid conditions, so drying was under-chosen in exactly
the climate this exists for (finding 17).

## Rejected

- **A humidity threshold (DR-027).** Ignores temperature.
- **A derivative.** Wrong for sensible cooling, and a claim about the formula
  that has to be re-derived by hand whenever the formula changes.

## Consequences

Until `k_rh_cooling` converges, a constant-vapour-pressure floor stands in
for it. Until the rates converge at all, the threshold (DR-027) decides.

## In the code

Checked against: 0.9.0. **Conforms.**

- `modes.py:228` - `_latent_route` - both routes projected
- `modes.py:203` - `DRY_MODE_ADVANTAGE` - 1.25
- `thermal.py:309` - `_observe_rh_cooling` - measured humidity response
- `thermal.py:465` - `sensible_rate_at` - the rate for this operating point
- `hci.py:190` - `relative_humidity_at_constant_vapour_pressure` - the floor
