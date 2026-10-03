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

It regulates the room sensor to the target by commanding the unit's setpoint,
and it is the only thing in this project allowed to command a temperature other
than the one the comfort index solved for. `sensor.<room>_commanded_setpoint`
publishes both numbers plus the trim between them, so the difference is never
silent.

## A PID, built on what the room has learned

Until 0.9.0 this loop was **integral-only**, on the reasoning that a second
proportional loop would be two controllers fighting over one actuator. That
reasoning was wrong. The unit's thermostat is the fast inner loop and this is
the slow outer one, which is an ordinary cascade, and a cascade is normally
built with a proportional term on the outside. Integral-only had no way to ease
off near the target and no sense of when the room would arrive. (DR-054)

The setpoint is now built from four terms:

| Term | What it does | Where it comes from |
|---|---|---|
| Feed-forward | The approach that just cancels the room's predicted drift, so a room warming at 0.6 °C an hour is held by asking for exactly the effort that stands still against it | The learned loss and solar terms, and the learned rate against approach, read backwards (`approach_for_rate`) |
| Proportional | The room is asked to close its error over **half an hour** | The same learned curve turns that rate into an approach |
| Derivative, on the measurement | The error is led by **six minutes** of the room's own rate of change, so the setpoint eases off before the room reaches target and it arrives in band without overshoot | The room's filtered rate of change, updated only when the sensor reading actually moves |
| Integral, **per direction** | Corrects the offset between the unit's sensor and the room's | One trim for cooling and one for heating |

**The trim is per direction.** The offset between the unit's sensor and the
room's is not the same number cooling as heating. Before 0.9.0 a trim learned
while cooling was carried straight into heating: on 3 October 2026 the Office
had a target of 23.2 °C, a heat demand and a commanded setpoint of 22.8 °C,
because a trim of −0.4 °C learned while cooling was applied to heating. Each
direction now has its own, and the trace publishes the one in force.

**Where the room has not learned enough, nothing changes.** Until the drift and
the rate against approach have both converged the loop sends the solved target
plus the trim for the direction, which is exactly what it sent before. An
unconverged coefficient is not a zero, and the loop does not pretend it is.

**The derivative term works on the measurement, never the error.** A sensor that
reports every five or six minutes, as a presence sensor often does, is a
staircase. The rate is updated only when the reading moves and decays between
readings, so one step does not read as a rate.

### The setpoint is rounded to what the unit can hold

The last step before a setpoint is recorded and sent is rounding it to the
unit's own step and holding it inside the unit's own range. Both are read from
the unit when the room is set up (see
[Configuration](configuration.md#what-the-air-conditioner-can-do)). (DR-051)

This was found the hard way. The Office Aircon advertises a step of 1.0 and holds
whole degrees. The integration sent 22.8, then 22.9, then 22.2; the unit kept
22.0; the integration saw the mismatch and sent again, about every ten seconds,
with no end. Across the recorder's history every fractional setpoint lasted at
most 24 seconds and whole degrees lasted for hours.

- Rounding is to the nearest step, counted from the unit's minimum.
- Where the power ceiling is binding, rounding goes the way that stays inside
  it: cooling rounds up, heating rounds down.
- A room with two heads uses the coarser step and the narrower range, provided
  one step is a whole multiple of the other. Otherwise the setpoint is not
  rounded and the log says why.
- A unit that advertises no step is rounded to tenths, as before.
- **The integral deadband is half the step, never under 0.3 °C.** At a whole-degree
  step an error of 0.4 °C cannot be resolved, and chasing it would flip the
  setpoint by a degree. The room is regulated to within half a step of target.

## Why an integrator and not a calibration offset

The obvious alternative is a measured per-room offset. It is wrong the day
after you measure it: the offset that is right at full compressor is wrong at
idle, and the one that is right in still air is wrong with the vanes swinging.

An integrator needs no calibration, adapts as conditions change, and converges
on whatever the true offset is right now. What it costs is discipline.

| Rule | Why |
|---|---|
| 0.35 °C of trim per °C of error per hour | a full degree of correction takes about three hours of steady error. Faster overshoots and hunts |
| 0.3 °C deadband, or half the unit's step if that is more | room sensors quantise at 0.1 and wander more than that with air movement alone, and a unit that holds whole degrees cannot resolve less than half a degree |
| ±3 °C limit | beyond this the fault is not calibration. The unit is undersized, the sensor is misplaced, or a door is open — and winding further hides it |
| No integration while the compressor is off | anti-windup, and the important one |

That last rule is why this is not a textbook PI loop. A room that is coasting,
unoccupied, or held because a window is open has an error no actuator is
addressing. Integrating through it winds the trim to its limit against nothing,
and the first thing the room does on coming back is overshoot by three degrees.

**The gate reads the step that was actually applied, not the one that was
wanted.** The short-cycle guard runs first and the integrator afterwards. Until
0.8.6 the order was reversed, so a start the guard had just refused was
integrated against anyway — winding the trim, in the one situation the gate
exists to prevent. The effect was small, about 0.03 °C per refusal, and it was
still precisely the condition the module refuses everywhere else.

A compressor the guard is holding on **does** count as regulating: it is
running and working toward the target, whatever step the decision named.

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

**Dry mode counts as running.** It energises the compressor. Treating it as a
stop, as the guard did before 0.8.6, blocked a cool-to-dry change for ten
minutes as though the compressor were shutting down, then recorded it as
stopped while it was in fact running — after which the minimum *off* time
blocked the return to cool, for a stop that never happened.

A refused *start* commands nothing this cycle.

A refused *stop* holds the compressor and leaves the decision alone. The guard
previously replaced the step with `compressor` outright, which meant a
compressor-protection mechanism silently cancelled cover and fan actions that
had nothing to do with the compressor — a blind that should have closed simply
did not. The trace now carries `hold_compressor`, and the actuator skips only
the parts of the decision that would have stopped the compressor early: the
covers still move, and the refusal is still written into the trace with the
time remaining.

A room that appears to ignore its own decision with no explanation is exactly
the fault this project will not ship.

## Reading it

| Where | What it tells you |
|---|---|
| `sensor.<room>_commanded_setpoint` | what was actually sent |
| its `solved_target_c` attribute | what the comfort index asked for |
| its `regulation_trim_c` attribute | how far Layer 2 has moved it, and which way |
| the mode sensor's `approach_c` | how far from the room's own reading the unit was asked to work, where the loop was built from what the room has learned |
| the mode sensor's `feed_forward_c` | the part of that which just holds the room against its drift |
| the mode sensor's `wanted_rate_c_per_hour` | how fast the room was asked to move toward target |
| the mode sensor's `setpoint_step_c` | the unit's step the setpoint was rounded to, or none |
| the mode sensor's `reasons` | each integration step, in plain words |
| the mode sensor's `rejected` | short-cycle refusals, with minutes remaining |
| the mode sensor's `hold_compressor` | true while a refused stop is holding the unit on |

A trim that settles near zero means your room sensor and the unit's agree. A
trim that settles at −1.8 means it has found a real 1.8 °C offset and is
correcting it on every cycle, which is the whole point.
