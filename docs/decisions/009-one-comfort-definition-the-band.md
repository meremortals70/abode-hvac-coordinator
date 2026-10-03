# DR-009: One comfort definition per room: a band in comfort index

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4 |
| Origin | Design v0.4 |
| Related | DR-010, DR-013, DR-045 |

## Decision

A room's comfort is one low/high pair per mode (occupied, sleep, precool), in
comfort index, not degrees. There is no separate setpoint and no separate
precondition target. The user says how the room should feel; the controller
works out what temperature to ask for.

## Why

The humidity correction happens inside the conversion from index to dry bulb,
which is the whole reason for using an index. A second setting (a setpoint
alongside the band) gives two answers to one question.

## Rejected

- **A band plus a setpoint.** Two definitions of comfort that can disagree.
- **A separate precondition target (v0.3).** Replaced by the occupied band
  (DR-013).

## Consequences

Users set numbers on an unfamiliar scale. The bands are seeded with values
converted from ASHRAE 55, and the form explains the scale.

## In the code

Checked against: 0.9.0. **Conforms.**

- `const.py:221` - `DEFAULT_BANDS` - seeded bands, from ASHRAE 55
- `modes.py:137` - `band_in_force` - one band per mode
- `hci.py:239` - `dry_bulb_for_index` - the band becomes a temperature here
