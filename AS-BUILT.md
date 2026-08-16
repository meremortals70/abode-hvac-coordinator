# As built

**Version 0.8.0.** What exists, module by module. Not a design document — see
`docs/architecture.md` for why any of it is shaped this way.

## Pure modules — fifteen, no Home Assistant imports

| Module | Holds |
|---|---|
| `hci.py` | Steadman apparent temperature indoors and out, radiant and still-air corrections, the inverse solve, `ComfortBand` |
| `models.py` | `Mode`, `ActuatorStep`, `RoomConfig`, `RoomInputs`, `DecisionTrace` |
| `modes.py` | Mode precedence, band in force, cheapest-first actuator selection |
| `thermal.py` | Per-room Kalman filter over four coefficients, sensible and latent learned separately |
| `forecast.py` | Vendor-neutral demand forecast, per period and per room |
| `tariff.py` | Consumes the Abode Power Tariffs interval series; collapses intervals into periods |
| `regulate.py` | Layer 2 integral outer loop, short-cycle protection |
| `staleness.py` | Reading-age tolerances per feed class |
| `psychro.py` | Dew point, free-cooling advisory on felt temperature with a dew-point veto, condensation risk |
| `scheduling.py` | Deadline-aware precondition start, sleep band ramp |
| `weather.py` | Hourly forecast trajectory, clear-sky fraction, the precool demand verdict |
| `grace.py` | Occupancy grace and shutdown announcements |
| `sun.py` | Sun-on-glass from geometry, with overhang shading |
| `forms.py` | Setup form shaping and the configuration summaries |
| `const.py` | Constants |

All fifteen pass `mypy --strict`, and a test parses their imports to prove none has acquired a Home Assistant dependency.

## Home Assistant modules — eight

`__init__.py`, `coordinator.py`, `config_flow.py`, `sensor.py`, `entity.py`,
`actuator.py`, `store.py`, `diagnostics.py`.

## What the coordinator owns at runtime

Per room: a thermal model, a grace state, a regulator state, the previous mode
and when it changed, and the last reading pair for learning. House-wide: the
tariff series, the demand forecast, and the set of stale feeds.

Regulator trims are **not persisted** — a trim is only valid for the conditions
that produced it. Thermal models are, keyed by room id.

## Configuration

Rooms, comfort bands, occupancy grace, lockout reasons; one tariff entry id;
outdoor temperature, humidity and wind speed entities; one weather entity.
Nothing else. The tariff plan
itself lives in Abode Power Tariffs.

## Entities

Per room: mode with the full trace, comfort index, commanded setpoint, dew
point, target dry bulb, settings.

House-wide: demand forecast, tariff rate, active constraints, projected cost,
outdoor temperature, outdoor apparent temperature, outdoor dew point, forecast
peak, stale feeds, rooms configured.

## Repair issues

Rooms with no bands; tariff constraints not acted on; the tariff could not be
read; the weather forecast could not be read.

## Tests

259 in the pure suite and 16 against a real Home Assistant, all passing, run with `python3 -m unittest discover -s
tests`. The Home Assistant side tests require
`pytest-homeassistant-custom-component` and have not been run here.

`tests/test_attributes.py` is a static check: it parses every class and fails on
an attribute read from `self` that is never assigned. It exists because that
exact fault shipped once and passed both lint and type checking.
