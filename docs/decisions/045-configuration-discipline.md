# DR-045: A setting exists only if a correct result is impossible without it

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4 |
| Origin | Design v0.4, in reaction to Dual Smart Thermostat |
| Related | DR-009, DR-015, DR-030, DR-040 |

## Decision

A setting is added only when a user cannot get a correct result without it.
The whole comfort configuration is one number pair per room per mode. Tuning
constants, model coefficients and tolerances are not settings. No site data
(tariffs, bands for a particular house) lives in source.

## Why

An options list can be individually defensible and collectively unusable.
Surfacing a regulator's tuning or a model's coefficients rebuilds that problem
one layer up, and asks questions users cannot answer.

## Rejected

- **Expose tuning for experts.** The same problem, labelled advanced.

## Consequences

Constants carry the burden of being right. They are documented where they are
defined, with the reason, so a week of real data can move them.

## In the code

Checked against: 0.8.14. **Conforms.**

- `const.py:214` - `DEFAULT_BANDS` - seeds from ASHRAE 55, not this house
- `regulate.py:60` - `INTEGRAL_GAIN_PER_HOUR` - a constant, not a setting
