# Troubleshooting

**Start with the decision trace.** Open `sensor.<room>_mode` and read the
`reasons` and `rejected` attributes. Nearly every question below is answered
there, in the room's own words.

## The room is doing nothing

Check `actuator` on the mode sensor. `rejected` says why.

**`off`** means the unit was commanded off:

| Trace says | Cause |
|---|---|
| `room is in lockout` | A lockout reason is set. Clear it in Configure |
| `room unoccupied, air conditioning off` | Working as designed. Use heading home |
| `an opening in this room is open` | A window or door has been open two minutes or more. The unit is off until it closes |
| `coasting, model predicts the band holds` | Working as designed |
| `preconditioning, but the deadline is far enough out that the pull can wait` | Working as designed |
| `this unit cannot cool` / `cannot heat` | The room needs a direction the unit does not have |
| `no grid import permitted…` | The tariff forbids import and the battery cannot carry the room |

**`none`** means nothing was commanded, and the unit is holding whatever
setpoint it was last given:

| Trace says | Cause |
|---|---|
| `within band` | Nothing to do. The unit's own thermostat is holding the setpoint |
| `no comfort reading for this room` | Missing temperature or humidity sensor. Comfort is held rather than withdrawn |
| `no band configured for this mode` | No band for the mode the room is in |
| `an opening in this room is open, holding the unit as it is…` | A door has just opened. Nothing new is actuated, and the unit is not stopped yet in case it closes |

## The comfort index is unavailable

The temperature or humidity sensor has no reading. Check both in Developer Tools
→ States. A sensor showing `unknown` or `unavailable` will make the index
unavailable too, deliberately, rather than showing a stale number.

## A room shows no bands and never actuates

A repair issue names the affected rooms. Add bands in Configure, or set a
lockout reason if the room is deliberately inactive.

## Sleep mode never happens

The room has no sleep schedule configured. Without one the sleep band is dead
config. Add a Schedule helper and select it as the room's sleep schedule.

## Coast never happens

Both the thermal model and the demand forecast are built, so this is almost
always convergence rather than a fault. The trace says
`coast: thermal model has not converged for this room`, and it needs 20 samples
per coefficient with the variance down before it will answer. Until then the
band is simply held — the hysteresis fallback, working as intended.

Coasting also stops the moment the tariff window does not permit it.

## Precool never happens

Three things must all be true, and the trace names which one failed:

- the tariff declares `precool_opportunity` on the current interval — check
  `sensor.active_constraints`
- a weather entity is configured, under Configure → House configuration →
  Weather forecast
- the forecast peak over the next ten hours is at least 3 °C above the room

Without a weather entity precool falls back to comparing current conditions,
which at midday cannot see the afternoon coming. The trace says it is falling
back rather than simply reporting no demand.

## Covers are never used

Three possible causes, and the trace distinguishes them:

- `covers: none configured for this room` — add them in Configure
- `covers: cannot tell whether the sun is on this room` — no sun sensor and no
  `sun.sun` either
- `covers: no sun on this room to act on` — the sun is not on the glass
- `covers: already closed against the gain` — working as designed; the ordering
  has escalated to the next step

If covers never move on a room the sun clearly reaches, the room has no window
direction set. Choose which way its windows face in Configure.

## The unit never runs in a mode I expected

Read `rejected` on the mode sensor. A unit that does not advertise a mode never
has it chosen — `dry: this unit has no dry mode`, `compressor: this unit cannot
heat`. The controller reads `hvac_modes` from the entity itself, so if a mode is
missing there it does not exist as far as this is concerned.

If the trace says `cool unavailable, using heat_cool instead`, the unit has no
dedicated cool mode and the fallback was used deliberately.

## The setpoint looks wrong for the temperature

It is not a temperature. The band is in HCI and the setpoint is derived from the
band **and the humidity**. The same band gives a lower setpoint on a humid
night. Compare `hci`, `band_low`, `band_high` and `target_dry_bulb_c` together.

If `rejected` contains `clamped`, the band and the measured humidity together
imply a setpoint outside 5–40 °C. The band needs adjusting.

## The integration will not load

Check the log. Configuration problems raise a message naming the room:

- `Comfort bands for room X are invalid` — a band is malformed or inverted
- `Room configuration is missing ...` — a required field is absent

A broken tariff does **not** stop the integration. It is logged and ignored, and
rooms continue on comfort alone.

## Enable debug logging

```yaml
logger:
  default: warning
  logs:
    custom_components.abode_hvac_coordinator: debug
```

Every evaluation logs its mode, actuator, index, target and reasons.

## Reporting a problem

Download diagnostics from the device page and attach them. They contain the
configuration, the tariff, and the current trace for every room — which is
almost always enough to see what happened without a conversation about it.

## The commanded setpoint is not the number I expected

That is Layer 2 working. `sensor.<room>_commanded_setpoint` carries
`solved_target_c` and `regulation_trim_c` in its attributes: the first is what
the comfort index asked for, the second is how far the outer loop has moved it
to make the room sensor actually get there. See [Regulation](regulation.md).

If the trim has pinned at ±3 °C the trace says the unit is not keeping up. That
is a real finding, not a tuning problem — look at the room.

## The room decided to stop and did not

Check the mode sensor's `rejected` list for a short-cycle guard entry. It names
the minutes remaining. Ten minutes minimum run and five minutes minimum off are
compressor protection and are not configurable.

## A room is holding and its sensors all look fine

Check `sensor.stale_feeds` and the room's own `stale_feeds` trace attribute. A
Zigbee device that has left the mesh keeps its last state indefinitely, so a
reading that looks present can be hours old. See [Stale feeds](staleness.md).

## The tariff rate sensor is empty

Either no tariff entry is selected, or the fetch is failing. A repair issue
distinguishes them and names the reason. The controller keeps the last series
it read rather than dropping to no tariff on one bad call, so a short outage in
Abode Power Tariffs changes nothing visible.
