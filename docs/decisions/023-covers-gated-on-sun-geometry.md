# DR-023: Covers are gated on sun geometry, not light level

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.4 |
| Related | DR-005, DR-022 |

## Decision

Whether covers can help is decided by whether the sun is on the room's
windows, from sun position and one compass direction per room. Illuminance is
not used. This integration commands the covers itself.

## Why

A semi-transparent blind reads bright when fully closed, so a light sensor
says there is nothing to block at exactly the moment the blind is already
blocking.

## Rejected

- **Gate on illuminance (v0.3).** The failure above. Illuminance was later
  removed from inputs altogether.
- **Depend on Adaptive Cover Pro.** Read for its geometry; replaced, because
  two writers on one cover fail silently (DR-005).

## Consequences

One direction per room: a corner room with two glazed walls is approximated.

## In the code

Checked against: 0.8.12 (`50e6abf`). **Conforms.**

- `sun.py:96` - `sun_on_window` - the geometry
- `modes.py:438-441` - `direct_sun` - covers gated on it
