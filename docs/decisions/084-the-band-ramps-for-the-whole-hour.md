# DR-084: A change of band ramps across the whole hour

| | |
|---|---|
| Status | Accepted; amends DR-014 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Review of the living room night, 2026-10-15. Instruction, 2026-10-15: "yes all changes and give me a script" |
| Related | DR-014, DR-058, DR-092 |

## Decision

When a room changes band, the band it changed from is kept for the hour of the ramp, so the band moves gradually for the whole hour. A coast is not a change of band: it keeps the band of the mode it displaced.

## Why

The ramp was given the previous evaluation's mode. After one evaluation the previous mode was the new one, so the ramp lasted thirty seconds and the band then stepped.

## Rejected

- **Pass the previous evaluation's mode, as before.** That is the fault.

## Consequences

A room going to Sleep is brought down over an hour. A coast inside a ramp keeps ramping.

## In the code

Checked against: 0.9.7. **Conforms.**

- `modes.py:946` - `band_mode = base if mode is Mode.COAST and base is not None else mode` - a coast keeps the band it displaced
- `modes.py:947` - `if inputs.previous_mode is not None and inputs.previous_mode is not band_mode:` - ramped from the band changed from
- `coordinator.py:1885` - `self._ramp_from[room_id] = current` - kept for the whole ramp
- `coordinator.py:3084` - `previous_mode=self._ramp_from.get(room.room_id),` - given to every evaluation
