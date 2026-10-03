# DR-025: A learned per-room thermal model, with hysteresis until it converges

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4; in the code from 0.6.0 (2026-08-16); compressor state from `hvac_action` and the solar-term fix in 0.8.6 |
| Origin | Design v0.4, after RoomMind; findings 2 and 4 |
| Related | DR-011, DR-026, DR-028, DR-031, DR-032 |

## Decision

Each room learns its own coefficients from observation with a scalar Kalman
filter. A coefficient is trusted only after 20 samples and a settled
variance. Until then, anything that needs the model answers "cannot say", and
the room is simply held in its band. Whether the compressor was running is
read from `hvac_action`, and from the mode only where the entity publishes no
`hvac_action`.

## Why

The system has to work on day one and improve, not demand a training period.

Two defects shaped the details. Reading the mode instead of `hvac_action`
counted every idle interval at setpoint as compressor running, diluting every
sensible observation (finding 2, 0.8.6). And a sunlit room with an unconverged
solar term got a drift estimate from heat loss alone, so it coasted with the
sun full on the glass (finding 4, 0.8.6). An unconverged term is not a zero
term.

## Rejected

- **A fixed model with configured coefficients.** Surfaces model coefficients
  as settings (DR-045).
- **A training period before acting.** The system would do nothing useful for
  weeks.

## Consequences

Every consumer of the model (coast, precool, preconditioning, dry vs cool,
the demand forecast, the power budget) has a "model cannot say" branch, and
each fails toward comfort. A learning anchor replaced every 30-second cycle
against a 60-second minimum once meant the model never gained a sample (0.8.3);
any change to the learning cadence has to be checked against that.

## In the code

Checked against: 0.9.0. **Conforms.**

- `thermal.py:192` - `ThermalModel` - per room
- `thermal.py:54` - `MIN_SAMPLES` - 20
- `thermal.py:420-421` - `k_solar.converged` - unconverged solar returns None
- `coordinator.py:1821` - `_compressor_direction` - `hvac_action` first
