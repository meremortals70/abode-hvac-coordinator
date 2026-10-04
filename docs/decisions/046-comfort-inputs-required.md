# DR-046: A room needs a temperature and a humidity sensor

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.9 (2026-08-22) |
| Origin | 0.8.9 build |
| Related | DR-009, DR-018 |

## Decision

Temperature and humidity sensors are required when a room is configured. A
room saved before this, missing either, keeps running and raises a repair
issue naming what is absent.

## Why

Without both there is no comfort index, and without the index this
integration offers nothing a thermostat does not.

## Rejected

- **Optional inputs (before 0.8.9).** Allowed rooms that could never do their
  job.
- **Refuse to load old rooms.** Takes down a working house for a form change.

## Consequences

Stored fields stay nullable, so old rooms still load.

## In the code

Checked against: 0.9.1. **Conforms.**

- `config_flow.py:113-116` - `vol.Required(CONF_TEMPERATURE_ENTITY)` - required at setup
- `coordinator.py:851` - `_rooms_missing_comfort_inputs` - repair issue for old rooms
