# Architecture review — August 2026

Read from the source at commit `d8bbc72` (v0.8.5), not from the documentation.
Every finding cites the file and line it was found at.

**Nineteen findings. Eight are fixed in 0.8.6.** The other eleven are recorded
here and not built; several of them are not defects but the shape of a
capability the component does not yet have.

**Findings 20 to 23 were added in August 2026** by a second read of the source
at v0.8.6. Three of them are defects of the same class as 1 to 8 — the code
does something silently different from what the documentation says.

Ordered by importance, not by effort.

---

## Fixed in 0.8.6

### 1. Covers with no reported position locked the air conditioner off

`_covers_can_help` returned `True` when position was `None` (`modes.py:240`),
on the reasoning that the covers should be commanded once and the next
evaluation would see the result. The next evaluation sees the same unknown.

Choosing COVERS also turns the climate entity off for that cycle
(`actuator.py:155`). So a room whose blinds never report `current_position`
chose COVERS on every cycle the sun was on the glass, with the unit off
throughout. Nothing cleared it — with the air conditioning off the room heats,
so the demand persists. A west-facing room lost its cooling from the moment the
sun reached the glass until it left.

**Fixed:** an unknown position skips the step, with its own rejection naming
the missing reading. `mean_cover_position` returns `None` when no cover
publishes a position (`actuator.py:132`), so this is reachable on any blind
without position feedback.

### 2. Compressor state was read from the mode string, not `hvac_action`

`_compressor_direction` (`coordinator.py:1096`) used `hvac_action` only for
`heat_cool` and `auto`. For `cool` and `heat` it read the mode.

A head sitting at setpoint in `cool` reports `cool` with the compressor idle.
All of that idle time therefore counted as an interval the compressor was
driving, and `k_sensible` became an average of running and idling that
describes neither. Since a room spends most of its life near setpoint, the
dilution is severe and in one direction.

**This gated every other learning finding in the review.** Nothing measured
about the compressor could be trusted while it stood.

**Fixed:** `hvac_action` first for every mode, mode only as a fallback where
the entity publishes none, and which source answered recorded per room in
diagnostics.

Related, same cause: `observe` ran the passive update whenever compressor
direction was zero (`thermal.py:186`), and dry mode has no sensible direction.
Every drying interval was folded into `k_loss` and `k_solar` as though nothing
had been driving the room. Dry mode energises the compressor. Now excluded.

### 3. Power management failed closed

Five exits in `_power_available` returned `False` on an unknown
(`coordinator.py:1394, 1404, 1412, 1424`): a missing or stale reading, a series
that does not clear inside its horizon, no solved target yet, and an
unconverged thermal model.

An unconverged model is the state every fresh install sits in. So switching
power management on stopped the compressor in occupied rooms for the whole of a
no-import window, with a trace line blaming the battery — on the strength of a
coefficient that had never been measured. For the declared 14:00-to-midnight
no-import rule that is the entire evening.

**Fixed:** fails open on every unknown, with a debug line. `False` is returned
only for a positively computed shortfall — readings present, model converged,
relief time known, arithmetic short. Comfort is a hard constraint; a projection
the controller cannot make is not grounds for withdrawing it.

### 4. `drift_rate` silently dropped the solar term

It added the solar contribution only when `k_solar` had converged
(`thermal.py:250`) but returned a rate either way. A sunlit room with an
unconverged solar term got a drift estimate built from heat loss alone —
missing its largest contribution. `holds_through` then reported the band would
hold and the room entered COAST with the sun full on the glass.

Absence of a converged coefficient is not evidence that its contribution is
zero.

**Fixed:** returns `None` when the sun is on the room and `k_solar` is
unconverged, which every caller already treats as *do not coast*.

### 5. The short-cycle guard treated dry mode as a compressor stop

`wants = trace.actuator is ActuatorStep.COMPRESSOR` (`coordinator.py:735`).
Dry mode energises the compressor.

Two consequences. A cool-to-dry change was blocked for ten minutes as though
the compressor were shutting down, then recorded as stopped while it was in
fact running — after which the minimum *off* time blocked the return to cool,
for a stop that never happened.

Separately, a refused stop replaced the step with COMPRESSOR outright, so a
guard that exists to protect the compressor silently cancelled cover and fan
decisions that had nothing to do with it. A blind that should have closed did
not.

**Fixed:** DRY counts as running. A refused stop sets `hold_compressor` on the
trace and leaves the decision alone; the actuator skips only the parts that
would stop the compressor early.

### 6. Solar sufficiency double-counted a running unit

`solar >= house_load + rated_kw` (`coordinator.py:1385`) is correct only when
the unit is off. When it is running its draw is already inside `house_load`, so
adding the rating counted it twice and the room read as unaffordable the moment
it started — the reading that justified starting became the reading that
stopped it.

**Fixed:** headroom is added only for a unit that is currently off.

### 7. Anti-windup ran before the guard, on the wrong flag

`_regulate` preceded `_guard_cycling` (`coordinator.py:672-673`) and integrated
on `trace.actuator is COMPRESSOR` (`coordinator.py:712`) — the desired step,
before the guard had reduced it. A start the guard had just refused was
integrated against anyway.

About 0.03 °C per refusal. Small, and still exactly the condition the module's
own docstring says it excludes.

**Fixed:** guard first, then regulate, on the applied step. A compressor the
guard is holding on counts as regulating, because it is running toward the
target.

### 8. Two rooms on one climate entity fought silently

The schema permits it, `_last_climate` is keyed by room (`actuator.py:146`),
and each cache sees only its own writes. Both rooms command their own setpoint
every cycle, neither errors, and nothing appears in the log.

**Fixed:** the room form refuses a climate entity another room claims;
configurations saved before 0.8.6 have both rooms locked out on load with a
repair issue naming the entity and the rooms. A lockout rather than a
`ConfigEntryError`, so a single bad pair does not take correctly configured
rooms down with it.

A ducted system genuinely serves several rooms from one indoor unit, which is
why the configuration looks reasonable to enter. This controller cannot drive
one: its output is a dry-bulb target per room, and a ducted system has one
setpoint with a damper per zone.

---

## Recorded, not built

These are the capability, not the defects. Nothing below is in 0.8.6.

### 9. `k_sensible` has no operating-point term

It is a single coefficient with no regressor for the temperature gap or the
inverter's modulation (`thermal.py:189`), so it describes exactly one operating
point: full tilt. A room spends most of its life near setpoint, where the rate
is small, and the filter averages that with pulldown intervals into a number
describing neither.

Downstream: `hours_to_reach` (preconditioning), `energy_for` (the demand
forecast and the battery projection), and the dry-versus-cool split all rest on
it. In `energy_for`, `hold_fraction = min(drift/k_sensible, 1.0)`
(`thermal.py:359`) saturates at 1.0 as k_sensible falls, projecting full rated
draw for the whole horizon.

`_observe_sensible` also discards intervals where the room moved against the
compressor (`thermal.py:197`), truncating the noise distribution and biasing
what remains.

**Proposed:** bin by approach — the gap between commanded setpoint and room
temperature — with a rate and a draw coefficient per bin on the existing Kalman
machinery. Also conditioned on how many heads on the same outdoor unit are
calling, so solo and joint intervals do not average together.

**Settled.** Ships with finding 14, not
alone: `hold_fraction = min(drift / k_sensible, 1.0)` saturates as `k_sensible`
falls, so a correctly small near-setpoint rate pushes the projection toward
full rated draw. Finding 9 on its own makes the forecast worse. The multi-head
conditioning is deferred to a second dimension on the bin key once finding 13
exists.

### 10. Power management is a veto, not a budget

The only thing it can do is refuse the compressor. It cannot run it gently
within an allowance.

The compressor is continuously variable and the component already owns both
levers that set its output: the commanded setpoint, which is what an inverter
modulates against, and fan speed, which sets capacity. `_async_set_fan`
(`actuator.py:297`) already chooses between fan modes — but on `active`, a
question about noise.

What blocks the throttle is finding 9: with one coefficient and one assumed
draw figure, there is no operating point at which "gentler" has a rate or a
cost, so the code had nothing to ration with.

**Proposed:** the check returns a kWh budget rather than a boolean; the budget
selects an approach bin; the bin's approach becomes a ceiling on
`commanded_dry_bulb_c`.

**Settled.** Two things move with it: the
projection compares against the house total rather than each room against the
whole battery, and the regulation integrator stops winding up against the
ceiling. Fan speed is not used as a throttle — air movement is an input to the
comfort index, so the saving partly pays for itself and the interaction is
circular.

### 11. There is no grid sensor

The five power inputs are state of charge, capacity, solar, house load and
reserve margin. `grep` for grid across the package returns only the constraint
name. A constraint called `no_grid_import` is enforced against quantities that
are not grid flow, and battery discharge availability — which the inverter
decides — is invisible.

### 12. No horizon in any decision except precool

`select_actuator` is greedy per cycle. UNOCCUPIED means off, not a wider
envelope (`modes.py:266`), so the mass discharges free during the cheap window
and is paid for at peak. Cheapest-first ordering is intra-cycle cost, not
energy cost.

### 13. Multi-head systems are not modelled

Heads on one outdoor unit share a compressor. Nothing in the configuration
knows that, so there is no unit of account for a shared draw, and a room's
measured °C/hour differs depending on whether the other head is calling.

The relation is many-to-many, not a grouping key on the room: two rooms can
share one system with one head each, and one room can have two heads on one
system. `CONF_CLIMATE_ENTITY` is a single required entity
(`config_flow.py:85`) and `climate_entity_id` is a scalar at every call site,
so the second case cannot be expressed at all.

**Proposed:** a global Systems step — system name, type (single-head or
multi-head; ducted refused), its heads, and which rooms each serves. Make the
room's heads a list at the same time, while the migration is being written
anyway.

**Settled, differently.** No global step and
no Systems objects: the room's climate field becomes a multi-select, and a step
after it asks per head which outdoor unit it is on, with groups created by
earlier rooms already in the dropdown. Membership is derived from a shared
group name, which covers two rooms on one unit and two heads in one room with
one rule. No type field — how many heads a unit has is answered by how many are
named on it.

Also settled: this is a **live defect**, not only a capability gap. `MIN_RUN`
and `MIN_OFF` protect a compressor and `RegulatorState` is keyed by room
(`coordinator.py:801`), so starting guest's head while study's is running is
refused as a compressor start and stopping study's while guest's still calls is
held as a compressor stop. This is the guard's unit of account, not comfort
arbitration between rooms — that remains settled as not applicable.

### 14. Per-unit draw is one assumed constant

`ASSUMED_UNIT_KW = 1.2` (`forecast.py:46`) stands in for every room. The house
load sensor already carries the signal: when one system's compressor changes
state and nothing else does, the step change in house load is that system's
draw. Estimated continuously in the same filter — including from intervals
where several systems ran, by apportioning the residual by variance — it tracks
the plant as it ages rather than staying a nominal figure.

**Settled.** Binned by approach the same way
finding 9's rate is, and keyed by outdoor unit group rather than by room. No
accept/reject gates: every candidate observation enters the filter with a
measurement variance built from how clean it was, so nothing is discarded and a
consistent shift in a unit's real draw is tracked where a single outlier is
absorbed. Apportioning a residual across simultaneous changes is **not** built
— solo observations are sufficient and an apportionment cannot be checked
against anything.

### 15. Permitting power management to reduce comfort

**Settled as a checkbox, not a floor.** The proposal here was one number per
room:
how far above the band it may be driven before rationing stops.

That makes the user set a magnitude to answer a yes-or-no question, and the
magnitude is not theirs to set — the limit is whatever the remaining energy can
buy, which the controller already computes.

So: a checkbox per room, off by default. Off, finding 10's ceiling lifts at
band top and comfort is never traded. On, it keeps applying above band top and
the room runs at the largest output the remaining energy allows, spread across
the hours until the grid is permitted again. As the battery depletes the
ceiling tightens, so the room degrades gradually rather than falling off a
cliff at the end of the window.

**It never stops.** There is no state of charge above reserve at which the room
is abandoned. It cannot see grid flow to verify that stopping would honour the
constraint, so stopping is not a compliance action.

The inadequate-storage warning is separate and not conditional on the checkbox:
with it off, the warning is why the constraint was breached; with it on, why
the bedroom was warm. That detection is finding 16 and is built once, there.

### 16. Overnight shortfall detection

Per constrained window, record energy available, energy spent, hours clamped
and worst deficit. Three shortfall nights in fourteen raises a repair issue
naming the kWh shortfall — actionable, because the number says whether it is a
reserve setting, a battery size or a tariff rule.

### 17. The dry-versus-cool comparison uses the wrong derivative

`sensitivity_to_temperature` is the partial at constant relative humidity
(`hci.py:190`). Sensible cooling does not hold RH constant; at constant
absolute humidity the derivative is exactly 1.0.

At 25 °C and 60% RH the constant-RH figure is about 1.38, so the cooling route
is overstated by roughly 38% — against the 25% handicap deliberately given to
drying. Drying is systematically under-chosen in the one climate this model
exists for.

### 18. Solar recovery is instantaneous only

The battery projection assumes the battery carries the whole constrained
window. The clear-sky fraction is already computed in `weather.py` from a
forecast already fetched, so an hour-by-hour solar shape over the remaining
window is reachable from data in hand.

### 19. Manual override wins permanently, and price is never used

`_last_climate` records what was commanded, not what the unit reports
(`actuator.py:218`). Change the head at the wall and the coordinator never
re-asserts until its own decision changes. There is no reconciliation loop.

Separately, `Interval.per_kwh` is fetched and parsed (`tariff.py:159`) and used
by no decision. Precool depends entirely on a hand-entered
`precool_opportunity` label in the tariff plan — which is why it has never
fired on the live install.

Also noted: `PRECOOL_DEMAND_MARGIN_C` is 2.0 (`const.py:30`) while
`architecture.md` §14 states 3 °C. The 3 °C is `weather.DEMAND_MARGIN_C` and
applies only when a forecast exists; the 2.0 is the no-forecast fallback. Two
different numbers, one of them documented as the other.

---

## Sequencing

Findings 1-8 are 0.8.6 and stand on their own.

| Release | Contents | Blocked on |
|---|---|---|
| **0.8.7** | Findings 20-23 | Nothing |
| **0.8.8** | Finding 13 | 0.8.7 |
| **0.8.9** | Findings 9 and 14 | 0.8.8 **and a hardware run** |
| **0.9.0** | Findings 10 and 15 | 0.8.9, and coefficients that have moved |

Nothing in 9-19 starts until the hardware run. Finding 2 gates every
measurement the rest depends on, and none of it has been observed on a unit
that reports `hvac_action` — which finding 22 is what establishes.

Finding 13 moves ahead of 9 and 14 rather than after them, for two reasons: it
renames `climate_entity_id` to a tuple at every call site, and finding 14 has
to key its draw model to a group that does not otherwise exist.

Finding 16 follows 0.9.0.

---

## Added August 2026 — a second read of the source

### 20. `ActuatorStep.NONE` means two different things

`select_actuator` returns `NONE` for seven reasons. Two mean *leave the unit as
it is*; five mean *the unit must stop*. Nothing in the value distinguishes
them, and two places downstream guess.

**The actuator** infers a stop from the mode (`actuator.py:167-173`), which
catches lockout and unoccupied. The other three leave the climate entity
exactly as last commanded, and `_last_climate` (`actuator.py:232`) suppresses
any re-send:

| Reason | Where | Today |
|---|---|---|
| An opening is open | `modes.py:283` | Compressor keeps cooling, window open |
| — and once fixed | | Stopping instantly would cycle the compressor for a twenty-second door, so the stop is debounced two minutes while the interlock still fires at once |
| Coasting | `modes.py:287` | The band is held by running |
| Preconditioning deferred | `modes.py:291` | The deferral does nothing |
| `power_available` false | `modes.py:360, 402` | The refusal never reaches the hardware |
| Direction not correctable | `modes.py:357, 399` | A cooling-only unit keeps cooling a cold room |

The open-window case is unbounded and silent. The `no_grid_import` case is the
only mechanism holding the constraint and it does nothing at all.

**The guard** derives `wants` from the step (`coordinator.py:807`) and resolves
the ambiguity as "no". Every time a room reaches its band it records a stop
(`coordinator.py:822`) while the compressor is still running — the most common
state the controller is in.

Harmless today, because the refusal downgrades to `NONE` and `NONE` does
nothing. Correct the actuator alone and the first thing the new stop paths do
is short-cycle the compressor past a guard that has been told the wrong thing.

**Settled and fixed in 0.8.7:** `ActuatorStep` gains `OFF`. `wants` becomes
running for COMPRESSOR and DRY, not running for OFF, and unchanged for NONE. A
missing reading holds rather than stops, on finding 3's reasoning. The stop is
debounced two minutes for an opening; the interlock itself is not.

### 21. `action_sources()` has no caller

`coordinator.py:1202`. Defined, documented, and called by nothing — `grep`
returns the definition and nothing that calls it.

It establishes whether the Intesis adapter publishes `hvac_action`, which is
finding 2, which gates every measurement findings 9 to 19 depend on.

**Settled:** a top-level key in `diagnostics.py`, plus one INFO line the first
time a room falls back to `hvac_mode`. Diagnostics is pull-only; this reading
is worth exactly once, at install.

### 22. Heat load and air movement are unreachable configuration

`coordinator.py:1779-1780` reads them, `forms.py:193-194` prints them,
`strings.json` labels them — and neither appears in `ROOM_SCHEMA`
(`config_flow.py:82-115`) or `room_from_input`.

`HEAT_LOAD_HCI` (`hci.py:120`) has therefore never applied to any room, and the
office — a workstation against a west wall — is the room it was written for.
Air movement falls back to the climate entity's own state, so a ceiling fan
running with the air conditioner off reads as still air.

**Settled:** two optional entity selectors and two lines in `room_from_input`.
No version bump and no migration; the keys are optional and absent already
reads as `None`.

### 23. The demand forecast uses the same shortcut as the actuator

`_build_forecast` (`coordinator.py:1240`) decides whether a room contributes
energy with `trace.mode not in (Mode.LOCKOUT, Mode.UNOCCUPIED)` — finding 20's
hole in a second place. A room stopped for an open window projects a full
horizon of draw.

**Settled:** `will_run` follows the actuator verdict.
