# Tests

| Folder | Needs | How to run |
| --- | --- | --- |
| `tests/pure` | Python 3.14 only, nothing installed. Tests the decision modules, the helpers and `forms.py` directly; `test_structure.py` fails if any of them imports Home Assistant | `python3 -m unittest discover -s tests/pure -t .` |
| `tests/sim` | Python 3.14 only. The whole loop on a simulated room for three days (about 75 seconds) | `python3 -m unittest discover -s tests/sim -t .` |
| `tests/ha` | The test helpers for the release under test: from 2026.11, Home Assistant's own `pytest-homeassistant`, installed from the core tag (`pip install "pytest-homeassistant @ git+https://github.com/home-assistant/core@2026.11.0#subdirectory=packages/pytest-homeassistant"`); before it, `pytest-homeassistant-custom-component` (0.13.375 for 2026.10.4, 0.13.367 for 2026.9.4). `tests/ha/harness.py` imports from whichever is installed. Sets up a config entry and goes through Home Assistant as a user would | `python -m pytest tests/ha` |

Coverage is measured over the pure and Home Assistant tests together:

```
python -m pytest tests/pure tests/ha --cov=custom_components/abode_hvac_coordinator --cov-branch
```

Notes for the Home Assistant tests:

- `pytest.ini` turns respx's pytest plugin off (`-p no:respx`): from 2026.11 Home Assistant aliases httpx to httpx2 when it is imported, and that plugin imports httpx first.
- The recorder is a dependency of the integration, so every test sets up `recorder_mock` before Home Assistant.
- `tests/ha/conftest.py` imports this repository's `custom_components` before Home Assistant starts; otherwise the test plugin's own `custom_components` package hides it.
- `tests/pure/_load.py` imports the real package when Home Assistant is installed, and registers it by path when it is not, so the pure tests run either way.
- The test harness runs asyncio in debug mode, which logs "Executing … took 0.1x seconds" for setup steps. That is debug mode recording a traceback for every scheduled callback; with debug off, the whole setup, first cycle included, takes about 0.06 s.
