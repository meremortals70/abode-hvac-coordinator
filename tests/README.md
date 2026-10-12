# Tests

| File | What it covers | Needs Home Assistant |
|---|---|---|
| `test_core.py` | The pure decision modules: comfort index, modes, actuator ordering, regulation (the room loop, rounding, per-direction trims), the thermal model and its projections, tariff, power, grace and warnings, capabilities, sun position, forms | No |
| `test_attributes.py` | Static checks on the source: every private attribute is assigned, the pure modules import nothing from Home Assistant, the outdoor apparent temperature is only compared where it is allowed to be | No (parses source) |
| `test_config_flow.py` | The setup and options flows, including reading what the air conditioner can do, refusing a room whose unit is not reporting, and the opening grace | Yes |
| `test_init.py` | Setup, unload, the actuator against a real Home Assistant, power management, the Automatic control switch | Yes |
| `test_controls.py` | The room's controls and switches, the stored profile, setpoint rounding, the command log, the opening grace and warnings, the weather coast and the room loop, end to end | Yes |
| `test_pure_edges.py` | Edges of the pure modules: the readings a feed or a stored file can send, out-of-range values, the solar forecast walk | No |
| `test_lifecycle.py` | What `__init__.py` owns: setup, the heading-home and clear-override services, migration, removing the entry and a room's device | Yes |
| `test_store.py` | The model store against Home Assistant's real `Store`: what each method keeps and when it reaches disk (DR-064) | Yes |
| `test_actuator.py` | What is sent to a unit or a cover, and what is not, through Home Assistant's service registry | Yes |
| `test_feeds.py` | The tariff, weather forecast and solar forecast fetches: startup retries, failures, repair issues | Yes |
| `test_living_room_night.py` | 0.9.3: each finding from the living room's night of 11/12 October 2026 (DR-059 to DR-066) | Yes |
| `test_coordinator_paths.py` | Coordinator paths reached only in particular conditions: learned draw, derated solar, the projection's forecast and sun, spoken warnings, setup errors | Yes |

The pure tests run with nothing installed:

    python3 -m unittest tests.test_core tests.test_pure_edges -v

The rest need the harness, and CI runs the whole suite:

    pip install pytest-homeassistant-custom-component pytest pytest-asyncio
    python3 -m pytest tests/ -v

**Say which Home Assistant a run was against.** A green run against the wrong
version is worth stating every time. At 0.9.3 the suite is 920 tests (599 of
them pure) and ran against Home Assistant 2026.8.3 on Python 3.14.6.

**Every test written for a fix is checked by putting the defect back.** The
reinstated run has to fail for the reason the test claims to be about. At 0.9.0
this was done for every new decision, pure and Home Assistant, and it found
three weak tests, which were then strengthened. At 0.9.1 it was done again for
the new tests, and the shipped tests of 0.9.0 were found to simulate a steeper
learned curve than the Office has, which is how the loop's asking for its
maximum went unseen.
