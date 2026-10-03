# DR-054: The room loop is a PID that uses what the room has learned

| | |
|---|---|
| Status | Accepted; supersedes DR-015 |
| Since | 0.9.0 (2026-10-03) |
| Origin | Instruction, 2026-10-03: the component learns the room, so it should know when the room will arrive in band and hold it there gently |
| Related | DR-003, DR-015, DR-051, DR-053 |

## Decision

The outer loop regulates the room temperature with a PID and commands the unit's
setpoint relative to the room's own reading. The unit's thermostat stays the fast
inner loop. The output is built from four terms:

- **Feed-forward from the thermal model.** The approach that just cancels the
  room's predicted drift, found by inverting the learned rate for each operating
  point (`k_sensible` bins). This is what holds the room at target without effort.
- **Proportional.** The room is asked to close its error over half an hour; the
  learned curve turns that rate into an approach.
- **Integral.** Kept per direction: one trim for cooling and one for heating.
  Anti-windup as before.
- **Derivative, on the measurement.** The error is led by six minutes of the room's
  own filtered rate of change, so the setpoint eases off before the room reaches
  target and it arrives in band without overshoot.

Where a learned coefficient has not converged, the loop uses the solved target plus
the trim for the direction, never the raw seed. The result is held to the power
ceiling where that is enforced, then rounded and limited as DR-051 says.

## Why

`regulate.py` was integral-only, with a header refusing a proportional term on the
grounds that two controllers would fight over one actuator. That reasoning does not
apply to a cascade, where the unit's thermostat is far faster than the room. The
loop had no way to ease off near target and no sense of when the room would arrive.
The learned approach and drift coefficients were used to estimate time and energy
and never to set the command. The trim was one number for both directions. At
11:08:38 on 2026-10-03 the Office had a target of 23.2 C, a heat demand and a
commanded setpoint of 22.8 C, because a trim of -0.4 C learned while cooling was
applied to heating.

## Rejected

- **Keep integral-only.** No anticipation, and a trim shared across directions.
- **Leave arrival to the unit's thermostat.** It regulates its own return-air
  sensor and does not know the room's time to arrive.
- **Full model-predictive control.** It needs converged coefficients across all the
  terms. It is worth revisiting once they have converged.

## Consequences

Gains come from learned coefficients, so a room behaves as it did before 0.9.0
until they converge. The room's reading drives the derivative term; a sensor that
reports every five or six minutes, as the Office presence sensor does, gives a
stepped signal, so the rate is only updated when the reading moves and decays
between readings. The loop is tested against a simulated first-order room with the
unit as the inner loop. The loop and DR-053 share the drift model.

## In the code

Checked against: 0.9.0. **Conforms.**

- `regulate.py:469` - `def pid_setpoint` - feed-forward, proportional and derivative terms
- `regulate.py:423` - `def track_room_rate` - the room's filtered rate of change, from distinct readings
- `regulate.py:137` - `heat_trim_c` - the heating trim, separate from the cooling one
- `regulate.py:44` - `ARRIVAL_HOURS` - half an hour
- `thermal.py:448` - `def approach_for_rate` - the learned rate against approach, inverted
- `coordinator.py:1213` - `def _room_loop_setpoint` - the loop, or the target plus trim where nothing has converged
- `coordinator.py:1084` - `def _regulate` - per-direction trim, ceiling, then rounding
