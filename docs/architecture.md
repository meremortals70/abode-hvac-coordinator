# Architecture

**Version 0.8** — supersedes v0.5. The architecture is complete against this design; every gap identified at v0.5.3 is closed. Three material changes since v0.5.

**Layer 2 is built, not adopted.** The plan was to wrap an existing
over-climate regulator. That was wrong: what was needed is one narrow thing —
closing the loop around the unit's own thermostat so the *room* sensor reaches
the target — and adopting a whole regulator to get it would have imported
presence handling, interlocks and preset machinery that Layer 3 already owns,
producing two controllers on one actuator.

**The tariff moved out.** Periods, prices and constraints belong to Abode Power
Tariffs. This integration reads them and holds none of them.

**The controller can see ahead.** A weather forecast drives precool, and the
learned sensible and latent rates drive the choice between cooling and drying.
Both replaced placeholders that compared the wrong things.

---

## 1. The layer model

```
┌─────────────────────────────────────────────────┐
│ L3  COORDINATOR              this integration   │
│     rooms, modes, comfort index, actuator       │
│     ordering, thermal model, trace              │
├─────────────────────────────────────────────────┤
│ L2  REGULATION               this integration   │
│     outer loop to the room sensor, short-cycle  │
│     protection                                  │
├─────────────────────────────────────────────────┤
│ L1  DEVICE DRIVER            replaceable        │
│     whatever presents a climate entity          │
├─────────────────────────────────────────────────┤
│ L0  READ-ONLY INPUTS                            │
│     sensors, weather forecast, battery,         │
│     tariff series                               │
└─────────────────────────────────────────────────┘

Abode Power Tariffs sits at L0 as far as this integration is concerned: a
read-only input, consumed through one service call, never written to.
```

The separation earns its keep in one specific way: **Layers 2 and 3 consume a
`climate` entity and do not care what is underneath.** The whole coordinator can
be built, run and judged against existing hardware while a driver replacement
proceeds on its own timeline. Neither waits for the other.

### Layer 1 — device driver

Anything that presents a `climate` entity. Replacing it is a per-room,
reversible change that Layer 3 does not notice.

A better driver buys per-head power and energy, compressor frequency, full vane
control, and error codes. Those improve the thermal model and enable fault
surfacing. None of them are required for the coordinator to work.

### Layer 2 — regulation

Built here, in `regulate.py`. Two responsibilities and no others.

**Close the outer loop.** The unit's thermostat regulates against its own
return-air sensor, which is not the room. An integral-only controller trims the
commanded setpoint until the room sensor reads the target. Integral only: the
unit's thermostat is already the proportional loop, and a second one is two
controllers fighting over one actuator.

**Protect the compressor.** Ten minutes minimum run, five minutes minimum off.
Nothing else in the stack prevents short cycling, because the unit's own
protection guards against its own thermostat rather than against a coordinator
commanding `hvac_mode` from outside.

Interlocks, presence and preset handling are **not** here. Layer 3 owns them
already, and duplicating them was the reason adopting a regulator was rejected.

See [Regulation](regulation.md).

### Layer 3 — the coordinator

This integration. Everything below is about it.

---

## 2. Internal structure

Four modules make decisions and import nothing from Home Assistant. Six modules
talk to Home Assistant and make no decisions.

```
        pure                          Home Assistant
    ┌──────────┐
    │  hci     │  index + inverse
    │  models  │  modes, room, inputs, trace
    │  modes   │  precedence + ordering  ◄──── coordinator ──── entity ── sensor
    │  tariff  │  windows + constraints         (gathers)      (device)  (publishes)
    └──────────┘                                    │
                                                  store
                                              (learned state)
```

This is not architectural decoration. It means every decision the system makes
is reachable in a plain Python session, with no Home Assistant, no mocking and
no fixtures — which is why the decision path has 43 tests and the rest has
almost none.

**The rule that keeps it true:** if a decision ends up in `coordinator.py`, it
is in the wrong place.

---

## 3. Settled principles

### Comfort is the constraint, not the variable

Cost never narrows the band. The tariff decides *when* energy is banked ahead of
need and *which* actuator delivers comfort. It never decides whether you get it.

**Since 0.8.11, cost-minimisation is not confined to one mode.** Every mode and
every operation evaluates the cheapest way to deliver whatever the comfort band
currently requires, at the point the decision is made — precool banking thermal
mass ahead of a load that is coming, and, new in 0.8.11, a room approaching the
edge of its band deferring briefly for an imminent cheaper tariff interval when
the thermal model can say the wait is safe. Neither ever narrows the band or
delays a correction the model cannot vouch for: an unconverged model, a missing
price, or a tariff window that forbids it all mean the room is corrected now,
on the same fail-safe-to-comfort principle the rest of the architecture already
uses. See [What is built](#8-what-is-built).

### No manual-override concept

This is an autonomous coordinator, not one that shares control with the wall.
Every cycle re-asserts the coordinator's own computed decision from the
climate entity's live reported state — never from memory of the last command
sent. A change made at the wall, or by any other automation, is simply
overridden on the next cycle, the same as any other stale command would be.

**The fix for an outcome nobody wants is to change the room's comfort bands,**
not to expect the hardware to hold a setting made outside this integration.
Building a reconciliation loop that treats a wall change as intent would be
building a second control surface into a system whose entire premise is that
there is exactly one.

### One comfort definition per room

The band. Not a band plus a setpoint, not a band plus a target for
preconditioning. Preconditioning drives to the occupied band because there is
nothing else to drive to.

The user states how the room should feel; the controller works out what to ask
for. The humidity correction happens inside that derivation, which is the entire
justification for an index rather than a temperature.

### An unoccupied room is off

Not a wider envelope. Off. The only override is an explicit heading-home
request.

This replaces the v0.3 position, which gave unoccupied its own wide band. That
was a compromise nobody asked for: it spent energy on empty rooms to avoid a
restart cost that the thermal model can simply predict.

### Configuration discipline

A setting exists only if a user cannot get a correct result without it. Applied
strictly, the entire comfort configuration is one number pair per room per mode.

This applies to inherited components too. Surfacing a regulator's tuning
parameters or a model's coefficients rebuilds the problem one layer up.

### Tariff constraints are absolute

Declared in configuration, not hard-coded. Never traded against comfort or price
at runtime. An unrecognised constraint is reported rather than dropped, so
adding one for another system to consume needs no code change here.

If a constraint and the comfort band cannot both hold, the controller says so
and holds the constraint.

**`no_grid_import` is still absolute — what changed, across two builds, is
how the controller responds to it.** This project has no control over where
household power comes from, so it cannot enforce the constraint directly.
What it does since 0.8.10 is throttle: it estimates how much energy the
battery can spare, converts that into a setpoint ceiling using the room's
own learned rate and draw at each operating point, and holds the compressor
to that ceiling rather than either running it flat out or refusing it
outright. That decision is optional (nothing engages until battery, solar
and house-load readings are all configured — see
[Configuration](configuration.md#power)), it is assessed per room against
shared live readings rather than negotiated between rooms, and it is applied
inside the same regulation step that trims the commanded setpoint for every
other reason — not as an override layered on afterward.

**The boolean veto that stood from 0.8.5 to 0.8.9 is retired, not narrowed —
and replaced by a checkbox the room's occupant controls, not a mechanism the
controller applies on its own judgement.** `allow_comfort_reduction`, off by
default, per room. Off, power management does not touch the room at all:
comfort wins unconditionally and the room holds its band regardless of the
constraint, exactly as if the feature were not configured. On, the
constraint is enforced for that room — the ceiling applies whether the room
is already inside its band or calling for correction, holding it as close to
the band as the remaining energy allows rather than running it flat out or
refusing it. **No state of this feature, in any configuration, ever commands
the compressor off for a power reason** — enforcing the constraint means
throttling toward it, never abandoning the room. Where the budget cannot
even afford holding at setpoint, the ceiling floors at "ask for nothing
colder than the room already is" rather than becoming a stop.

**Since 0.8.11, solar is checked first, as a direct offset — the battery
only becomes a binding constraint at all where solar is insufficient.**
Solar generation pays down the rest of the house's draw before anything is
asked of the battery; whatever is left over after that goes straight to the
room being throttled, free of both the battery's stored-energy limit and its
rated discharge-rate limit. Only the shortfall — what solar cannot cover —
draws against the battery at all. The credit is derated using the weather
forecast's clear-sky fraction across the remaining hours of the constrained
window, so a window that runs past sunset, or into cloud the forecast
already expects, is not credited with the current moment's solar persisting
for hours it will not; a house with no weather entity configured sees the
instantaneous reading unchanged, exactly as before this finding.

**Since 0.8.7 the actuation reaches the hardware; since 0.8.10 a grid sensor
lets the controller measure rather than only project.** One signed power
reading, its sign settled once at setup against live evidence and never
inferred again, gives the controller `battery = house_load − solar − grid`
— a diagnostic view of the rate energy actually leaves the battery. The
setpoint ceiling itself is bounded by the battery's rated maximum discharge
power, entered at setup rather than derived from this reading. The grid
sensor's own contribution is measurement: it lets a `no_grid_import`
window's actual import be reported afterward, rather than reconstructed
from a projection nobody could verify.

**It fails open.** The ceiling applies only when every reading the budget
needs is present; any missing or stale figure leaves the room unthrottled,
exactly as an unconfigured feature would. Comfort is a hard constraint, and a
number the controller cannot compute is not grounds for constraining it.
See [Tariff](tariff.md#no_grid_import-what-it-actually-does).

### One writer per actuator

| Actuator | Owner |
|---|---|
| Battery | Whoever owns the battery. **Never this project** — this project only ever reads state of charge and grid flow, to decide how gently a room may run under `no_grid_import` |
| Climate entities | The regulation layer, driven by Layer 3 |
| Covers | The cover layer, driven by Layer 3 |

Two reasons, the second stronger.

**Two writers fail silently.** If this controller sets a battery reserve and
another automation overwrites it four minutes later, nothing errors — the
battery just behaves oddly.

**Battery control is vendor-specific.** Different vendors expose different
primitives with incompatible semantics. Coding one in would tie the project to
one manufacturer.

Instead the controller publishes a **vendor-neutral demand forecast**: projected
energy over a horizon plus the constraint windows in force. Whoever owns the
battery translates that into their own primitives.

### The decision trace is not optional

Every room publishes why it is in its current state, including which cheaper
actuators were rejected and on what grounds. Without it the system is
unmaintainable, and a controller nobody can audit is a controller nobody should
run.

---

## 4. The comfort index

Steadman shaded apparent temperature, wind zero. Full detail in
[Comfort index](comfort-index.md).

**This changed from v0.3 and the change matters.** An earlier index in use fell
as humidity rose. That is backwards: at fixed temperature, humid air is less
comfortable, because sweat evaporates less readily. A controller running on an
inverted index concludes a muggy room is fine and sits there doing nothing on
exactly the night you want it dehumidifying.

The band numbers moved with it. Bands calibrated against the old index are not
transferable.

---

## 5. Actuator ordering

Covers, fan, dry, compressor. Full detail in
[Actuator ordering](actuator-ordering.md).

Two things make this real rather than aspirational:

**Direction.** The controller works out whether the room needs cooling or
heating first. Heating skips fan and dry entirely — neither adds heat — so
heating goes covers, then compressor.

**Auditability.** Every skipped step writes its reason into the trace. A claim
that the cheap options were exhausted is worthless without evidence, and the
evidence is per-decision.

---

## 6. Thermal model

Per-room, learned from observation, with hysteresis fallback until converged.
**The system works on day one and improves**, rather than requiring a training
period before it does anything.

Built. Full detail in [Thermal model](thermal-model.md). What it unblocks:

| Consumer | What it needs |
|---|---|
| `COAST` | Whether the band holds unaided over a horizon |
| `PRECOOL` | How far to overshoot without waste |
| Heading home | When to start, to arrive at comfort on time |
| Dry mode selection | The sensible/latent split, replacing a humidity threshold |
| Demand forecast | Projected energy over a horizon |
| Power-aware compressor decision | Projected energy until `no_grid_import` clears, weighed against battery headroom |

**The latent term is the addition.** Models built for heating climates learn
heat loss, heating power and solar responsiveness — all sensible-heat terms. A
humid subtropical climate needs latent load learned separately, or the filter
fits one coefficient and is wrong on exactly the days the two diverge.

Rain is that case: dry bulb falls while humidity climbs toward saturation, so
sensible load drops as latent load rises. The compressor may still need to run
to hold the band on a day that feels cool.

---

## 7. Forecast inputs

| Input | Role |
|---|---|
| Irradiance forecast | Solar gain, far better than a weather condition string |
| Weather forecast | Temperature and humidity trajectory, giving sensible and latent load separately |
| Cover state | A room with blinds closed has a different gain profile to the same room open |

Two effects run in opposite directions and the forecast must resolve them rather
than pass them on: rain means less solar generation **and** less cooling
required. A poor generation forecast does not automatically mean tighten up,
because the reduced solar gain has already reduced the load.

Winter is the inverse — on a sunny winter day, north-facing rooms need
materially less heating, and the correct action is to open the blinds rather
than run the compressor.

---

## 8. What is built

| Component | State |
|---|---|
| Room model, modes, precedence | Built, tested |
| Comfort index and inverse | Built, tested |
| Actuator ordering | Built, tested |
| Tariff consumption from Abode Power Tariffs | Built, tested |
| Forecast-driven precool | Built, tested |
| Learned latent/sensible actuator choice | Built, tested |
| Layer 2 outer-loop regulation | Built, tested |
| Short-cycle protection | Built, tested |
| Feed staleness guard | Built, tested |
| Dew point, free cooling, condensation risk | Built, tested |
| Deadline-aware preconditioning | Built, tested |
| Sleep band ramp | Built, tested |
| Decision trace | Built, tested |
| Config flow, devices, entities, diagnostics | Built, untested |
| Learned state persistence | Built, unused |
| Thermal model | Built, tested |
| Demand forecast | Built, tested |
| Actuation | Built, tested |
| Power-aware compressor decision (`no_grid_import`) | Built and tested. Rebuilt in 0.8.10: the boolean veto retired in favour of a setpoint ceiling — see below |
| Per-room cover-control override | Built, tested |
| Stopping the compressor, as distinct from leaving it alone | Built, tested. New in 0.8.7 |
| A room's heads, and which outdoor unit each is on | Built, tested. New in 0.8.8 |
| Heat load and air movement inputs | Built, tested. Reachable in the room form from 0.8.7; before it, configured nowhere and always absent |
| Required comfort inputs at room setup | Built, tested. New in 0.8.9 |
| `k_sensible` binned by operating point | Built, tested. New in 0.8.9. The one caller left reading the pooled figure regardless of approach (the dry-versus-cool comparison) was fixed in 0.8.10 |
| Learned per-outdoor-unit draw | Built, tested. New in 0.8.9. Needs a house-load sensor configured; without one, every consumer sees the same assumed constant as before |
| The dry-versus-cool comparison, without the constant-RH derivative | Built, tested. New in 0.8.9 |
| The grid sensor, and derived battery power | Built, tested. New in 0.8.10. Optional; without it the power budget still runs on the energy figure alone |
| The power budget and setpoint ceiling, replacing the boolean veto | Built, tested. New in 0.8.10 |
| Per-room permission to extend the ceiling past the comfort band | Built, tested. New in 0.8.10 |
| Cost-minimisation at every decision point (the room-price defer) | Built, tested. New in 0.8.11 — see below |
| Solar checked first in the power budget, battery only on the shortfall | Built, tested. New in 0.8.11. Corrects a gap in the 0.8.10 rewrite, where solar was dropped from the calculation entirely — see below |
| Re-asserting the commanded setpoint from live entity state | Built, tested. New in 0.8.11 — see below |
| Grid sign zero-flow correction | Built, tested. New in 0.8.11 — see below |

Since 0.8.6 the whole suite runs against a real Home Assistant, not only the
pure modules — 443 tests at 0.8.11, against 2025.1.4 rather than the 2026.8.x
targeted, because the build sandbox is Python 3.12. Running it for the first
time found two tests that had never passed. See
[Known limitations](known-limitations.md).

### The power budget replaces the boolean veto

**New in 0.8.10.** From 0.8.5 to 0.8.9, `no_grid_import` was enforced by a
boolean: a room whose projected energy need exceeded the battery's spare
capacity had its compressor refused outright, above its comfort band, on a
positively computed shortfall. That refusal is gone. The only actuators this
project has ever owned are the commanded setpoint and fan speed — the
compressor itself is continuously variable — and 0.8.9's approach bins
finally gave an operating point both a rate and a cost, which is what
rationing needs.

`_power_ceiling` computes an allowance in kWh from the battery's spare
energy, bounded by the battery's **rated maximum discharge power** — a
nameplate specification entered at setup (`CONF_BATTERY_MAX_DISCHARGE_KW`),
not learned or assumed unbounded — minus the rest of the house's own
measured draw. The allowance selects the most permissive approach bin the
room's compressor group can afford, and that bin's approach becomes a
ceiling on how far below (heating: above) the room's own reading the
commanded setpoint may go.

**The ceiling never stops the compressor, and the checkbox is a single,
unconditional gate on the whole mechanism.** `allow_comfort_reduction`, off
by default, per room. Off, power management does not touch the room at
all — comfort wins and the band is held regardless of the constraint, even
while the room sits comfortably inside it. On, the constraint is enforced:
the ceiling applies whether the room is inside its band or calling for
correction, holding it as close to the band as the remaining energy allows —
running at the largest output the budget affords, spread across the hours
until the constraint clears, degrading gradually as the battery depletes
rather than either running flat out or being abandoned. Where even holding
at setpoint cannot be afforded, the ceiling floors at zero further
correction — never at an actuator state that turns the unit off.

Anti-windup follows the same rule finding 7 (0.8.6) established for a
guard-refused start: the regulation integrator does not wind against a
setpoint the ceiling is actively capping.

### The grid sensor

**New in 0.8.10.** One signed power reading, normalised at the point of
reading into import watts. The sign convention is resolved once, at setup,
from live evidence — `house_load − solar` as a lower bound on what the house
must be drawing from somewhere, compared against the raw reading's own sign
— and stored. Never inferred again at runtime; a persistent disagreement
between live evidence and the stored convention is named in a repair issue,
never auto-corrected.

`battery = house_load − solar − grid` is a derived, diagnostic figure —
published so the actual rate energy leaves the battery can be seen, though
the power budget itself is bounded by the rated maximum discharge power
(above) rather than by this reading. Optional: without a grid sensor
configured, the budget still runs on the energy and rated-discharge figures
alone, and no breach can be measured.

A `no_grid_import` window's actual grid import is integrated across the
window and, where it exceeds a small noise floor, reported in a repair issue
naming the kWh — a measured figure, not a reconstruction, and the same
mechanism whether or not any room's comfort-reduction checkbox is set.

### A room has heads; heads sit on outdoor units

**New in 0.8.8.** Two relations, and neither implies the other.

A room's `climate_entity_ids` is a list. One is the usual case; two is a room
served by two indoor units, which gets one band, one target and one commanded
setpoint sent to both. What the room can do is the **intersection** across its
heads — claiming dry mode because one of two has it produces a decision the
actuator cannot carry out on the other.

Separately, each head carries an outdoor unit **name**. Two heads with the same
name share a compressor, whether they sit in one room or two. Membership is
derived from that one rule rather than held as objects, which covers every case
in this house — two rooms on one outdoor unit, two heads in one room on one
outdoor unit, and two heads in one room on two separate ones — with nothing to
keep in step.

A head with no declared name is its own compressor, so a house that declares
nothing behaves exactly as it did.

**The short-cycle guard is keyed by compressor.** `MIN_RUN` and `MIN_OFF`
protect a compressor and `RegulatorState` was keyed by room, so two rooms with
a head each on one outdoor unit refused and held each other's transitions in
both directions. Cycling state moved to `CompressorState`, keyed by outdoor
unit; the regulation trim stays keyed by room, because it corrects for where
that room's sensor sits.

A compressor's demand is the **or** across the rooms on it. A room reaching its
band, or emptying, does not stop an outdoor unit that its neighbour is still
calling on.

**This is not multi-head arbitration**, which remains settled as not
applicable. No room's comfort is traded against another's, no starts are
sequenced and nothing is staggered. The compressor protection simply counts
compressors.

### The sensible rate and the compressor draw both depend on how far from setpoint the room is

**New in 0.8.9.** `k_sensible` was one coefficient describing exactly one
operating point — full tilt — because a room spends most of its life near
setpoint, where an inverter modulates down and the true rate is a fraction
of that, and the filter averaged the two into a number describing neither.

**Approach** — the magnitude of the gap between the commanded setpoint and
the room temperature, in the direction the compressor is driving — is binned
into four: `at_setpoint`, `close`, `working`, `pulldown`. Each is a
`Coefficient` on the same Kalman machinery as before; the pooled coefficient
stays and is what an unconverged bin falls back to. The bin an interval is
attributed to is chosen once, from the mean approach across the interval —
not re-evaluated every cycle, which would repeat the 0.8.3 fault of an
anchor replaced faster than it can mature.

The same four bins key a learned draw model per outdoor unit group,
replacing the flat `ASSUMED_UNIT_KW` constant that stood in for every room's
rated draw. When exactly one group's compressor changes state and nothing
else does, the change in house load is that group's draw — folded into the
filter weighted by how clean the observation was, never gated outright.

**These two ship together because binning the rate alone makes the energy
forecast worse.** `hold_fraction` is a duty cycle standing in for a draw; as
`k_sensible` correctly falls near setpoint the fraction saturates toward
1.0, projecting full rated draw for a room barely running. A learned draw
per bin is what turns the corrected rate into a corrected forecast rather
than a more precisely wrong one.

### Comfort inputs are required, and the comparison between drying and cooling no longer uses a derivative

**New in 0.8.9.** A temperature and a humidity sensor are required when a
room is configured — without both there is no comfort index, and without the
index this component has nothing to offer that a thermostat does not. An
existing room missing either keeps running (the fields stay nullable on the
stored config) and raises a repair issue naming which input is absent.

The choice between drying and cooling used to multiply the learned sensible
rate by the partial derivative of the comfort index at constant relative
humidity — overstating cooling's benefit by up to 64% in hot, humid
conditions, because dry bulb falling also *raises* relative humidity at
constant vapour pressure, an effect the derivative silently credited to
cooling. Both routes are now projected forward an hour and the index is
evaluated at each end directly, using a fourth learned coefficient,
`k_rh_cooling` — the room's own measured net humidity response while
cooling — where it has converged, and a constant-vapour-pressure floor where
it has not.

### Stopping is a decision, and a separate one from leaving the unit alone

**New in 0.8.7.** The actuator step carries both, as distinct values.

`OFF` commands the climate entity off: lockout, unoccupied, an opening in the
room, coasting, a deferred precondition, and a direction the unit cannot
deliver. (A fifth reason existed here from 0.8.7 to 0.8.9 — a room refused
under `no_grid_import` — retired in 0.8.10; see §3, "Tariff constraints are
absolute". Power management now throttles via a setpoint ceiling and never
produces `OFF`.)

`NONE` commands nothing, and the unit holds the trimmed setpoint it was last
given against its own sensor until the next evaluation: a room inside its band,
and a room whose reading or band is missing.

They were one value, and two places downstream guessed which was meant. The
actuator guessed from the mode, which covered lockout and unoccupied and missed
the other five — so an open window, a coasting room and a refusal under
`no_grid_import` all left the compressor running with nothing in the log. The
short-cycle guard guessed "not running", so every time a room reached its band
it recorded a stop that had not happened.

Neither could be fixed alone. The guard's wrong record was harmless only while
nothing was ever actually stopped; the moment the stop paths became real it
would have let a genuine stop through inside the minimum run time.

### Cost-minimisation at every decision point

**New in 0.8.11.** Precool was, until this build, the only mode that ever
looked at price. A room approaching the edge of its band always ran the
compressor immediately, even when the thermal model could say the band would
hold unaided for a few more minutes and a strictly cheaper tariff interval
was about to begin.

`tariff.cheaper_interval_ahead` scans the forward interval series for the
next interval with a lower price within a bounded horizon — the same one
hour `COAST_HORIZON_HOURS` already trusts for "the band holds unaided".
`coordinator._cheaper_window_imminent` combines that with the room's own
`holds_through` projection: does the band survive, unaided, until the
cheaper interval starts. Only when both are true does the mode become
`COAST`, with a trace reason distinct from the unaided-hold case.

This folds in the pool's own finding 19b — `Interval.per_kwh` was parsed and
reached no decision — as a consequence rather than a separate mechanism: once
any decision point evaluates price, the parsed figure has somewhere to go.

**Fails toward comfort exactly like the unaided-hold check it extends.** An
unconverged thermal model, a tariff series with no price on the relevant
interval, or a window the tariff plan marks as not permitting coasting at
all mean the room is corrected now — the same `coasting_permitted` flag that
already gates the unaided-hold case gates this one too.

### Solar checked first in the power budget

**New in 0.8.11.** The 0.8.10 rewrite of the power budget dropped solar from
the calculation entirely — a genuine gap in that build, not a design choice:
the pre-0.8.10 boolean veto had checked solar sufficiency directly, and
`_power_ceiling` never picked that check back up.

`power.solar_offset_kw` splits current solar generation into what the rest
of the house consumes and what is left over. The rest of the house's draw is
paid down by solar first, which shrinks how much of the battery's discharge
rate and stored energy that draw is still competing for; whatever solar
remains after that goes straight to the room being throttled, free of both
of the battery's own limits. The battery only becomes a binding constraint
at all where solar is insufficient to cover the house.

`coordinator._sustained_solar_kw` derates the instantaneous reading using
the weather forecast's clear-sky fraction — the same figure the thermal
model already uses for solar gain — across the remaining hours of the
constrained window, taking the worst ratio rather than assuming the current
moment's solar holds for a window that runs past sunset or into cloud the
forecast already expects.

### Re-asserting the commanded setpoint from live entity state

**New in 0.8.11.** There is no manual-override concept in this design — see
[Settled principles](#3-settled-principles) — but the dedupe cache that
avoids re-sending an unchanged command compared only against its own memory
of the last command sent, not against what the entity is currently
reporting. A change made at the wall, or by any other automation, could
therefore sit unreflected in the coordinator's memory for one or more
cycles before something incidental caused a re-send.

`actuator._matches_live_state` closes it: a command is skipped only when
both the coordinator's own memory *and* the entity's live reported mode and
setpoint already agree with what is about to be sent. A live divergence,
from any cause, is corrected on the next cycle — the coordinator re-asserts
its own computed decision, it does not attempt to detect or honour intent
behind the divergence.

### Grid sign zero-flow correction

**New in 0.8.11.** `power.implied_sign` and the recurring
`coordinator._check_grid_sign` both used to treat a raw grid reading of
exactly zero as directional evidence — `grid_reading_w > 0` failing meant
"exporting", regardless of how the zero arose. A `no_grid_import` window
running on battery overnight, with no export and no import, reads exactly
zero for the whole window: the single most common reading this feature
sees, misread as a sustained contradiction every time.

A reading within a small tolerance of zero (`power.NO_FLOW_BELOW_W`) is now
treated as no evidence at all, not as ambiguous evidence — there is no
direction to a flow that did not happen. The recurring check's disagreement
counter also resets on an inconclusive reading exactly as it does on an
agreeing one, so a night of correctly-inconclusive zero readings cannot
leave the counter primed to trip on the next real reading.

---

## 9. What changed from v0.3

| Area | v0.3 | v0.4 |
|---|---|---|
| Comfort index | Steadman, correct in principle | Steadman, and the inverted index actually in use was found and replaced |
| Unoccupied | Off, or a wide envelope | Off. No band exists for it |
| Preconditioning | Explicit target plus deadline | The occupied band. No separate target |
| Actuator ordering | Four steps described | Four steps reachable, direction-aware, every skip traced |
| Site data | Tariff and bands in the proposal | Configuration only. None in source |
| Sleep | Schedule assumed | A configured schedule entity, or the mode is unreachable |
| Quality scale | Not considered | Tracked against all 54 rules |
| Covers | Gated on illuminance | Gated on sun geometry — a semi-transparent blind reads bright when closed |
| Unit capabilities | Assumed | Read from the entity and fed into the decision |
| Thermal model | Proposed | Built, with sensible and latent learned separately |
| Demand forecast | Proposed | Built, vendor-neutral, published as a sensor |

---

## 10. What changed from v0.5

| Area | v0.5 | v0.8 |
|---|---|---|
| Tariff | Held here: windows, prices, feed-in, supply charge | Read from Abode Power Tariffs as a forward interval series. Nothing held |
| Prices | Cents per kWh | Dollars per kWh, the publisher's unit, unconverted |
| Layer 2 | Adopt an existing regulator | Built here: integral outer loop plus short-cycle protection |
| Setpoint | The solved target, sent as-is | The solved target plus a learned trim, both published |
| Stale sensors | Not detected | Every reading carries an age; too old is treated as absent |
| Free cooling | Not considered | Advised on dry bulb **and** dew point, published not actuated |
| Condensation | Not considered | Flagged when the setpoint nears the room dew point |
| Preconditioning | Started immediately | Starts when the model says the pull needs to start |
| Sleep transition | Stepped | Ramped over an hour |
| Domain | `hvac_coordinator` | `abode_hvac_coordinator` |
| Feels-like | Not computed | Outdoor apparent temperature, Bureau formula with wind, compared against the indoor index |
| Precool demand | Current outdoor vs indoor | Hourly forecast peak over ten hours |
| Dry vs cool | One humidity threshold | Learned rates converted to index per hour |
| Illuminance | Recorded, unused | Removed |
| Pure modules | 10 | 15 |
