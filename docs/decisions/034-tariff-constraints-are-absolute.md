# DR-034: Tariff constraints are declared, absolute, and reported if unknown

| | |
|---|---|
| Status | Accepted; amended by DR-041 for `no_grid_import` |
| Since | Design v0.4; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.4 |
| Related | DR-004, DR-037, DR-041 |

## Decision

Constraints are declared in the tariff, not hard-coded, and are never traded
against comfort or price at runtime. A constraint this integration does not
recognise is reported in a repair issue, not dropped, so another system can
consume it with no change here.

## Why

A rule like "no grid import from 14:00 to midnight" is a commitment the
household has made. A controller that weighs it against a warm room each
cycle has not kept it.

## Rejected

- **Hard-coded windows.** Site data in source (DR-045).
- **Drop unknown constraints silently.** Hides a typo in the tariff.

## Consequences

This integration cannot choose where power comes from, so it cannot enforce
`no_grid_import` directly. How the air conditioning responds to it is
decided per room by the occupant (DR-041).

## In the code

Checked against: 0.8.14. **Conforms.**

- `tariff.py:33` - `KNOWN_CONSTRAINTS` - the recognised set
- `coordinator.py:750-762` - `ISSUE_UNRECOGNISED_CONSTRAINT` - unknown ones reported
