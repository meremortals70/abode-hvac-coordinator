# Architecture review — August 2026

Read from the source at commit `d8bbc72` (v0.8.5), not from the documentation.
Every finding cites the file and line it was found at.

**Nineteen findings. Eight are fixed in 0.8.6.** The other eleven are recorded
here and not built; several of them are not defects but the shape of a
capability the component does not yet have.

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

### 14. Per-unit draw is one assumed constant

`ASSUMED_UNIT_KW = 1.2` (`forecast.py:46`) stands in for every room. The house
load sensor already carries the signal: when one system's compressor changes
state and nothing else does, the step change in house load is that system's
draw. Estimated continuously in the same filter — including from intervals
where several systems ran, by apportioning the residual by variance — it tracks
the plant as it ages rather than staying a nominal figure.

### 15. Comfort floor, in place of a run-or-don't switch

One number: how far above the occupied band a room may be driven before
rationing stops. Zero means power management never touches comfort; high means
power is the deciding factor; everything between is the graceful middle. At the
floor with the battery still short, the controller holds at the floor, keeps
running and notifies. It does not break the constraint — it cannot see grid
flow to verify such a decision.

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

Findings 1–8 are 0.8.6 and stand on their own. Nothing in 9–19 should start
until 0.8.6 has run on real hardware long enough for the coefficients to move —
finding 2 gates every measurement the rest depends on, and none of it has been
observed yet on a unit that reports `hvac_action`.

The proposed order after that is: finding 9 (binned coefficients, no behaviour
change), then 14 and 13 (learn draw; the systems model that makes draw
attributable), then 10 and 15 (the budget and the floor), then 16.
