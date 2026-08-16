# Contributing

## Running the tests

The pure decision modules import nothing from Home Assistant, so they run with
no dependencies at all:

```
python3 -m unittest discover -s tests -p "test_core.py" -v
```

181 tests covering the comfort index and its inverse, mode precedence, the full
actuator ordering, tariff windows and constraint handling, and setpoint
clamping, the setup form helpers, the seeded band defaults and lockout reason
handling.

The Home Assistant surface tests need the test harness:

```
pip install pytest-homeassistant-custom-component
pytest tests
```

**Those have never been run.** They are written, not verified.

## Linting and typing

```
ruff check custom_components tests
mypy --config-file mypy.ini custom_components
```

Ruff passes clean. Mypy passes clean on the four pure modules; the rest cannot
be verified without Home Assistant installed, because every `homeassistant.*`
import resolves to `Any`.

## Structure

| File | Home Assistant? |
|---|---|
| `hci.py` | No — the comfort index and its inverse |
| `models.py` | No — modes, room config, inputs, trace |
| `modes.py` | No — mode precedence and actuator selection |
| `tariff.py` | No — windows and constraints |
| `thermal.py` | No — the learned model |
| `forecast.py` | No — the demand forecast |
| `sun.py` | No — is the sun on this room's windows |
| `grace.py` | No — occupancy grace and announcements |
| `forms.py` | No — shaping setup forms into stored configuration |
| `store.py` | Yes — learned state persistence |
| `actuator.py` | Yes — turns decisions into service calls |
| `coordinator.py` | Yes — reads state, runs the evaluator |
| `entity.py` | Yes — base entity and device info |
| `sensor.py` | Yes — the three sensors per room |
| `config_flow.py` | Yes — setup and room configuration |
| `diagnostics.py` | Yes — diagnostics download |

**Decisions belong in the pure modules.** If a change puts a decision in
`coordinator.py`, it has gone in the wrong place — that file gathers inputs and
publishes results, nothing more.

## Versioning

`0.MINOR.PATCH`. The minor number goes up for a release with new behaviour;
the patch number for a fix to a released version. Internal iterations between
releases do not get their own version — the version in `manifest.json` is what
was published, not how many times it was rebuilt.

## Quality scale

`custom_components/abode_hvac_coordinator/quality_scale.yaml` tracks all 54 rules from
the Home Assistant integration quality scale, each marked done, todo, or exempt
with a written reason.

Any change that would move a rule backwards should say so in the pull request.

## Adding a configuration option

Don't, unless a user cannot get a correct result without it. Read
[Configuration](configuration.md) first. A new option needs to name the user who
is stuck without it.

## Running the checks

```
ruff check custom_components tests
mypy --strict --ignore-missing-imports custom_components/abode_hvac_coordinator/
python3 -m pytest tests/
```

All three, every time. **mypy must be run with Home Assistant installed and
across the whole package**, not only the pure modules. Restricting it to the
pure modules is how a three-argument `dict.get` shipped in 0.6.0 and crashed
`async_setup_entry` — the fault was in one of the files being skipped.

The pure suite alone will run without Home Assistant:

```
python3 -m unittest discover -s tests -p "test_core.py"
python3 -m unittest tests.test_attributes
```

That is useful for a quick loop. It is not sufficient to release on.
