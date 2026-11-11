> Living document. Update when layers, boundaries, or conventions change.
> The full design specification is `docs/architecture.md`; design rules are in `docs/decisions/D###.md`.

# Architecture — Abode HVAC Coordinator

## Purpose

A Home Assistant custom integration that learns a thermal model of each room and decides how to run that room's air conditioners: a plan every 5 minutes and a PID every 30 seconds.

## Tech stack

| Layer | Technology |
| ----- | ---------- |
| Runtime | Python 3.14 |
| Platform | Home Assistant 2026.10.4+ |
| Language | Python 3.14 |
| Testing | `unittest`, `pytest-homeassistant-custom-component`, `ruff`, `mypy` |
| Packaging | HACS custom integration |

## Directory structure

```text
custom_components/abode_hvac_coordinator/
├── __init__.py              # Integration bootstrap
├── config_flow.py           # Setup and options flow
├── coordinator.py           # Scheduling, state aggregation, model updates
├── entity.py                # Base entity classes
├── sensor.py                # Published sensors
├── switch.py                # Switches
├── number.py                # Number entities
├── select.py                # Select entities
├── button.py                # Button entities
├── diagnostics.py           # Diagnostics
├── const.py                 # Constants
├── capabilities.py          # Device capability detection
├── models.py                # Shared data models
├── memory.py                # Persistence helpers
├── evidence.py              # Logging / evidence capture
├── services.yaml            # Service definitions
├── strings.json             # Translation keys
├── icons.json               # Icon mappings
├── manifest.json            # Integration metadata
├── quality_scale.yaml       # HA quality scale checklist
├── brand/                   # Brand assets
├── translations/            # UI translations
├── control/                 # Control algorithms (plan / PID)
├── model/                   # Thermal model learning and prediction
└── pure decision layer (no Home Assistant imports)
    ├── hci.py               # Comfort index calculations
    ├── psychro.py           # Psychrometrics
    ├── sun.py               # Solar gain
    ├── comfort.py           # Comfort targets and bands
    ├── economics.py         # Cost / energy economics
    ├── limits.py            # Safety and operational limits
    ├── delivery.py          # Deliverable setpoints
    ├── act.py               # Actuation decisions
    ├── observe.py           # Observation / filtering
    ├── forms.py             # Form / schedule helpers
    ├── linalg.py            # Linear algebra utilities
    └── model/               # Core thermal model

tests/
├── pure/                    # stdlib-only unit tests of the decision layer
├── ha/                      # Tests through Home Assistant
├── sim/                     # Whole-loop simulation of a room
└── test_structure.py        # Fails if a decision module imports Home Assistant

docs/
├── architecture.md          # Full design specification
├── decisions/               # Numbered design rules (D000–Dxxx)
├── actions.md               # User actions reference
├── configuration.md         # Configuration guide
├── data-updates.md          # Data update behaviour
├── entities.md              # Entity reference
├── examples.md              # Usage examples
├── installation.md          # Installation guide
├── known-limitations.md     # Known limitations
└── troubleshooting.md       # Troubleshooting
```

## Layer rules

1. **Pure decision layer** has no Home Assistant imports. It holds all thermal, comfort, economic and control decisions.
2. **Home Assistant layer** translates between HA APIs and the pure layer.
3. **Tests in `tests/pure/`** must pass without HA installed.
4. **Design rules** are immutable records in `docs/decisions/`. Do not renumber or rewrite old rules; add new ones.
5. **Public identifiers** (entity IDs, service names, config keys, stored data keys) are preserved unless an issue explicitly asks to change them.

## Data flow

1. `observe.py` collects sensor readings (temperature, humidity, AC state, energy, weather).
2. `model/` learns and predicts the room's thermal response.
3. `economics.py` and `comfort.py` evaluate forecast options.
4. `control/` produces a 5-minute plan and a 30-second PID output.
5. `delivery.py` converts the decision into a setpoint the AC can accept.
6. `act.py` issues the command via the Home Assistant layer.
7. `evidence.py` and `memory.py` record what happened for debugging and persistence.

## Cross-cutting concerns

- **Comfort index (HCI):** All targets and forecasts are evaluated in HCI, not raw temperature, so humidity and solar gain are accounted for as the occupant feels them.
- **Safety:** `limits.py` enforces hard operational constraints.
- **Time:** Forecasts are built against real instants; plan windows are wall-clock but resolved precisely.
- **No runtime dependencies:** `requirements` in `manifest.json` is empty; the component uses the standard library only.

## Decisions

| Date | Decision | Rationale |
| ---- | -------- | --------- |
| D009/D010 | Comfort index based on Steadman apparent temperature | Humidity and wind are felt by occupants |
| D016 | TiTe grey-box thermal model | Two heat stores (air + mass) with sun, leakage and appliance paths |
| D073 | Free cooling advised only when plan is to cool and outdoor conditions permit | Avoids unwanted ventilation |

## Known issues / next work

See `docs/known-limitations.md` and GitHub issues. Use `/defect <issue>` to work an issue end to end.
