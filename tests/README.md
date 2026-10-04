# Tests

| File | What it covers | Needs Home Assistant |
|---|---|---|
| `test_core.py` | The pure decision modules: comfort index, modes, actuator ordering, regulation (the room loop, rounding, per-direction trims), the thermal model and its projections, tariff, power, grace and warnings, capabilities, sun position, forms | No |
| `test_attributes.py` | Static checks on the source: every private attribute is assigned, the pure modules import nothing from Home Assistant, the outdoor apparent temperature is only compared where it is allowed to be | No (parses source) |
| `test_config_flow.py` | The setup and options flows, including reading what the air conditioner can do, refusing a room whose unit is not reporting, and the opening grace | Yes |
| `test_init.py` | Setup, unload, the actuator against a real Home Assistant, power management, the Automatic control switch | Yes |
| `test_controls.py` | The room's controls and switches, the stored profile, setpoint rounding, the command log, the opening grace and warnings, the weather coast and the room loop, end to end | Yes |

The pure tests run with nothing installed:

    python3 -m unittest discover -s tests -p "test_core.py" -v

The rest need the harness, and CI runs the whole suite:

    pip install pytest-homeassistant-custom-component pytest pytest-asyncio
    python3 -m pytest tests/ -v

**Say which Home Assistant a run was against.** A green run against the wrong
version is worth stating every time. At 0.9.1 the suite ran against Home
Assistant 2026.8.3 on Python 3.14.

**Every test written for a fix is checked by putting the defect back.** The
reinstated run has to fail for the reason the test claims to be about. At 0.9.0
this was done for every new decision, pure and Home Assistant, and it found
three weak tests, which were then strengthened. At 0.9.1 it was done again for
the new tests, and the shipped tests of 0.9.0 were found to simulate a steeper
learned curve than the Office has, which is how the loop's asking for its
maximum went unseen.
