# Decision records

Every significant decision behind this integration, one per file. The rest of
`docs/` explains how the integration behaves. This folder explains why it
behaves that way, including the options that lost.

## How a record is laid out

Each record has the same six parts, in this order ([template](TEMPLATE.md)):

1. **Header table** - status, the build that shipped it, where it came from,
   related records.
2. **Decision** - one or two plain sentences.
3. **Why** - the situation, defect or instruction that forced it.
4. **Rejected** - each alternative and the reason it lost.
5. **Consequences** - what it makes easier or harder.
6. **In the code** - file and line anchors, and whether the current build
   actually does what the record says.

## Rules

- **The decision is frozen.** Decision, Why, Rejected and Consequences are
  never edited once accepted. A changed decision is a new record, and the old
  record's status becomes "Superseded by DR-NNN".
- **Superseded records stay.** They keep the losing arguments. A decision
  that is reversed without its history tends to be made again.
- **"In the code" is the one living section.** Code moves; the record does
  not. After every build, each record's anchors are re-checked and its
  conformance line is updated to that build. A record whose code no longer
  matches is marked **Partly** or **Does not conform**, and the gap goes on the
  fix list. It is never quietly rewritten to match the code.
- **Anchors are checked by script, not by eye.** Each anchor names a file,
  a line or range, and a symbol that must appear there.
- **Numbers are permanent.** Records are numbered in order of area, not date.
  A new record takes the next free number and joins its area in the index.
- **"Finding N"** in code comments refers to the architecture review
  registers kept during development. The Origin column below maps each one to
  its record.

## Index

Anchors checked against 0.9.7, 2026-10-15.

### Origin

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [000](000-origin-and-purpose.md) | Origin and purpose | Accepted | The project brief | Conforms |

### Foundations

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [001](001-four-layer-model.md) | Four layers, and Layers 2 and 3 only see a climate entity | Accepted | Design v0.5 | Conforms |
| [002](002-decisions-in-pure-modules.md) | Decisions live in pure modules; the coordinator gathers and acts | Accepted | Design v0.4 | Conforms |
| [003](003-build-layer-2-here.md) | Build the regulation layer here instead of adopting a regulator | Accepted | Design v0.8, replacing the v0.5 plan | Conforms |
| [004](004-tariff-lives-elsewhere.md) | The tariff lives in Abode Power Tariffs; this integration only reads it | Accepted | Design v0.8, replacing the v0.5 in-house tariff | Conforms |
| [005](005-one-writer-per-actuator.md) | One writer per actuator; never write the battery | Accepted | Design v0.4 | Conforms |
| [006](006-decision-trace-is-mandatory.md) | Every room publishes why it is doing what it is doing | Accepted | Design v0.4 | Conforms |
| [007](007-no-manual-override.md) | No manual override; re-assert from the entity's live state | Accepted; amended by DR-047, then DR-049 | Finding 19a | Conforms |

### Comfort

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [008](008-comfort-is-the-constraint.md) | Comfort is the constraint, not the variable | Accepted | Design v0.4 | Conforms |
| [009](009-one-comfort-definition-the-band.md) | One comfort definition per room: a band in comfort index | Accepted | Design v0.4 | Conforms |
| [010](010-steadman-comfort-index.md) | The comfort index is Steadman shaded apparent temperature, wind zero | Accepted; amended by DR-055 | Design v0.4, replacing the index in use at v0.3 | Conforms |
| [011](011-unoccupied-wide-band.md) | An unoccupied room holds a wide band | Superseded by DR-012 | Design v0.3 | Superseded |
| [012](012-unoccupied-is-off.md) | An unoccupied room is off | Accepted; amended by DR-071; amended by DR-077; amended by DR-082 | Design v0.4, superseding DR-011 | Conforms as amended by DR-071 and DR-077 and DR-082 |
| [082](082-no-wait-for-an-unheard-warning.md) | An empty room waits for its warning only where a warning can be said | Accepted; amends DR-012 | Review, 2026-10-14 | Conforms |
| [013](013-precondition-to-the-occupied-band.md) | Preconditioning drives to the occupied band, and starts when the model says | Accepted; amended by DR-080 | Design v0.4 and v0.8 | Conforms as amended by DR-080 |
| [080](080-heading-home-ends-by-itself.md) | A heading-home request ends once the room is occupied, or an hour after its deadline | Accepted; amends DR-013 | Review, 2026-10-14 | Conforms |
| [014](014-sleep-ramps-over-an-hour.md) | The change into the sleep band ramps over an hour | Accepted; amended by DR-084 | Design v0.8 | Conforms as amended by DR-084 |
| [055](055-the-index-ignores-the-units-own-air.md) | The comfort index is never changed by what the component itself is doing | Accepted; amends DR-010 | Incident, 2026-10-04 | Conforms |
| [058](058-comfort-mode-per-room.md) | Each room has a Comfort mode: Schedule, Sleep or Awake | Accepted; amends DR-014; amended by DR-079, then DR-083 | Instruction, 2026-10-11 | Conforms as amended by DR-083 |
| [079](079-sleep-ends-in-the-morning.md) | Sleep chosen by hand ends when the sleep schedule finishes, or at sunrise | Superseded by DR-083 | Review, 2026-10-14 | Superseded; code removed |
| [083](083-the-owners-comfort-mode-stands.md) | A Comfort mode chosen by the owner stays until the owner changes it | Accepted; supersedes DR-079, amends DR-058 | Instruction, 2026-10-15 | Conforms |
| [084](084-the-band-ramps-for-the-whole-hour.md) | A change of band ramps across the whole hour | Accepted; amends DR-014 | Review, 2026-10-15 | Conforms |
| [085](085-no-heat-on-the-way-out-of-sleep.md) | A room coming out of Sleep is not heated up to its daytime band | Accepted | Incident, living room, 2026-10-15 | Conforms |
| [092](092-a-band-change-is-known-to-the-evaluation-that-sees-it.md) | The evaluation that sees a band change decides knowing it | Accepted; amends DR-084 and DR-085 | Review, 2026-10-15 | Conforms |
| [088](088-a-leave-button-per-room.md) | Each room has a Leave button | Accepted | Instruction, 2026-10-15 | Conforms |

### Control

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [015](015-integral-only-outer-loop.md) | The outer loop is integral-only, and does not wind up against a limit | Superseded by DR-054 | Design v0.8; finding 7 | Superseded; its integrator is DR-054's integral term |
| [016](016-short-cycle-guard-per-outdoor-unit.md) | Short-cycle protection is 10 minutes on, 5 off, per outdoor unit | Accepted | Design v0.8; findings 5 and 13 | Conforms |
| [017](017-stop-and-leave-alone-are-separate.md) | Stopping the unit and leaving it alone are separate decisions | Accepted; coasting no longer stops the unit, DR-057 | Finding 20 | Conforms |
| [018](018-missing-reading-leaves-the-unit-alone.md) | A missing reading leaves the unit alone | Accepted; amended by DR-074 | Finding 20, on finding 3's reasoning | Conforms as amended by DR-074 |
| [019](019-openings-stop-after-a-debounce.md) | An open window or door stops the unit after two minutes | Superseded by DR-050 | Finding 20 | Superseded |
| [020](020-heads-and-outdoor-units.md) | A room has heads, and heads sit on named outdoor units | Accepted | Finding 13 | Conforms |
| [021](021-lockout.md) | Lockout takes a room out of control, set by the user or forced by a conflict | Accepted | First release; finding 8 | Conforms |
| [047](047-automatic-control-switch.md) | A room's Automatic control switch holds its unit off | Superseded by DR-049 | Instruction, 2026-10-01 | Superseded |
| [049](049-automatic-control-means-automation-only.md) | Automatic control off stops the automation and leaves the unit alone | Accepted; supersedes DR-047, amends DR-007; amended by DR-089 | Instruction, 2026-10-03 | Conforms as amended by DR-089 |
| [089](089-automatic-control-off-turns-the-unit-off-once.md) | Switching Automatic control off turns the unit off, once | Accepted; amends DR-049 | Instruction, 2026-10-15 | Conforms |
| [050](050-opening-grace-and-warnings.md) | An open window or door has a per-room grace and spoken warnings before the unit stops | Accepted; supersedes DR-019; amended by DR-069; amended by DR-073 and DR-077 | Instruction, 2026-10-03 | Conforms as amended by DR-069 and DR-073 and DR-077 |
| [069](069-spoken-warnings-through-a-tts-engine.md) | Spoken warnings go through one text-to-speech engine, and a warning that fails never stops an evaluation | Accepted; amends DR-050; amended by DR-071 | Incident, Office, 2026-10-13 | Conforms as amended by DR-071 |
| [071](071-a-room-speaks-only-while-run-and-awake.md) | A room speaks only while the coordinator runs it and the room is awake | Accepted; amends DR-012 and DR-069; amended by DR-077 | Incident, Office, 2026-10-13 | Conforms as amended by DR-077 |
| [073](073-free-cooling-before-the-compressor.md) | When outdoor air is better, an occupied room is not started, and an opening made for it stops the unit at once | Accepted; amends DR-050 | Incident, Office, 2026-10-14 | Conforms |
| [074](074-the-units-own-reading-stands-in.md) | Where a room's sensor is unavailable, the unit's own reading stands in | Accepted; amends DR-018 | Incident, living room, 2026-10-13 | Conforms |
| [077](077-a-warning-is-said-only-when-true.md) | A warning is said only when it is true | Accepted; amends DR-012, DR-050 and DR-071 | Incident, Office, 2026-10-14 | Conforms |
| [051](051-commanded-setpoint-is-rounded-to-the-unit.md) | The commanded setpoint is rounded to what the unit can hold | Accepted; amended by DR-065 | Incident, 2026-10-03 | Conforms as amended by DR-065 |
| [054](054-room-loop-is-a-model-based-pid.md) | The room loop is a PID that uses what the room has learned | Accepted; supersedes DR-015; amended by DR-056, DR-075, DR-086 and DR-097 | Instruction, 2026-10-03 | Conforms as amended by DR-075, DR-086 and DR-097 |
| [056](056-the-loop-asks-for-what-the-unit-can-deliver.md) | The room loop asks for what the unit can deliver, and walks its setpoint back | Accepted; amends DR-054; amended by DR-091 and DR-097 | Incident, 2026-10-04 | Conforms as amended by DR-091 and DR-097 |
| [065](065-rounding-follows-the-direction.md) | The commanded setpoint rounds down when cooling and up when heating | Accepted; amends DR-051; amended by DR-075 | Living room review, 2026-10-12 | Conforms as amended by DR-075 |
| [075](075-the-room-loop-turns-round-on-its-rate.md) | The room loop changes the way it works only when the rate it needs has clearly changed sign | Accepted; amends DR-054; amended by DR-094 | Incident, living room, 2026-10-13 | Conforms as amended by DR-094 |
| [086](086-the-pid-rate-is-a-trend-and-the-setpoint-has-a-deadband.md) | The room loop's rate is a 15-minute trend, and a setpoint sent stands through three quarters of a step | Accepted; amends DR-054 | Review, living room, 2026-10-15 | Conforms |
| [094](094-the-fallback-loop-turns-round-only-well-past-target.md) | The fallback loop changes direction only half a degree past its target | Accepted; amends DR-075 | Review, 2026-10-15 | Conforms |
| [095](095-the-units-sensor-is-measured-against-the-rooms.md) | The unit's own sensor is measured against the room's and allowed for | Accepted; amends DR-054 | Review, 2026-10-15 | Conforms |
| [097](097-the-band-is-held-on-the-units-thermostat.md) | Inside its band the unit's thermostat holds the target, and the trim is learned there | Accepted; amends DR-054 and DR-056 | Review, 2026-10-15 | Conforms |

### Actuation

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [022](022-actuator-ordering.md) | Cheapest first: covers, fan, dry, compressor, every skip traced | Accepted; amended by DR-072 | Design v0.4; finding 1 | Conforms as amended by DR-072 |
| [023](023-covers-gated-on-sun-geometry.md) | Covers are gated on sun geometry, not light level | Accepted | Design v0.4 | Conforms |
| [024](024-send-only-what-is-advertised.md) | Send the unit only what it says it can do | Accepted; amended by DR-048; amended by DR-078 | Design v0.4 | Conforms as amended by DR-078 |
| [072](072-no-fan-halfway-down.md) | A unit bringing a room down is not put on its fan short of the band | Accepted; amends DR-022 | Incident, living room, 2026-10-14 | Conforms |
| [091](091-in-band-the-unit-leaves-a-mode-whose-reason-has-passed.md) | Inside its band, a unit leaves a mode whose reason has passed | Accepted; amends DR-056 | Review, 2026-10-15 | Conforms |
| [078](078-dry-takes-the-rooms-setpoint.md) | Dry mode is sent with the room's setpoint and held to it | Accepted; amends DR-024 | Operational review, 2026-10-14 | Conforms |
| [048](048-capability-profile-read-at-config.md) | What a room's units can do is read when the room is configured | Accepted; amends DR-024 | Instruction, 2026-10-03 | Conforms |
| [052](052-every-command-sent-is-recorded.md) | Every mode or setpoint command the component sends is recorded | Accepted | Incident, 2026-10-03 | Conforms |

### Learning

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [025](025-learned-thermal-model.md) | A learned per-room thermal model, with hysteresis until it converges | Accepted; amended by DR-060 | Design v0.4, after RoomMind; findings 2 and 4 | Conforms as amended by DR-060 |
| [026](026-sensible-and-latent-separately.md) | Sensible and latent load are learned separately | Accepted | Design v0.4; the addition to RoomMind's approach | Conforms |
| [027](027-humidity-threshold-dry-mode.md) | Choose dry mode on a relative humidity threshold | Superseded by DR-029; amended by DR-076 | Design v0.5 | Superseded; kept as fallback |
| [028](028-approach-bins-and-learned-draw.md) | Rate and draw are learned per operating point, and ship together | Accepted; amended by DR-081 | Findings 9, 13 and 14 | Conforms as amended by DR-081 |
| [081](081-learning-uses-the-units-setpoint.md) | The model learns against the setpoint the unit reports | Accepted; amends DR-028 | Review, 2026-10-14 | Conforms |
| [029](029-dry-versus-cool-by-projection.md) | Dry versus cool is decided by projecting both routes | Accepted; amended by DR-076 | Design v0.8; finding 17 | Conforms as amended by DR-076 |
| [076](076-dry-or-cool-holds-its-route.md) | Dry or cool holds its route, and an unconverged room well above its band is cooled | Accepted; amends DR-027 and DR-029; amended by DR-090 | Incident, living room, 2026-10-13 | Conforms as amended by DR-090 |
| [090](090-a-route-once-chosen-is-kept-and-not-moved-by-its-own-command.md) | A route, once chosen, is kept for 20 minutes, and the comparison is not moved by the component's own command | Accepted; amends DR-076 | Incident, living room, 2026-10-15 | Conforms |
| [030](030-stale-feeds-are-absent.md) | A reading that is too old is treated as absent | Accepted; amended by DR-066 and DR-070 | Design v0.8 | Conforms as amended by DR-066 and DR-070 |
| [066](066-staleness-from-last-reported.md) | A reading's age is measured from when it was last reported | Accepted; amends DR-030; amended by DR-070 | Living room review, 2026-10-12 | Conforms as amended by DR-070 |
| [070](070-room-readings-have-no-age-limit.md) | A room's temperature and humidity are trusted until they are unavailable | Accepted; amends DR-030 and DR-066 | Incident, living room, 2026-10-13 | Conforms |
| [087](087-presence-and-contacts-have-no-age-limit.md) | Presence and door or window sensors have no age limit | Accepted | Review, 2026-10-15 | Conforms |
| [031](031-model-persisted-trim-not.md) | The learned model is persisted; the regulation trim is not | Accepted; amended by DR-064; amended by DR-068 | Design v0.8; architecture review 2026-08-23 | Conforms as amended by DR-064 and DR-068 |
| [064](064-store-saves-and-flushes.md) | Learned state is saved on one pending timer, choices at once, and the store is flushed on unload | Accepted; amends DR-031 | Living room review, 2026-10-12 | Conforms |
| [068](068-occupancy-survives-a-restart.md) | A room's occupancy is kept across a restart | Accepted; amends DR-031 | Incident, Office, 2026-10-12 | Conforms |

### Modes

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [032](032-coast-on-a-one-hour-hold.md) | A room coasts when the model says its band holds for an hour | Accepted; amended by DR-053 and DR-098; coast no longer commands the unit off, DR-057 | Design v0.4 | Partly; see DR-053, DR-057 and DR-098 |
| [053](053-coast-when-the-room-will-return-unaided.md) | A room coasts when its own physics will bring it back into band | Accepted; amends DR-032; amended by DR-060 | Instruction, 2026-10-03 | Conforms as amended by DR-060 |
| [057](057-coasting-is-fan-only.md) | Coasting is the compressor off and the unit on its fan | Accepted; supersedes part of DR-032; amended by DR-059 | Instruction, 2026-10-04 | Conforms as amended by DR-059 |
| [059](059-a-stop-follows-the-rooms-humidity.md) | When the compressor stops, a humid room dries and a dry room stays on its fan | Accepted; amends DR-057; amended by DR-093 | Instruction, 2026-10-12 | Conforms as amended by DR-093 |
| [093](093-no-drying-after-heating.md) | A coast after heating does not dry | Accepted; amends DR-059 | Incident, living room, 2026-10-15 | Conforms |
| [098](098-a-working-unit-coasts-only-at-target.md) | A room the unit is working on coasts only once it reaches its target | Accepted; amends DR-032 | Review, 2026-10-15 | Conforms |
| [060](060-coast-counts-the-coils-moisture.md) | A coast counts the moisture the coil gives back, and the passive model does not learn from it | Accepted; amends DR-053 and DR-025 | Living room review, 2026-10-12 | Conforms |
| [033](033-precool-ignores-occupancy.md) | Precool ignores present occupancy and runs on the weather forecast | Accepted | Design v0.4 and v0.8 | Conforms |

### Tariff and cost

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [034](034-tariff-constraints-are-absolute.md) | Tariff constraints are declared, absolute, and reported if unknown | Accepted; amended by DR-041 for `no_grid_import` | Design v0.4 | Conforms |
| [035](035-cost-minimised-at-every-decision-point.md) | Every decision point looks for the cheapest way to deliver the band | Accepted | Findings 12 and 19b | Conforms |

### Power

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [036](036-boolean-power-veto.md) | Refuse the compressor when the battery cannot cover a no-import window | Superseded by DR-037 | 0.8.5 | Superseded |
| [037](037-power-budget-setpoint-ceiling.md) | A no-import window is met with a setpoint ceiling, never a stop | Accepted; gated per room by DR-041; amended by DR-062 and DR-063; amended by DR-067 | Finding 10, superseding DR-036 | Conforms as amended by DR-062 and DR-063 and DR-067 |
| [038](038-grid-sign-settled-at-setup.md) | The grid sensor's sign is settled at setup and never inferred again | Accepted | Finding 11 | Conforms |
| [039](039-solar-checked-first.md) | Solar pays down the house first; the battery binds only on the shortfall | Accepted; amended by DR-061; amended by DR-067 | Finding 18 | Conforms as amended by DR-061 and DR-067 |
| [040](040-comfort-reduction-checkbox.md) | A per-room checkbox permits power management to reduce comfort | Superseded by DR-041 | Finding 15 | Superseded |
| [041](041-power-management-off-guidance-enforced.md) | Power management per room is off, guidance or enforced | Accepted; amended by DR-063 | Instruction, 2026-08-24, superseding DR-040 | Conforms as amended by DR-063 |
| [042](042-battery-discharge-is-a-setting.md) | The battery's maximum discharge is a setup figure, not learned | Accepted | Finding 10 build | Conforms |
| [043](043-shortfall-measured-afterwards.md) | A no-import window's actual grid import is measured and reported afterwards | Accepted | Finding 16 | Conforms |
| [044](044-power-management-fails-open.md) | Power management fails open | Accepted | Finding 3 | Conforms |
| [061](061-forecast-solar-in-the-budget.md) | The power budget counts the day's forecast solar, from house-level forecast sources | Accepted; amends DR-039 | Instruction, 2026-10-12 | Conforms |
| [062](062-power-ceiling-hysteresis.md) | The power ceiling drops at once and rises only when the allowance is clear | Accepted; amends DR-037; amended by DR-067 | Living room review, 2026-10-12 | Conforms as amended by DR-067 |
| [063](063-enforced-may-turn-the-unit-off.md) | Enforced power management may turn the unit off; guidance never does | Accepted; amends DR-037 and DR-041; amended by DR-067 | Instruction, 2026-10-12 | Conforms as amended by DR-067 |
| [067](067-the-budget-measures-the-rest-of-the-house.md) | The power budget measures the rest of the house, averages its readings, and waits before turning a room off | Accepted; amends DR-037, DR-039, DR-062 and DR-063 | Incident, Office, 2026-10-12 | Conforms |
| [096](096-the-trace-says-no-limit.md) | The power trace says "no limit" where there is none | Accepted | Live trace, living room, 2026-10-15 | Conforms |

### Configuration

| DR | Decision | Status | Origin | At 0.9.7 |
|---|---|---|---|---|
| [045](045-configuration-discipline.md) | A setting exists only if a correct result is impossible without it | Accepted; set aside for two settings by DR-049 and DR-050 | Design v0.4, in reaction to Dual Smart Thermostat | Conforms |
| [046](046-comfort-inputs-required.md) | A room needs a temperature and a humidity sensor | Accepted | 0.8.9 build | Conforms |

## Not yet recorded

Decisions visible in the code that do not have a record yet:

- Free cooling and condensation risk are advised and published, never
  actuated.
- Presence grace periods, and the spoken warning before a room switches off.
- The heading-home and clear-override actions.
- The demand forecast horizon (eight hours) and its use of the mean forecast
  temperature.
