# DR-057: Coasting is the compressor off and the unit on its fan

| | |
|---|---|
| Status | Accepted; supersedes the "commands the unit off" part of DR-032; amends DR-017 and DR-053 |
| Since | 0.9.1 (2026-10-04) |
| Origin | Instruction, 2026-10-04: "changing the definition of coast to fan only is better and making off actually be off" |
| Related | DR-017, DR-022, DR-032, DR-053, DR-055 |

## Decision

A coasting room stops the compressor and leaves the unit in fan-only mode at its
quietest fan speed and settled vane position, where the unit offers a fan-only
mode. A unit with no fan-only mode coasts off, and the trace says so. Off is kept
for the stops that mean off: lockout, an empty room, an opening held past its
grace, a direction the unit cannot deliver, and a deferred precondition. A
coasting room projects no compressor energy and is not throttled by the power
ceiling.

## Why

DR-032 defined coasting as commanding the unit off, on the reasoning that a unit
left running would hold the band by running. The user's own word for the intent
was "coast or fan". In the Office, coast turned the unit off every 37 minutes and
left it off, with the fan that mixes the room's air off too, and the unit then
started again when the room warmed.

## Rejected

- **Keep coast as off.** It is what the user saw as the unit switching off and on.
- **Leave the unit in its running mode at a higher setpoint.** The compressor would
  keep working, which is the opposite of coasting.
- **A third state between fan and off.** Nothing in a climate entity offers one.
- **Fan-only on every unit regardless.** A unit that does not offer it cannot be
  sent it, and the room would be sent nothing.

## Consequences

Coasting uses a little fan power the old definition did not. The compressor
short-cycle guard is unchanged: a coast that would stop a compressor started less
than ten minutes ago waits for the minimum run. The weather coast of DR-053 and
the one-hour hold of DR-032 both coast this way.

## In the code

Checked against: 0.9.1. **Conforms.**

- `modes.py:541` - `def coast_step` - fan where the unit has fan-only, otherwise off
- `modes.py:409` - `return coast_step(inputs, trace)` - the in-band coast
- `modes.py:724` - `trace.actuator = coast_step(inputs, trace)` - the weather coast
- `coordinator.py:2088` - `and trace.mode is not Mode.COAST,` - a coasting room projects no compressor energy
