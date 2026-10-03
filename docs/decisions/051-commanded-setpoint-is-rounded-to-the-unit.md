# DR-051: The commanded setpoint is rounded to what the unit can hold

| | |
|---|---|
| Status | Accepted |
| Since | 0.9.0 (2026-10-03) |
| Origin | Incident, Office Aircon, 2026-10-03, from the Home Assistant recorder |
| Related | DR-007, DR-015, DR-016, DR-048, DR-052, DR-054 |

## Decision

The last step before a setpoint is sent is to round it to the nearest multiple of
the unit's advertised temperature step, counted from the unit's minimum, and hold
it between the unit's advertised minimum and maximum. Where the power ceiling is
binding, the rounding goes the way that stays inside it: cooling rounds up,
heating rounds down. The rounded value is the one recorded in the trace as the
commanded setpoint, the one compared with the unit's live setpoint, and the one
the thermal model sees. The outer loop's deadband is the larger of 0.3 C and half
the step. A room with two heads uses the coarser step and the narrower range,
provided one step is a whole multiple of the other; otherwise the setpoint is
left unrounded and the log says why. A unit that advertises no step is rounded to
tenths, as before. The step and range are the ones in the room's stored profile
(DR-048).

## Why

The Office Aircon advertises a step of 1.0 and limits of 18 to 30 C. The
component sent 22.8, 22.9 and 22.2. Across the recorder's history for
`climate.office_aircon`, every fractional setpoint stayed in place for at most 24
seconds, and whole degrees stayed for as long as 11,962 seconds. The unit holds
whole degrees and the entity reverts to what the unit holds. Since 0.8.11 the
coordinator compares the live setpoint with what it wants (DR-007), saw a
mismatch each cycle, and sent the command again, about every 10 seconds, with no
end. The learned trim was correcting against a setpoint the unit never received.
Nothing in the component read the step or the limits.

## Rejected

- **Leave it to the unit or the cloud.** They ignore the value and the component
  sends it again. That is the incident.
- **Round only inside the actuator at send time.** The trace and the thermal model
  would record 22.8 while the unit held 22.0, so every approach figure the model
  learns from would be wrong.
- **Round the trim itself.** The trim integrates a fraction of a degree per hour.
  Rounding it would stop it ever reaching a rounding boundary.
- **Keep the 0.3 C deadband at a 1.0 step.** The loop would chase an error the
  unit cannot resolve and flip the setpoint by a whole degree each time.
- **Round to nearest even where the power ceiling binds.** Rounding could carry
  the setpoint past the ceiling by up to half a step.

## Consequences

Sends of an unchanged setpoint stop once the unit holds the value. At a 1.0 step a
room is regulated to within half a degree of its target, and the trim shows up as
occasional whole-degree changes to the setpoint, not smooth ones. This record is
worth revisiting if a unit advertises a finer step than it holds, because the
loop above would return; the Intesis integration's step is a hardcoded 1.0, which
happens to match the Office unit. The live setpoint is already in the data, so a
trace reason could name a unit that does not hold what it was sent.

## In the code

Checked against: 0.9.0. **Conforms.**

- `regulate.py:383` - `def quantise_setpoint` - nearest step from the unit's minimum, inside its range, toward a ceiling
- `regulate.py:413` - `def effective_deadband` - half the step, never under 0.3 C
- `regulate.py:349` - `def combine_limits` - two heads: coarser step, narrower range
- `coordinator.py:1204` - `rounded = quantise_setpoint` - the last step before the setpoint is recorded and sent
- `coordinator.py:1251` - `def _setpoint_limits` - step and range from the stored profile
