# DR-026: Sensible and latent load are learned separately

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.4; the addition to RoomMind's approach |
| Related | DR-010, DR-025, DR-029 |

## Decision

The model learns heat loss, solar gain and the compressor's sensible rate, and
separately a latent (humidity) rate.

## Why

Models built for heating climates learn only sensible terms. In a humid
subtropical climate one coefficient is wrong on exactly the days the two
diverge. Rain is that case: dry bulb falls while humidity climbs, so sensible
load drops as latent load rises, and the compressor may still need to run on
a day that feels cool.

## Rejected

- **One lumped coefficient.** Wrong whenever sensible and latent diverge.

## Consequences

Dry mode can be chosen on the room's own measured behaviour (DR-029).

## In the code

Checked against: 0.8.12 (`50e6abf`). **Conforms.**

- `thermal.py:275` - `_observe_sensible` - sensible
- `thermal.py:318` - `_observe_latent` - latent
