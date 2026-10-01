# DR-037: A no-import window is met with a setpoint ceiling, never a stop

| | |
|---|---|
| Status | Accepted; gated per room by DR-041 |
| Since | 0.8.10 (2026-08-23) |
| Origin | Finding 10, superseding DR-036 |
| Related | DR-015, DR-028, DR-039, DR-041, DR-042, DR-044 |

## Decision

During a `no_grid_import` window, the controller works out how many kW the
battery can spare until the window ends, after the rest of the house, bounded
by the battery's rated discharge. That allowance picks the most permissive
operating-point bin whose learned draw fits, and the bin's approach becomes a
ceiling on how far the commanded setpoint may sit from the room's own
reading. Nothing in this mechanism ever commands the unit off. Precool is not
rationed.

## Why

The compressor is continuously variable and the setpoint is what an inverter
modulates against, so the setpoint is a real throttle. The approach bins
(DR-028) finally gave each operating point a rate and a draw, which is what
rationing needs. Stopping is not a compliance action: without a view of grid
flow at decision time, the controller cannot tell that stopping would honour
the constraint.

Precool runs in a window chosen because energy is free, and drives to the
bottom of its band, not the middle.

## Rejected

- **The boolean veto (DR-036).** Abandons the room.
- **Fan speed as the throttle.** Air movement is an input to the comfort
  index, so the saving partly pays for itself and the interaction is circular.
- **Each room against the whole battery.** Compared against the house total
  instead.

## Consequences

The regulator must not wind up against the ceiling (DR-015). When even
holding at setpoint cannot be afforded, the ceiling floors at 0.0 C: the
commanded setpoint is held at the room's own reading.

## In the code

Checked against: 0.8.14. **Conforms.** Precool was rationed in 0.8.12; the
exemption was added in 0.8.13.

- `power.py:94` - `allowable_draw_kw` - the allowance
- `power.py:121` - `ceiling_bin` - the most permissive bin that fits
- `coordinator.py:2350` - `_power_ceiling` - the ceiling
- `coordinator.py:2397-2403` - `Mode.PRECOOL` - precool is not rationed
- `coordinator.py:2474-2490` - `bin_index is None` - floor at 0.0 C, never off
- `power.py:216-229` - `held_setpoint` - the clamp
