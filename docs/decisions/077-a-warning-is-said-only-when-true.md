# DR-077: A warning is said only when it is true

| | |
|---|---|
| Status | Accepted; amends DR-012, DR-050 and DR-071 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Office, 13–14 October 2026. Instruction, 2026-10-14: "i want you to roll everything into 0.9.6" |
| Related | DR-012, DR-016, DR-050, DR-069, DR-071 |

## Decision

A warning that the air conditioning is running, or will be turned off, is said only where the unit was on at the start of the evaluation. "Turning ... off" is said in the evaluation the off command is actually sent; while the minimum run holds the unit on it waits, and it is dropped if the room no longer stops.

## Why

On 14 October the Office said it was turning its air conditioning off at 08:28:48, when the stop was decided; the ten-minute minimum run held the unit on until about 08:33. On 13 October it was told the air conditioning was still running in an empty room whether or not it was.

## Rejected

- **Say it when decided, with the time left.** The time is not always known, and a second announcement is more noise.
- **Drop the warning if the stop is held.** The stop does happen; it is just later.

## Consequences

A warning can come up to ten minutes after the stop was decided. A room whose unit was already off hears nothing.

## In the code

Checked against: 0.9.6. **Conforms.**

- `coordinator.py:2333` - `def _timed_announcements` - the rule
- `coordinator.py:2367` - `self._deferred_final[room.room_id] = announcement` - held until the stop is sent
- `coordinator.py:2356` - `self._unit_mode_seen.get(room.room_id) in (None, "off")` - only for a unit that was on
- `actuator.py:583` - `trace.stopped = True` - an off actually sent
