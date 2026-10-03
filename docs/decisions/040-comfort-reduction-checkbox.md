# DR-040: A per-room checkbox permits power management to reduce comfort

| | |
|---|---|
| Status | Superseded by DR-041 |
| Since | 0.8.10 (2026-08-23); replaced in 0.8.12 (2026-08-24) |
| Origin | Finding 15 |
| Related | DR-037, DR-041 |

## Decision

One checkbox per room, off by default. On, the ceiling keeps applying above
the band top, so the room runs at the largest output the remaining energy
allows, spread across the window, degrading gradually as the battery depletes.
It never stops.

## Why

The first proposal was a number per room: how far above the band it may be
driven. That asks the user to set a magnitude to answer a yes-or-no question,
and the magnitude is not theirs to set: the limit is whatever the remaining
energy can buy, which the controller already computes.

## Rejected

- **A comfort-reduction floor, in degrees or index, per room.** The reason
  above.

## Consequences

As built in 0.8.10, "off" still applied a ceiling while the room was inside
its band. That was not what was asked for: power management was meant to be a
complete gate. DR-041 removed it.

## In the code

Checked against: 0.9.0. **Superseded.** The stored key is unchanged; a stored true reads as
"enforced" and false as "off".

- `forms.py:60` - `power_management_from_raw` - old booleans mapped
- `coordinator.py:2698-2704` - `first version` - the within-band ceiling, recorded as a mistake
