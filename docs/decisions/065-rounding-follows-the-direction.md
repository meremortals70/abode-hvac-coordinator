# DR-065: The commanded setpoint rounds down when cooling and up when heating

| | |
|---|---|
| Status | Accepted; amends DR-051; amended by DR-075 in 0.9.6 |
| Since | 0.9.3 (2026-10-12) |
| Origin | Living room overnight review, 11/12 October 2026, finding 1 |
| Related | DR-051, DR-054, DR-056 |

## Decision

Rounding to the unit's step goes the way the room is being driven: down when
cooling, up when heating. Where the power ceiling has just bound, it still goes
the way that stays inside the ceiling.

## Why

From 20:27 to 21:40 the loop asked for 0.2 C below the room. Rounded to the
nearest whole degree, the unit was sent 26.0 C against a room of 25.9 C, and
did nothing.

## Rejected

- **Round to nearest (DR-051 unchanged).** It can send the room's own
  temperature, or past it.
- **Pass the unrounded value.** The unit rounds it anyway, in its own way
  (DR-051).

## Consequences

A cooling room can be sent up to one step colder than the loop asked. The loop
walks the setpoint back as the room approaches it (DR-056).

## In the code

Checked against: 0.9.7. **Conforms**, as amended by DR-075.

- `coordinator.py:1647` - `elif way == "cool":` - cooling rounds down (the room loop's way since DR-075)
- `coordinator.py:1649` - `elif way == "heat":` - heating rounds up
- `coordinator.py:1651` - `rounded = quantise_setpoint(commanded, limits, toward=toward)` - the rounding
