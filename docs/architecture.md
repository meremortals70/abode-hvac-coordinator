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

Precool is the only genuinely price-driven mode, and it only ever makes a room
more comfortable.

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

**`no_grid_import` is still absolute — what changed is how the controller
decides whether it is currently satisfied.** This project has no control over
where household power comes from, so it cannot enforce the constraint
directly. What it can do is estimate its own projected need and read the
battery's state of charge, then decide whether a given room may keep running
without importing. That decision is optional (nothing engages until battery,
solar and house-load readings are all configured — see
[Configuration](configuration.md#power)), it is assessed per room against
shared live readings rather than negotiated between rooms, and it is checked
inline inside the same actuator selection that already checks whether the
unit can physically heat or cool — not as an override layered on afterward.

**It fails open.** A room is refused only on a positively computed shortfall:
readings present, model converged, relief time known, and the arithmetic
short. Every case the controller cannot answer leaves the room its comfort.
Before 0.8.6 all four unknowns — a stale reading, a series that never clears,
no solved target yet, an unconverged thermal model — returned a refusal, and
since an unconverged model is the state every fresh install sits in, enabling
power management stopped the compressor in occupied rooms for a whole
no-import window on the strength of a coefficient that had never been
measured. Comfort is a hard constraint, and a projection the controller cannot
make is not grounds for withdrawing it.
See [Tariff](tariff.md#no_grid_import-what-it-actually-does).

### One writer per actuator

| Actuator | Owner |
|---|---|
| Battery | Whoever owns the battery. **Never this project** — this project only ever reads state of charge, to decide whether a room may keep running under `no_grid_import` |
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
| Power-aware compressor decision (`no_grid_import`) | Built and tested. The coordinator wiring has run against Home Assistant since 0.8.6 — against 2025.1.4, not the targeted 2026.8.x |
| Per-room cover-control override | Built, tested |

Since 0.8.6 the whole suite runs against a real Home Assistant, not only the
pure modules — 324 tests, against 2025.1.4 rather than the 2026.8.x targeted,
because the build sandbox is Python 3.12. Running it for the first time found
two tests that had never passed. See
[Known limitations](known-limitations.md) and
[the review](architecture-review-2026-08.md).

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
