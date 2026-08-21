# Handover

**Version 0.8.6. The architecture is complete.**

Every gap identified against the v0.3 proposal at v0.5.3 is closed. See
`ARCHITECTURE-GAPS.md` for the ledger, including the one closed as not
applicable rather than built.

## What is built

Three layers, fifteen pure modules and seven Home Assistant modules.

**Layer 3** decides what a room should feel like: comfort index, modes,
occupancy grace, sun geometry, actuator ordering, the thermal model, the demand
forecast, and a decision trace that explains every one of those in words.

**Layer 2** makes the room actually reach it: an integral outer loop closing
around the unit's own thermostat, and short-cycle protection the unit cannot
provide for itself.

**Layer 0** is read-only: sensors with age tolerances, a tariff series from
Abode Power Tariffs, and an hourly weather forecast.

## What changed since 0.5.3

| Release | Change |
|---|---|
| 0.6.0 | Tariff moved out to Abode Power Tariffs. Layer 2 built. Feed staleness. Dew point and condensation. Deadline-aware preconditioning. Sleep band ramp. Domain renamed |
| 0.6.1 | Fixed a crash on setup, an impossible sensor device class, and two config-flow tests that had never been run |
| 0.7.0 | Free cooling rebuilt on apparent temperature rather than dry bulb, with the dew point kept as a veto. Optional wind feed with unit conversion |
| 0.8.0 | Forecast-driven precool. Learned latent/sensible actuator choice. Illuminance removed |
| 0.8.1 | Fixed a crash on the first forecast: naive timestamps against an aware clock. Same fault fixed latent in the tariff. Tariff windows converted to local wall time |
| 0.8.2 | Documentation corrected against the code — 18 files. The §5 rule stated precisely and enforced by a test |
| 0.8.3 | **The thermal model could never learn.** The learning anchor was replaced every evaluation, so every interval was 30 s against a 60 s minimum and every observation was discarded. Dew point now published whenever the readings exist. Diagnostics completed |
| 0.8.4 | Beta preceding this handover's review |
| 0.8.6 | **Eight defects in shipped code, found by a review that read the source rather than the documentation.** Covers with no reported position no longer hold the air conditioning off all afternoon. Compressor state read from `hvac_action`, not the mode string, so idle time stops diluting `k_sensible`. Power management fails open on every unknown instead of stopping an occupied room on an unmeasured coefficient. A sunlit room with an unconverged solar term refuses to predict rather than predicting without it. Dry mode counts as the compressor running, and a refused stop no longer cancels cover and fan decisions. Solar headroom no longer double-counts a running unit. Short-cycle guard runs before the regulator, so anti-windup sees the applied step. Two rooms on one climate entity are refused at configuration and locked out on load. Details in `docs/architecture-review-2026-08.md` |
| 0.8.5 | **`no_grid_import` now acted on, not just read.** Optional battery/solar/house-load inputs; a room may keep running under the constraint if solar or the battery can cover its own projected need until it clears — checked inline in `select_actuator`, never by writing to the battery. **Per-room cover-control override**, for blinds kept for privacy or glare that must never move automatically. `energy_for` (thermal model) and the demand forecast now respect `can_heat`/`can_cool`, so a unit incapable of a direction is never projected to draw energy correcting it. `manifest.json`'s `quality_scale` corrected from `platinum` to `custom` — three quality-scale items are genuinely `todo`, not two, and `strict-typing` is a platinum-tier item that was unmet while claiming platinum |

## Installing

The domain rename in 0.6.0 means this is a new integration as far as Home
Assistant is concerned. Abode Power Tariffs must be configured first for
precool windows, the no-import rule or a costed forecast — without it the
controller runs on comfort alone, which is supported.

Optional but worth configuring on day one: an outdoor humidity sensor, an
outdoor wind sensor, and a weather entity. Each is stated in the configuration
screen in terms of what is lost without it rather than left blank.

## Verification state

**0.8.6 is the first build in which the Home Assistant side tests have
actually executed.** They ran against Home Assistant 2025.1.4 under
`pytest-homeassistant-custom-component` 0.13.205 — not the 2026.8.x this
integration targets, because the build sandbox is Python 3.12 and cannot
install anything newer.

Running them for the first time immediately found two tests that had never
passed and had never been run to find out:

- `test_a_naive_forecast_does_not_take_the_evaluation_loop_down` asserted the
  forecast was fetched inline at setup. 0.8.4 moved it to a background task on
  `async_call_later`. The test now advances the clock past the first startup
  attempt.
- `test_power_unavailable_holds_the_compressor_off` called
  `async_update_entry` before `add_to_hass`, which raises `UnknownEntry`. It
  also asserted the fail-closed power behaviour that 0.8.6 deliberately
  reverses, so it was rewritten rather than repaired.

Both were broken before this build. Assume there are others: a test that has
never run is not evidence of anything.

| Check | 0.8.5 | 0.8.6 |
|---|---|---|
| `ruff check custom_components tests` | clean, re-run | clean, re-run |
| `mypy --strict`, all 23 modules | not re-run — no Home Assistant | 17 errors, **identical set and count to unmodified 0.8.5**, every one a symbol that moved between 2025.1.4 and 2026.8.x (`ClimateEntityFeature`, the climate `ATTR_*`/`SERVICE_*` names, `AddConfigEntryEntitiesCallback`). No shipped code depends on the older names |
| Pure test suite (`tests/test_core.py`) | 281 passing | **290 passing** |
| Home Assistant side tests | **not run** | **run and passing.** 323 tests across all four files |
| Home Assistant log during those tests | not applicable | no errors; one pre-existing warning that `climate.test` is unavailable in fixtures that do not define it |
| Purity of the fifteen pure modules | not re-run | enforced and passing |
| Step / string / placeholder / entity / icon cross-checks | not re-run | passing |
| `strings.json` vs `translations/en.json` | identical | identical, re-checked |
| New tests verified against the bug they cover | — | **all eight.** Each bug was reinstated and the corresponding test confirmed to fail before being accepted |
| Documentation links | not re-checked | not re-checked |
| **Running against real hardware** | **not confirmed** | **not confirmed** |

Nothing has run in your house. Only Jason confirms it works.

### Every new test was checked against the reinstated bug

`dumb-mistakes.md` records that the first test written for the 0.8.3 thermal
fault passed against the broken code, and was only caught by putting the bug
back and watching the test pass. Every test added in 0.8.6 was checked that
way before it was accepted: the defect was reinstated in a scratch copy, the
suite was run, and the test was kept only if it failed.

One test did not fail on the first attempt — the fail-open test covered the
no-target path but not the unconverged-model path, which was the one that
mattered. A second test was written for that path specifically and verified
the same way.

### How 0.6.0 shipped broken, and what stops it recurring

0.6.0 failed at `async_setup_entry` on a three-argument `dict.get`. mypy would
have caught it in one line. It did not, because mypy had only been run on the
pure modules — Home Assistant was not installed, so checking the modules that
import it produced noise, and the file with the bug was in the set being
skipped. Filtering out the noise filtered out the fault.

Home Assistant and `pytest-homeassistant-custom-component` are now installed in
the build environment, `mypy --strict` runs across all twenty-three modules,
and the Home Assistant side tests run and must pass.

It earned its keep immediately. The identical insertion fault recurred twice
more while adding the wind feed and the weather feed, and mypy caught both in
the container. A narrow static test now also rejects any `.get()` with three
positional arguments. An indentation heuristic was tried as a wider net,
produced seven false positives on legitimately wrapped calls, and was removed
rather than shipped with exceptions.

The container is Python 3.12 and the targeted Home Assistant needs 3.14, so
the harness runs against 2025.1.4. Symbols that moved between those releases
account for the shim in `tests/conftest.py` and all 17 remaining mypy errors,
both commented where they are. Nothing in the shipped component is affected.

That gap is itself the limit of this verification. The suite passing against
2025.1.4 does not establish it passes against 2026.8.2.

## What is left

Not architecture. Four constants want tuning against a real house — the Layer 2
integral gain, the wind penetration fraction, the dry-mode advantage ratio and
the precool demand margin. Each has a stated basis and a documented direction,
and only running the thing improves them.

Layer 1, the ESPHome Matter-over-Thread adaptor, is a separate project. Nothing
in Layer 3 changes when it lands: the coordinator consumes a `climate` entity
and does not care what is underneath. Intesis in the meantime.

**Before 0.8.6 ships:**

- Run the whole suite against Home Assistant 2026.8.2, the version actually
  installed. Everything below rests on a run against 2025.1.4.
- The three quality-scale items in `quality_scale.yaml` remain `todo`, all
  three now blocked on that same point rather than on the tests not having
  run. `manifest.json` says `custom`, which is accurate.
- `ASSUMED_UNIT_KW` (1.2 kW) still stands in for every room's rated draw in
  the battery-headroom projection. Untouched in 0.8.6.

**What 0.8.6 does not fix.** It closes eight defects in shipped code and
changes no architecture. Eleven further findings from the same review are
recorded in `docs/architecture-review-2026-08.md` and are not built: the
per-approach thermal binning, the house-level power loop, the systems model
for multi-head units, the comfort floor, overnight shortfall detection, the
constant-RH derivative in the dry-versus-cool comparison, and the price that
is fetched and then ignored.

**What to watch on the next install**, in order of what tells you most:

1. `sensor.<room>_mode` → `model` attribute. `k_loss` samples should climb
   within about ten minutes. Still zero after an hour means something else is
   wrong.
2. Diagnostics → `action_sources`. A room reading `hvac_mode` rather than
   `hvac_action` is learning from mode, and its sensible coefficient will be
   diluted. Worth knowing which the Intesis adapter publishes.
3. The office blind, once its position reporting is known. If it reports no
   position, the trace should now say
   `covers: no cover in this room reports its position` and the compressor
   should run — where before the unit would have been held off.
4. `rejected` on a room during a no-import window with an unconverged model.
   It must **not** say `no grid import permitted`; the room should be cooling.
