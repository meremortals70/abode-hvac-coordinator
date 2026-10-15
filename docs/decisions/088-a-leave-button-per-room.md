# DR-088: Each room has a Leave button

| | |
|---|---|
| Status | Accepted |
| Since | 0.9.7 (2026-10-15) |
| Origin | Instruction, 2026-10-15: "I need to be able to turn the aircon control off when I leave the room manually… is a scene the best way"; "yes all changes and give me a script" |
| Related | DR-049, DR-083, DR-089 |

## Decision

Pressing a room's Leave button turns the room's air conditioning off at once, not held to the minimum run, and keeps it off until someone comes back: the presence sensor reading empty and then occupied again, or the owner changing the room's Automatic control, Comfort mode or heading-home request. It survives a restart.

## Why

A presence sensor is slow to see someone leave, and a room with none never does. The owner leaving should be enough. A scene that turned the unit off would be undone by the next evaluation.

## Rejected

- **A scene.** The component would turn the unit back on.
- **Automatic control off.** That hands the room to the owner by hand; Leave keeps it automatic and off.

## Consequences

A presence sensor that has not yet seen the person leave does not bring the room back on. A script can press the button with other leaving actions.

## In the code

Checked against: 0.9.7. **Conforms.**

- `button.py:52` - `class RoomLeaveButton` - the button
- `coordinator.py:1233` - `async def async_leave_room` - off at once
- `modes.py:128` - `if inputs.left:` - nothing runs the room while left
- `coordinator.py:2187` - `left by the owner, off until someone comes back` - until someone comes back
- `store.py:186` - `def set_left` - kept across a restart
