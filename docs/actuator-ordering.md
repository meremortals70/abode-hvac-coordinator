# Actuator ordering

Before increasing compressor load, exhaust the cheaper options in order. This is
the largest energy saving available and the reason covers belong inside this
controller rather than running as a separate automation.

```
1. Covers      free
2. Fan         circulation only
3. Dry         latent load at a fraction of cooling draw
4. Compressor  cooling or heating
```

Nothing reaches a step until every step above it has been ruled out, and **every
rule-out is written into the decision trace** with the reason. That is what
makes the ordering auditable rather than merely asserted.

## Gates, checked before any actuator

| Condition | Result |
|---|---|
| Room in lockout | Nothing |
| Room unoccupied | Nothing |
| An opening in the room is open | Nothing |
| Coasting | Nothing |
| No temperature or humidity reading | Nothing |
| No comfort band configured for this mode | Nothing |
| Comfort index inside the band | Nothing |

A room with no bands configured never actuates. There is no default to fall
back on, and inventing one would be worse than doing nothing.

## Direction

The controller first works out which way the room needs to move:

- Index above the band high → **cool**
- Index below the band low → **heat**
- Inside → nothing to do
- In precool, above the band low → **cool**, because precool targets the low
  bound rather than the middle

Direction is published in the trace as `demand`.

## 1. Covers

Free, and they work both ways: block solar gain when the room is too warm, admit
it when the room is too cold.

Skipped when:

- The room has no covers configured
- There is no way to tell whether the sun is on the room
- The sun is not on the room — moving a blind at night achieves nothing
- The covers are already where they need to be (within 5% of the useful
  extreme), which is what lets the ordering escalate

**The gate is sun geometry, not light level.** A semi-transparent blind reads
bright when it is fully closed, so illuminance would report nothing to block at
exactly the moment the blind is already blocking.

The controller works this out itself, from the sun position Home Assistant
already publishes and the direction you told it the room's windows face. **No
extra sensor and no other integration is involved.** A room with no direction
set never uses its blinds, because it will not move them on a guess.

For a room too complicated for one compass direction — a corner room, or one
shaded at certain hours — point its sun-on-window setting at your own binary
sensor, which overrides the calculation.

Cover control belongs to this integration, which handles sun geometry,
venetian dual-axis sequencing and glare zones. This controller sets intent only.

## 2. Fan

Air movement, no compressor. Tried only when the room is within 0.5 HCI of the
band. Beyond that a fan is noise, and the trace says how far out of band the
room actually was.

**Heating skips fan entirely.** A fan does not add heat.

## 3. Dry

A latent-dominated load costs far less to shift with dry mode on a low fan than
with cooling. Selected when indoor humidity is at or above 65%.

**Heating skips dry entirely.** Dry mode does not add heat.

## 4. Compressor

Reached only when everything above has been ruled out. For heating, covers are
the only cheaper step, so heating goes covers → compressor.

## What is actually called

| Step | Call |
|---|---|
| Covers | `cover.set_cover_position`. 0% to block gain, 100% to admit it |
| Fan | `fan_only`, plus the quietest fan mode and the least draughty swing the unit advertises |
| Dry | `dry`, plus the quietest fan mode |
| Compressor | `cool` or `heat`, plus the setpoint, a mixing fan mode and a mixing swing mode |
| Nothing, in lockout or unoccupied | `climate.set_hvac_mode` to `off` |

Setpoints go through the standard `climate.set_temperature`. Versatile
Thermostat has no service of its own for it, so this works identically against
a Versatile Thermostat wrapper or a bare manufacturer entity.

**Choosing covers turns the climate entity off** for that cycle. Trying the
free option first means not spending compressor energy alongside it. If the
room is still out of band next cycle and the covers have no travel left, the
ordering escalates.

**Covers must have somewhere to go.** A blind already within 5% of shut counts
as shut, is rejected with `covers: already closed against the gain`, and the
next step is tried instead.

### Nothing is sent that the entity has not advertised

Every call is resolved against the entity's own capabilities, not against
assumptions about any particular adaptor:

| Wanted | Resolution order |
|---|---|
| Cooling | `cool`, then `heat_cool`, then `auto` |
| Heating | `heat`, then `heat_cool`, then `auto` |
| Dry | `dry` only |
| Fan | `fan_only` only |

A unit with no dedicated `cool` mode may still cool in `heat_cool` or `auto`,
so those are real fallbacks rather than failures, and the trace records which
was used.

Capabilities also reach the **decision**, not just the actuation: a unit with no
dry mode never has dry chosen for it, so the ordering escalates properly instead
of stalling on a step that cannot be carried out.

Targets follow `supported_features`. A unit taking a single target gets
`temperature`; one taking a range gets `target_temp_low` and `target_temp_high`
straddling the target by 1 °C, because sending a single value to a range-only
unit is either rejected or silently applied to one side.

**Swing is used where the unit has it.** A comfort index measured at one sensor
is misled by a stratified room, so vanes move while conditioning and settle
while idling. Fan and swing are only touched when `supported_features` says the
unit has them.

An unchanged decision is not re-sent.

## The thresholds

| Threshold | Value | Status |
|---|---|---|
| Fan margin | 0.5 HCI | Fixed internal |
| Cover travel margin | 5% | Fixed internal |
| Dry mode humidity | 65% | **Placeholder** |

These are not settings and will not become settings. Exposing tuning parameters
is how configuration becomes unusable.

The one marked placeholder is a stand-in for the thermal model: a single
humidity threshold cannot distinguish a latent load from a sensible one. 65% at
22 °C and 65% at 30 °C are different loads. The model learns the sensible and
latent terms separately, and that split will make this decision properly.
