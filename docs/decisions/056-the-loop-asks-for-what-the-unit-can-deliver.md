# DR-056: The room loop asks for what the unit can deliver, and walks its setpoint back

| | |
|---|---|
| Status | Accepted; amends DR-054 |
| Since | 0.9.1 (2026-10-04) |
| Origin | Incident, Office, 2026-10-04, from the Home Assistant recorder |
| Related | DR-017, DR-051, DR-054 |

## Decision

The room loop never asks for more approach than the smallest approach at which
the learned rate stops rising. Beyond that point the curve is flat, so a larger
approach buys no more cooling. And while a unit is running against a setpoint in
cool, heat, heat-cool or auto, and the room is inside its band, the loop's
setpoint is still sent to it whenever it differs from the unit's own by at least
a step and at least half a degree. Only the setpoint is sent, never a mode, and
never to a unit that is off, on its fan or in dry mode, or while an opening is
held inside its grace.

## Why

The Office learned a nearly flat curve: 1.22 C per hour at the smallest approach,
1.00, 1.73, and a pulldown bin with 13 samples that falls back to the pooled
1.01. Any wanted rate above 1.73 read as "ask for the most", which in 0.9.0 was 4 C
below the room. The loop asked for it for errors as small as 0.3 C. The unit
showed a setpoint of 19.0 C in every recorded row from 12:15 to 13:34.

The in-band step is "none", which sent nothing, so the setpoint that a pull-down
had put on the unit was never walked back. The unit kept working flat out after
the room had arrived, until the room overshot, which fed the cycle in DR-055. The
tests that shipped with 0.9.0 simulated a steep, even curve of 2 C per hour per
degree, which the Office does not have.

## Rejected

- **Leave the setpoint where a pull-down put it.** It is the fault.
- **Send a setpoint every cycle.** Re-sending a value that has landed is the
  incident DR-051 fixed. Only a difference of a step is sent.
- **Cap the approach at a fixed number.** The point where the learned curve flattens
  differs per room and per unit, and the room has already learned it.
- **Ask for the maximum and rely on the trim to correct it.** The trim moves
  0.35 C per hour per degree of error, and the unit would overshoot long before.

## Consequences

A unit that cannot deliver the rate the room asked for is asked for the effort
that gets its best rate, not a lower setpoint than that. The integral, the
derivative and the feed-forward are unchanged. A room with a unit whose learned
curve is still unconverged falls back to the target plus the trim, as before.

## In the code

Checked against: 0.9.1. **Conforms.**

- `thermal.py:456` - `smallest approach at which the unit does the most it does` - the cap
- `actuator.py:411` - `async def _async_hold_setpoint` - walks the setpoint onto a running unit that is in band
- `modes.py:452` - `trace.hold_setpoint = True` - set only for the within-band decision
- `models.py:371` - `hold_setpoint` - the flag on the trace
