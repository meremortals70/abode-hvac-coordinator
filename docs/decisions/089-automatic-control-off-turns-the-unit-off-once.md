# DR-089: Switching Automatic control off turns the unit off, once

| | |
|---|---|
| Status | Accepted; amends DR-049 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Instruction, 2026-10-15: "so here is a new change, when autonomous mode is set off, the aircon shuts down" |
| Related | DR-049, DR-088 |

## Decision

Switching a room's Automatic control off sends its unit off once, straight away and not held to the minimum run. After that nothing is sent: a unit the owner then turns on by hand is theirs. A restart or switching off a room already off sends nothing.

## Why

DR-049 left the unit exactly as it was. On 15 October the living room was cooling when Automatic control was switched off, and it kept cooling.

## Rejected

- **Hold the unit off while Automatic control is off.** That was DR-047's fault: the owner could not use the unit by hand.

## Consequences

Turning Automatic control off is also turning the aircon off. To keep it running by hand, turn it on after.

## In the code

Checked against: 0.9.7. **Conforms.**

- `coordinator.py:1272` - `self._switch_off_pending.add(room_id)` - only on the change from on to off
- `coordinator.py:1439` - `inactive_reason == SWITCHED_OFF_REASON` - the one command sent
- `coordinator.py:1737` - `def _note_owner_stop` - not held to the minimum run
