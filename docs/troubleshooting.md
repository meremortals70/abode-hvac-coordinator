# Troubleshooting

**Start with the decision trace.** Open `sensor.<room>_mode` and read the
`reasons` and `rejected` attributes. Nearly every question below is answered
there, in the room's own words.

## The room is doing nothing

Check `actuator` on the mode sensor. If it is `none`, `rejected` says why. The
usual causes:

| Trace says | Cause |
|---|---|
| `room is in lockout` | A lockout reason is set. Clear it in Configure |
| `room unoccupied, air conditioning off` | Working as designed. Use heading home |
| `an opening in this room is open` | A window or door is open |
| `no comfort reading or no band in force` | Missing sensor, or no band for this mode |
| `within band` | Nothing to do |

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

## Coast and precool never happen

Correct, currently. Both need modules that are not built — the thermal model
and the demand forecast. The trace says
`coast: thermal model has not converged for this room`. See
[Known limitations](known-limitations.md).

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
