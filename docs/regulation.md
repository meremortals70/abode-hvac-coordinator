# Layer 2 — regulation

Layer 3 decides what the room should feel like and solves that into a dry-bulb
target. Something then has to make the room actually reach it.

That is not the same job, and until now nothing was doing it.

## The problem

Your air conditioner has a thermostat. It regulates well. It just does not
regulate against your room.

It regulates against its own return-air sensor: a thermistor inside the head,
high on a wall, reading air the unit has just pulled across its own coil. Your
room sensor is somewhere a person actually is. The two readings differ, and the
difference is not a constant — it moves with fan speed, with stratification,
with how long the unit has been running, and with where you put the sensor.

Command 24.0 and the head reads 24.0. The seat by the window reads 25.5. The
comfort index is computed from that seat, so the room is out of band while the
unit believes it has finished.

## What Layer 2 does

It trims the commanded setpoint until the **room sensor** reads the target. It
is the only thing in this project allowed to command a temperature other than
the one the comfort index solved for, and `sensor.<room>_commanded_setpoint`
publishes both numbers plus the trim between them, so the difference is never
silent.

## Why an integrator and not a calibration offset

The obvious alternative is a measured per-room offset. It is wrong the day
after you measure it: the offset that is right at full compressor is wrong at
idle, and the one that is right in still air is wrong with the vanes swinging.

An integrator needs no calibration, adapts as conditions change, and converges
on whatever the true offset is right now. What it costs is discipline.

| Rule | Why |
|---|---|
| Integral only, no derivative | the room sensor is noisy and the loop is slow; a derivative term on a thirty-second sample of an hour-scale thermal mass amplifies noise and nothing else |
| Integral only, no proportional | the unit's own thermostat **is** the proportional loop. A second one is two controllers on one actuator, which this project refuses everywhere |
| 0.35 °C of trim per °C of error per hour | a full degree of correction takes about three hours of steady error. Faster overshoots and hunts |
| 0.3 °C deadband | room sensors quantise at 0.1 and wander more than that with air movement alone |
| ±3 °C limit | beyond this the fault is not calibration. The unit is undersized, the sensor is misplaced, or a door is open — and winding further hides it |
| No integration while the compressor is off | anti-windup, and the important one |

That last rule is why this is not a textbook PI loop. A room that is coasting,
unoccupied, or held because a window is open has an error no actuator is
addressing. Integrating through it winds the trim to its limit against nothing,
and the first thing the room does on coming back is overshoot by three degrees.

An interval longer than fifteen minutes is capped, so a restart or a blocked
coordinator cannot deliver an hour of accumulated error in one step.

The trim is **not persisted**. It is only valid for the conditions that produced
it, and restoring last evening's correction into this morning's room would be
worse than starting from zero.

## When the trim pins

At ±3 °C the trace says so, in those words: the unit is not keeping up. That is
a real diagnostic. Something is wrong that a controller cannot fix, and the
right response is to look at the room rather than at the software.

## Short-cycle protection

Separate concern, same module, because both are about what the compressor is
allowed to do.

Short cycling is the most damaging thing a controller can do to a split system.
Every start draws locked-rotor current and floods the compressor with liquid
refrigerant, and neither is metered anywhere you will ever see it. Nothing else
in the stack prevents it: the unit's internal protection guards against its own
thermostat, not against a coordinator commanding `hvac_mode` from outside every
thirty seconds.

- **Ten minutes minimum run** once started
- **Five minutes minimum off** once stopped

A refused transition holds whatever the unit is already doing and writes the
refusal into the trace with the time remaining. A room that appears to ignore
its own decision with no explanation is exactly the fault this project will not
ship.

## Reading it

| Where | What it tells you |
|---|---|
| `sensor.<room>_commanded_setpoint` | what was actually sent |
| its `solved_target_c` attribute | what the comfort index asked for |
| its `regulation_trim_c` attribute | how far Layer 2 has moved it, and which way |
| the mode sensor's `reasons` | each integration step, in plain words |
| the mode sensor's `rejected` | short-cycle refusals, with minutes remaining |

A trim that settles near zero means your room sensor and the unit's agree. A
trim that settles at −1.8 means it has found a real 1.8 °C offset and is
correcting it on every cycle, which is the whole point.
