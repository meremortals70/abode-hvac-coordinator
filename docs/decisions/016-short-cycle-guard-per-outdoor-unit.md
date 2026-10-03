# DR-016: Short-cycle protection is 10 minutes on, 5 off, per outdoor unit

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.8, in the code from 0.6.0 (2026-08-16); dry counted as running in 0.8.6; keyed by outdoor unit in 0.8.8 (2026-08-22) |
| Origin | Design v0.8; findings 5 and 13 |
| Related | DR-015, DR-017, DR-020 |

## Decision

A compressor, once started, runs at least 10 minutes; once stopped, stays off
at least 5. Dry mode counts as running. The guard is keyed by outdoor unit,
not by room, and a compressor's demand is the OR across the rooms on it. When
the guard refuses a stop, it holds the compressor and leaves the rest of the
decision (covers, fan) alone.

## Why

Short cycling is the most damaging thing a controller can do to a split
system, and nothing else in the stack prevents it: the unit's own protection
guards against its own thermostat, not against a coordinator switching
`hvac_mode` from outside.

Three defects shaped the details:

- Treating dry as a stop blocked cool-to-dry changes for ten minutes, then
  recorded a stop that never happened (finding 5, 0.8.6).
- A refused stop used to replace the whole step with COMPRESSOR, cancelling
  unrelated cover and fan decisions (finding 5, 0.8.6).
- Keyed by room, two rooms on one outdoor unit refused and held each other's
  transitions in both directions (finding 13, 0.8.8).

## Rejected

- **Keyed by room.** Wrong unit of account for shared outdoor units.
- **Sequencing or staggering starts between rooms.** That is arbitration of
  comfort between rooms, settled as not applicable.

## Consequences

The guard only limits the transitions the coordinator itself commands. A unit
switching its own compressor on and off at the setpoint it was given is
outside it.

## In the code

Checked against: 0.9.0. **Conforms.**

- `regulate.py:119-120` - `MIN_RUN` - 10 and 5 minutes
- `regulate.py:134` - `CompressorState` - keyed by outdoor unit
- `regulate.py:553` - `ActuatorStep.DRY` - dry counts as running (`wants_running`)
- `regulate.py:589` - `wanted_by_group` - OR across the rooms on a compressor (`arbitrate_cycling`)
- `coordinator.py:1161-1167` - `hold_compressor` - a refused stop holds, decision kept
