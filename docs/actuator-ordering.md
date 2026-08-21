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
- Cover control is disabled for this room — see below
- There is no way to tell whether the sun is on the room
- The sun is not on the room — moving a blind at night achieves nothing
- The covers are already where they need to be (within 5% of the useful
  extreme), which is what lets the ordering escalate
- **No cover in the room reports a position.** Since 0.8.6 an unknown position
  skips the step, and the trace says
  `covers: no cover in this room reports its position`

**Cover control can be turned off per room**, independent of whether covers
are configured. A semi-transparent blind kept for privacy or glare must not
be moved just because it is capable of being moved — the room simply
escalates straight past the covers step, exactly as if it had none
configured, and the trace records the distinct reason
`covers: control disabled for this room` rather than
`covers: none configured for this room`.

**The gate is sun geometry, not light level.** A semi-transparent blind reads
bright when it is fully closed, so a light sensor would report nothing to block
at exactly the moment the blind is already blocking. The controller took an
illuminance reading until 0.8.0 and acted on it nowhere; the field was removed
rather than left looking like an input.

The controller works this out itself, from the sun position Home Assistant
already publishes and the direction you told it the room's windows face. **No
extra sensor and no other integration is involved.** A room with no direction
set never uses its blinds, because it will not move them on a guess.

For a room too complicated for one compass direction — a corner room, or one
shaded at certain hours — point its sun-on-window setting at your own binary
sensor, which overrides the calculation.

Cover control belongs to this integration, which handles sun geometry,
venetian dual-axis sequencing and glare zones. This controller sets intent only.

### Covers with no reported position are skipped

Choosing covers is not free of consequence: it turns the climate entity off for
that cycle, because the point of trying the free option first is not to spend
compressor energy alongside it.

Until 0.8.6 a cover that reported no `current_position` was treated as worth
commanding — command it once, the reasoning went, and let the next evaluation
see the result. The next evaluation sees the same unknown. So a room whose
blinds never report a position chose covers on every cycle the sun was on the
glass, and the air conditioning stayed off for as long as that lasted. Nothing
cleared it: with the unit off the room heats, so the demand persists.

An unknown position now skips the step and the ladder falls through to fan,
dry and compressor as it would for a room with no covers at all. If your blinds
do not report a position, cover control simply does not apply to that room, and
the trace names the missing reading rather than leaving a silent gap.

## 2. Fan

Air movement, no compressor. Tried only when the room is within 0.5 HCI of the
band. Beyond that a fan is noise, and the trace says how far out of band the
room actually was.

**Heating skips fan entirely.** A fan does not add heat.

## 3. Dry

A latent-dominated load costs far less to shift with dry mode on a low fan than
with cooling.

Which route is faster is answered from the two rates the thermal model has
learned for this room, not from a humidity number. Both are converted into
comfort index closed per hour and compared directly; cooling has to be 25%
faster to be chosen, because dry mode gets the same effect at lower duty. While
either rate is unconverged a 65% humidity threshold applies and the trace says
it is falling back. See [Drying against cooling](latent-and-sensible.md).

**Heating skips dry entirely.** Dry mode does not add heat.

## 4. Compressor

Reached only when everything above has been ruled out. For heating, covers are
the only cheaper step, so heating goes covers → compressor.

**Skipped only on a computed power shortfall.** If power management is
configured and the tariff's `no_grid_import` constraint is in force for this
room's interval, the compressor step is refused when the arithmetic says
neither solar nor the battery can carry the room until the constraint lifts —
checked inline here, the same way `can_heat` and `can_cool` are checked, not
as a separate step. The trace records `compressor: no grid import permitted,
and battery/solar cannot cover this room's projected need`.

**It fails open.** Every case the controller cannot answer — a missing or
stale reading, a tariff series that does not reach the end of the window, no
solved target yet, a thermal model not converged enough to project — leaves
the room its comfort and logs a debug line. A projection that cannot be made
is not a reason to stop cooling an occupied room. Before 0.8.6 all four
returned a refusal, and since an unconverged model is the state every fresh
install is in, switching power management on stopped the compressor for the
whole of a no-import window. See
[Tariff](tariff.md#no_grid_import-what-it-actually-does).

## What is actually called

| Step | Call |
|---|---|
| Covers | `cover.set_cover_position`. 0% to block gain, 100% to admit it |
| Fan | `fan_only`, plus the quietest fan mode and the least draughty swing the unit advertises |
| Dry | `dry`, plus the quietest fan mode |
| Compressor | `cool` or `heat`, plus the setpoint, a mixing fan mode and a mixing swing mode |
| Nothing, in lockout or unoccupied | `climate.set_hvac_mode` to `off` |

Setpoints go through the standard `climate.set_temperature`, so this works
identically against a bare manufacturer entity or any wrapper over one.

The setpoint sent is not the solved target: Layer 2 trims it until the room
sensor reaches the target, because the unit regulates against its own
return-air thermistor. See [Regulation](regulation.md).

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
| Dry-mode advantage | 25% | Fixed internal |
| Dry mode humidity | 65% | Fallback only, until the model converges |

These are not settings and will not become settings. Exposing tuning parameters
is how configuration becomes unusable.

The humidity threshold was the primary test until 0.8.0 and was wrong: it
cannot distinguish a latent load from a sensible one, because 65% at 22 °C and
65% at 30 °C are different loads. The model learns the sensible and latent
terms separately and that split now makes the decision. The threshold survives
only as the fallback while a room's rates are still converging.
