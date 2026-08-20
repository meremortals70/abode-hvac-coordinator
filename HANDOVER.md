# Handover

**Version 0.8.5. The architecture is complete.**

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

The 0.8.3 row below is what a prior build environment with Home Assistant
installed confirmed. **0.8.5 was built in a sandbox with no Home Assistant
package available** (only `homeassistant==2024.3.3` resolves there, older
than the 2025.1.4/2026.x this integration targets), so the two Home
Assistant–dependent checks could not be re-run this build. Everything else
was.

| Check | 0.8.3 | 0.8.5 |
|---|---|---|
| `ruff check custom_components tests` | clean | clean, re-run |
| `mypy --strict`, pure modules only, `--ignore-missing-imports` | clean | clean on every module changed this build (`thermal.py`, `forecast.py`, `models.py`, `modes.py`, `tariff.py`, `coordinator.py` syntax-checked) |
| `mypy --strict`, all 23 modules, Home Assistant installed | clean | **not re-run — no Home Assistant in this sandbox** |
| Pure test suite (`tests/test_core.py`) | 271 passing | **281 passing**, run directly with `python3 -m unittest` |
| Home Assistant side tests (`test_init.py`, `test_config_flow.py`) | 21 passing | **not run.** New tests were written for the power-aware compressor gate and the config/options flow's new fields, following the existing tests' patterns, but nothing in these two files has executed since before this build — they need `pytest-homeassistant-custom-component` against a matching Home Assistant version |
| Home Assistant log during those tests | no warnings, no errors | not applicable — tests not run |
| Purity of the fifteen pure modules | enforced by parsing imports | not re-run (the enforcing test lives in the Home Assistant side suite) |
| Step / string / placeholder / entity / icon cross-checks | clean | not re-run, same reason |
| `strings.json` vs `translations/en.json` | identical | re-checked by direct JSON diff, identical |
| Documentation links | resolve | not re-checked |
| **Running against real hardware** | **not confirmed** | **not confirmed** |

Nothing has run in your house. Only Jason confirms it works.

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

The container is Python 3.12 and Home Assistant 2026.6 needs 3.14.2, so the
harness runs against 2025.1.4. Two symbols moved between those releases; one is
shimmed in `tests/conftest.py` and one is filtered from the mypy run, both
commented where they are. Nothing in the shipped component is affected.

## What is left

Not architecture. Four constants want tuning against a real house — the Layer 2
integral gain, the wind penetration fraction, the dry-mode advantage ratio and
the precool demand margin. Each has a stated basis and a documented direction,
and only running the thing improves them.

Layer 1, the ESPHome Matter-over-Thread adaptor, is a separate project. Nothing
in Layer 3 changes when it lands: the coordinator consumes a `climate` entity
and does not care what is underneath. Intesis in the meantime.

**Before 0.8.5 ships:**

- Run `tests/test_init.py` and `tests/test_config_flow.py` against a real
  Home Assistant install — they have not executed since before this build.
- The three quality-scale items in `quality_scale.yaml` are genuinely `todo`:
  `config-flow-test-coverage`, `test-coverage` (both blocked on the point
  above) and `strict-typing` (untouched this build). `manifest.json` now says
  `custom` rather than `platinum`, which is accurate either way.
- The power-aware compressor decision assumes a single unit rating,
  `ASSUMED_UNIT_KW` (1.2 kW) — the same constant the demand forecast already
  used — rather than a per-room rated draw. If a room's unit is materially
  different from that, its battery-headroom projection will be off by
  roughly the same proportion.
