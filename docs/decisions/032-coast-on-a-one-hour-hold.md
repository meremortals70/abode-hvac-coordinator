# DR-032: A room coasts when the model says its band holds for an hour

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.4 |
| Related | DR-017, DR-025, DR-035 |

## Decision

A room enters COAST when the thermal model projects that its band holds, with
nothing running, for the next hour, and the current tariff window permits
coasting. COAST commands the unit off and keeps the band of the mode it
displaced. The test is made fresh every cycle.

## Why

Leaving the unit on while the band would hold unaided has the thermostat hold
the band by running, which is the opposite of coasting. One hour is long
enough for coasting to mean something and short enough for the prediction to
be trusted.

## Rejected

- **Coast on a fixed hysteresis gap.** Used only as the fallback until the
  model converges; the model knows how fast this room drifts.
- **Coast by running the fan.** Not recorded as considered.

## Consequences

An unconverged model never coasts. The projection is a straight line from the
steady-state loss rate, so the heat that returns in the first minutes after a
stop is not in it. Short cycling reported from live running on 2026-10-01 is
under investigation against this record.

## In the code

Checked against: 0.8.14. **Conforms** to this record.

- `const.py:26` - `COAST_HORIZON_HOURS` - one hour
- `modes.py:101-103` - `Mode.COAST` - entered when the band holds and the window permits
- `modes.py:377-382` - `Mode.COAST` - coast is off
- `coordinator.py:1292` - `_predicted_to_hold` - the projection
- `thermal.py:383` - `holds_through` - straight-line drift over the horizon
