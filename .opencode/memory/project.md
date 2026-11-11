> Project memory for Abode HVAC Coordinator. Update at the end of each session.

## Current objective

Maintain and extend the Abode HVAC Coordinator Home Assistant custom integration. It learns a thermal model of each room and decides how to run that room's air conditioners.

## Active work

- Migrating project instructions into the OpenCode scaffolding: created `ARCHITECTURE.md` and `.opencode/memory/`.

## Recently completed

| Date | What |
| ---- | ---- |
| Earlier | Shipped 0.10.4 |

## Decisions

| Date | Decision | Why |
| ---- | -------- | --- |
| Design | Comfort index (HCI) is Steadman apparent temperature with wind zero | Occupants feel humidity, not just dry-bulb temperature |
| Design | Two-store thermal model (air + mass) | Captures building inertia and solar gain |
| Design | No runtime dependencies | `requirements` is empty; stdlib only |

## Blockers

- None currently.

## Known issues

See `docs/known-limitations.md` and GitHub issues at `https://github.com/meremortals70/abode-hvac-coordinator/issues`.

## Glossary

| Term | Meaning |
| ---- | ------- |
| HCI | Human Comfort Index, derived from Steadman apparent temperature |
| TiTe model | Two-store thermal model: room air + building mass |
| Plan | 5-minute lookahead decision |
| PID | 30-second setpoint adjustment |
| Pure layer | Decision modules with no Home Assistant imports |
| D-record | Numbered design rule in `docs/decisions/D###.md` |
