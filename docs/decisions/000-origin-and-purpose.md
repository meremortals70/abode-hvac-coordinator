# DR-000: Origin and purpose

| | |
|---|---|
| Status | Accepted |
| Since | Before the repository (design v0.3) |
| Origin | The project brief |
| Related | Every other record |

## Decision

Build one Home Assistant integration that decides what each room's air
conditioning should be doing, from how the room should feel, and says why.
It takes the best of the prior art named in `ATTRIBUTION.md` and none of the
parts that made it unsuitable.

## Why

The brief was to take the best of RoomMind and Dual Smart Thermostat. Each
solved part of the problem and brought something that could not be kept:

- **Dual Smart Thermostat** showed the cost of configuration: an options list
  that is individually defensible and collectively unusable. It also drives
  toggle entities, not climate entities.
- **RoomMind** showed the thermal model: per-room state estimation with a
  Kalman filter, solar gain from sun position, a convergence test and a
  hysteresis fallback until converged. It was built for heating climates and
  has no latent term.
- **Versatile Thermostat** was the regulator the early design assumed at
  Layer 2 (see DR-003 for why that changed).
- **Adaptive Cover Pro** was read for sun geometry and replaced, not used.

The house is in a humid subtropical climate, with solar, a battery and a
time-of-use tariff. A thermostat holding a temperature is the wrong target
there, and running the compressor first is the wrong method when blinds, a fan
and dry mode cost less.

## How it is proven

Every build runs continuously against a live air conditioner in the office,
used as the test room. That room is where real compressor, blind and fan
behaviour is proven. The test suite proves the logic, not the plant.

## Consequences

Every later record is read against this one. A proposal that adds a setting,
a second controller on one actuator, or a comfort trade the user did not ask
for has to argue against the brief, not just for itself.

## In the code

Checked against: 0.9.1. **Conforms** at the level of the whole integration.

- `manifest.json:2` - `abode_hvac_coordinator` - the domain
