# Handover

**Version 0.8.0. The architecture is complete.**

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

## Installing

The domain rename in 0.6.0 means this is a new integration as far as Home
Assistant is concerned. Abode Power Tariffs must be configured first for
precool windows, the no-import rule or a costed forecast — without it the
controller runs on comfort alone, which is supported.

Optional but worth configuring on day one: an outdoor humidity sensor, an
outdoor wind sensor, and a weather entity. Each is stated in the configuration
screen in terms of what is lost without it rather than left blank.

## Verification state

| Check | Result |
|---|---|
| `ruff check custom_components tests` | clean |
| `mypy --strict`, all 23 modules, Home Assistant installed | clean |
| Pure test suite | 261 passing |
| Home Assistant side tests | 16 passing |
| Home Assistant log during those tests | no warnings, no errors |
| Purity of the fifteen pure modules | enforced by parsing imports |
| Step / string / placeholder / entity / icon cross-checks | clean |
| `strings.json` vs `translations/en.json` | identical |
| Documentation links | resolve |
| **Running against real hardware** | **not confirmed** |

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
