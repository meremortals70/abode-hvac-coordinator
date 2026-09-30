# DR-020: A room has heads, and heads sit on named outdoor units

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.8 (2026-08-22) |
| Origin | Finding 13 |
| Related | DR-016, DR-021, DR-028 |

## Decision

A room's climate entities are a list. Two heads in one room get one band, one
target and one setpoint. Each head carries an outdoor unit name; heads with
the same name share a compressor, in one room or two. A head with no name is
its own compressor. What a room can do is the intersection of what all its
heads can do.

## Why

Two rooms on one outdoor unit, and one room with two heads, both exist in the
house. The single-entity schema could not express the second at all, and the
guard's unit of account was wrong for the first (DR-016).

Claiming dry mode because one of two heads has it produces a decision the
other head cannot carry out.

## Rejected

- **A global Systems step with system objects and a type field.** Membership
  derived from a shared name covers every case with nothing to keep in step,
  and how many heads a unit has is answered by how many are named on it.
- **Multi-head arbitration.** No room's comfort is traded against another's.

## Consequences

Ducted systems (one indoor unit, dampers per zone) cannot be driven: the
output is a dry-bulb target per room.

## In the code

Checked against: 0.8.13. **Conforms.**

- `models.py:151` - `group_of` - a head's outdoor unit
- `models.py:162` - `groups` - a room's compressors
- `coordinator.py:2121` - `set.intersection` - capabilities across heads
