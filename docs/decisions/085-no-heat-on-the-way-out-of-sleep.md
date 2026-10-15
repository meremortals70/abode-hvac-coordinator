# DR-085: A room coming out of Sleep is not heated up to its daytime band

| | |
|---|---|
| Status | Accepted |
| Since | 0.9.7 (2026-10-15) |
| Origin | Incident, living room, 2026-10-15 05:12: "it jumped in temperature and woke us up". Instruction, 2026-10-15: "yes all changes and give me a script" |
| Related | DR-083, DR-084, DR-091, DR-092 |

## Decision

A room that has come out of Sleep is marked waking. While waking, a room below its daytime band is not heated: it warms by itself. Waking ends once the ramp is over and the room is inside or above its daytime band.

## Why

A room cooled for sleep is below its daytime band in the morning by design. On 15 October the living room was heated to 23 C at 05:12 for exactly that reason.

## Rejected

- **Lower the daytime band in the morning.** The band is the owner's; the fault was heating, not the band.

## Consequences

A room that stays below its daytime band all morning is not heated that morning. A room that was not asleep is heated as before.

## In the code

Checked against: 0.9.7. **Conforms.**

- `coordinator.py:1888` - `self._waking.add(room_id)` - marked on leaving Sleep
- `modes.py:616` - `if demand == "heat" and inputs.waking:` - not heated while waking
