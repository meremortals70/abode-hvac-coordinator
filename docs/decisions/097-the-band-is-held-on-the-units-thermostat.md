# DR-097: Inside its band the unit's thermostat holds the target, and the trim is learned there

| | |
|---|---|
| Status | Accepted; amends DR-054 and DR-056 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Root-and-branch review re-run, 2026-10-15, findings F9 and F10. Instruction, 2026-10-15: "go ahead and then review again" |
| Related | DR-015, DR-054, DR-056, DR-095 |

## Decision

Inside its band, a working unit is given the room's target, corrected by the measured sensor difference and the trim; the unit's own thermostat holds it there. The learned curve brings a room to its band. The trim is learned while the band is held, and outside the band only within a degree of the target.

## Why

The learned curve, used inside the band, rested on the modelled drift. Where that was overstated the unit was asked for 2.2 C below the room with the room already below its target, and the trim, frozen inside the band, never corrected it: the simulated Office was cooled below its band for an hour and then heated. A pull-down far from target wound the trim to -1.3 C, and a humid evening was then held 1.3 C cold for three hours.

## Rejected

- **Keep the curve inside the band and trust the integral.** Its gain is too slow for a feed-forward that is degrees out.

## Consequences

Inside the band the setpoint changes only when the target, the sensor difference or the trim does: one to three changes a day in simulation. A drift model that is wrong costs a pull-down, not the band.

## In the code

Checked against: 0.9.7. **Conforms.**

- `coordinator.py:1695` - `if trace.actuator is ActuatorStep.NONE and trace.hold_setpoint:` - the band held on the target
- `regulate.py:161` - `INTEGRATE_WITHIN_C = 1.0` - learned only near the target outside the band
- `coordinator.py:1588` - `and near_target` - the gate
