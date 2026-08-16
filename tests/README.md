# Tests

`test_core.py` — 28 tests over the pure modules (`hci`, `models`, `modes`,
`tariff`). No Home Assistant required:

    python3 -m unittest discover -s tests -p "test_core.py" -v

`test_config_flow.py`, `test_init.py`, `test_sensor.py` — Home Assistant
surface. These require `pytest-homeassistant-custom-component` and **have never
been run**. They are written, not verified.

    pip install pytest-homeassistant-custom-component
    pytest tests
