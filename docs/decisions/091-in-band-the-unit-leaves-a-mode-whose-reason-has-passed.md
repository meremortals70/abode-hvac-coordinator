# DR-091: Inside its band, a unit leaves a mode whose reason has passed

| | |
|---|---|
| Status | Accepted; amends DR-056 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Root-and-branch review, 2026-10-15, finding F4. Instruction, 2026-10-15: "go ahead and then review again" |
| Related | DR-056, DR-085, DR-090 |

## Decision

Inside its band, a unit heating a room at or above the middle of the band goes to its fan. A unit drying when the room no longer calls for it goes back to cooling at the room's setpoint. A waking room below its band is not left cooling: its unit goes to its fan.

## Why

Inside the band the unit kept whatever mode it had. In a simulated morning the living room was heated for two hours; on a humid evening the unit dried for five hours at 51%.

## Rejected

- **Turn the unit off inside the band.** A cooling unit holding the band is doing its job.

## Consequences

A heated room is let drift down from the middle of its band before it is heated again.

## In the code

Checked against: 0.9.7. **Conforms.**

- `modes.py:710` - `def _in_band_step` - what a room inside its band asks
- `modes.py:725` - `if inputs.unit_mode == "heat" and hci >= band.midpoint:` - heat ends at the middle
- `modes.py:630` - `"below band on its way out of Sleep"` - a waking room is not cooled
