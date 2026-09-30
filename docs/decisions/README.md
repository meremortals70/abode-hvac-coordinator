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

Anchors checked against 0.8.12 (`50e6abf`), 2026-10-01.

### Origin

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [000](000-origin-and-purpose.md) | Origin and purpose | Accepted | The project brief | Conforms |

### Foundations

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [001](001-four-layer-model.md) | Four layers, and Layers 2 and 3 only see a climate entity | Accepted | Design v0.5 | Conforms |
| [002](002-decisions-in-pure-modules.md) | Decisions live in pure modules; the coordinator gathers and acts | Accepted | Design v0.4 | Partly |
| [003](003-build-layer-2-here.md) | Build the regulation layer here instead of adopting a regulator | Accepted | Design v0.8, replacing the v0.5 plan | Conforms |
| [004](004-tariff-lives-elsewhere.md) | The tariff lives in Abode Power Tariffs; this integration only reads it | Accepted | Design v0.8, replacing the v0.5 in-house tariff | Conforms |
| [005](005-one-writer-per-actuator.md) | One writer per actuator; never write the battery | Accepted | Design v0.4 | Conforms |
| [006](006-decision-trace-is-mandatory.md) | Every room publishes why it is doing what it is doing | Accepted | Design v0.4 | Conforms |
| [007](007-no-manual-override.md) | No manual override; re-assert from the entity's live state | Accepted | Finding 19a | Conforms |

### Comfort

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [008](008-comfort-is-the-constraint.md) | Comfort is the constraint, not the variable | Accepted | Design v0.4 | Conforms |
| [009](009-one-comfort-definition-the-band.md) | One comfort definition per room: a band in comfort index | Accepted | Design v0.4 | Conforms |
| [010](010-steadman-comfort-index.md) | The comfort index is Steadman shaded apparent temperature, wind zero | Accepted | Design v0.4, replacing the index in use at v0.3 | Conforms |
| [011](011-unoccupied-wide-band.md) | An unoccupied room holds a wide band | Superseded by DR-012 | Design v0.3 | Superseded |
| [012](012-unoccupied-is-off.md) | An unoccupied room is off | Accepted | Design v0.4, superseding DR-011 | Conforms |
| [013](013-precondition-to-the-occupied-band.md) | Preconditioning drives to the occupied band, and starts when the model says | Accepted | Design v0.4 and v0.8 | Conforms |
| [014](014-sleep-ramps-over-an-hour.md) | The change into the sleep band ramps over an hour | Accepted | Design v0.8 | Conforms |

### Control

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [015](015-integral-only-outer-loop.md) | The outer loop is integral-only, and does not wind up against a limit | Accepted | Design v0.8; finding 7 | Conforms |
| [016](016-short-cycle-guard-per-outdoor-unit.md) | Short-cycle protection is 10 minutes on, 5 off, per outdoor unit | Accepted | Design v0.8; findings 5 and 13 | Conforms |
| [017](017-stop-and-leave-alone-are-separate.md) | Stopping the unit and leaving it alone are separate decisions | Accepted | Finding 20 | Conforms |
| [018](018-missing-reading-leaves-the-unit-alone.md) | A missing reading leaves the unit alone | Accepted | Finding 20, on finding 3's reasoning | Conforms |
| [019](019-openings-stop-after-a-debounce.md) | An open window or door stops the unit after two minutes | Accepted | Finding 20 | Conforms |
| [020](020-heads-and-outdoor-units.md) | A room has heads, and heads sit on named outdoor units | Accepted | Finding 13 | Conforms |
| [021](021-lockout.md) | Lockout takes a room out of control, set by the user or forced by a conflict | Accepted | First release; finding 8 | Conforms |

### Actuation

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [022](022-actuator-ordering.md) | Cheapest first: covers, fan, dry, compressor, every skip traced | Accepted | Design v0.4; finding 1 | Conforms |
| [023](023-covers-gated-on-sun-geometry.md) | Covers are gated on sun geometry, not light level | Accepted | Design v0.4 | Conforms |
| [024](024-send-only-what-is-advertised.md) | Send the unit only what it says it can do | Accepted | Design v0.4 | Conforms |

### Learning

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [025](025-learned-thermal-model.md) | A learned per-room thermal model, with hysteresis until it converges | Accepted | Design v0.4, after RoomMind; findings 2 and 4 | Conforms |
| [026](026-sensible-and-latent-separately.md) | Sensible and latent load are learned separately | Accepted | Design v0.4; the addition to RoomMind's approach | Conforms |
| [027](027-humidity-threshold-dry-mode.md) | Choose dry mode on a relative humidity threshold | Superseded by DR-029 | Design v0.5 | Superseded; kept as fallback |
| [028](028-approach-bins-and-learned-draw.md) | Rate and draw are learned per operating point, and ship together | Accepted | Findings 9, 13 and 14 | Conforms |
| [029](029-dry-versus-cool-by-projection.md) | Dry versus cool is decided by projecting both routes | Accepted | Design v0.8; finding 17 | Conforms |
| [030](030-stale-feeds-are-absent.md) | A reading that is too old is treated as absent | Accepted | Design v0.8 | Conforms |
| [031](031-model-persisted-trim-not.md) | The learned model is persisted; the regulation trim is not | Accepted | Design v0.8; architecture review 2026-08-23 | Conforms |

### Modes

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [032](032-coast-on-a-one-hour-hold.md) | A room coasts when the model says its band holds for an hour | Accepted | Design v0.4 | Conforms |
| [033](033-precool-ignores-occupancy.md) | Precool ignores present occupancy and runs on the weather forecast | Accepted | Design v0.4 and v0.8 | Conforms |

### Tariff and cost

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [034](034-tariff-constraints-are-absolute.md) | Tariff constraints are declared, absolute, and reported if unknown | Accepted; amended by DR-041 for `no_grid_import` | Design v0.4 | Conforms |
| [035](035-cost-minimised-at-every-decision-point.md) | Every decision point looks for the cheapest way to deliver the band | Accepted | Findings 12 and 19b | Conforms |

### Power

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [036](036-boolean-power-veto.md) | Refuse the compressor when the battery cannot cover a no-import window | Superseded by DR-037 | 0.8.5 | Dead code remains |
| [037](037-power-budget-setpoint-ceiling.md) | A no-import window is met with a setpoint ceiling, never a stop | Accepted; gated per room by DR-041 | Finding 10, superseding DR-036 | Does not conform on precool |
| [038](038-grid-sign-settled-at-setup.md) | The grid sensor's sign is settled at setup and never inferred again | Accepted | Finding 11 | Conforms |
| [039](039-solar-checked-first.md) | Solar pays down the house first; the battery binds only on the shortfall | Accepted | Finding 18 | Conforms |
| [040](040-comfort-reduction-checkbox.md) | A per-room checkbox permits power management to reduce comfort | Superseded by DR-041 | Finding 15 | Superseded |
| [041](041-power-management-off-guidance-enforced.md) | Power management per room is off, guidance or enforced | Accepted | Instruction, 2026-08-24, superseding DR-040 | Does not conform on precool |
| [042](042-battery-discharge-is-a-setting.md) | The battery's maximum discharge is a setup figure, not learned | Accepted | Finding 10 build | Conforms |
| [043](043-shortfall-measured-afterwards.md) | A no-import window's actual grid import is measured and reported afterwards | Accepted | Finding 16 | Does not conform |
| [044](044-power-management-fails-open.md) | Power management fails open | Accepted | Finding 3 | Conforms |

### Configuration

| DR | Decision | Status | Origin | At 0.8.12 |
|---|---|---|---|---|
| [045](045-configuration-discipline.md) | A setting exists only if a correct result is impossible without it | Accepted | Design v0.4, in reaction to Dual Smart Thermostat | Conforms |
| [046](046-comfort-inputs-required.md) | A room needs a temperature and a humidity sensor | Accepted | 0.8.9 build | Conforms |

## Not yet recorded

Decisions visible in the code that do not have a record yet:

- Free cooling and condensation risk are advised and published, never
  actuated.
- Presence grace periods, and the spoken warning before a room switches off.
- The heading-home and clear-override actions.
- The demand forecast horizon (eight hours) and its use of the mean forecast
  temperature.
