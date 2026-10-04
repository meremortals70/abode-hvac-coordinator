# DR-014: The change into the sleep band ramps over an hour

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.8; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.8 |
| Related | DR-009 |

## Decision

Moving between the occupied and sleep bands is interpolated over one hour,
not stepped. Sleep is reachable only when a sleep schedule entity is
configured.

## Why

A stepped change asks for a large correction at the moment someone is falling
asleep, which means a loud pulldown. A ramp asks for it gradually.

## Rejected

- **A stepped change (v0.5).** A pulldown at bedtime.
- **An assumed schedule (v0.3).** Replaced by a configured entity, or no sleep
  mode at all.

## Consequences

For the first hour after the change, the band in force is neither band.

## In the code

Checked against: 0.9.1. **Conforms.**

- `scheduling.py:45` - `SLEEP_RAMP` - one hour
- `scheduling.py:126` - `ramped_band` - the interpolation
