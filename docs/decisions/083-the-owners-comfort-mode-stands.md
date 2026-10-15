# DR-083: A Comfort mode chosen by the owner stays until the owner changes it

| | |
|---|---|
| Status | Accepted; supersedes DR-079; amends DR-058 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Incident, living room, 2026-10-15 05:12. Instruction, 2026-10-15: "when I have chosen manual mode, the system can't just change it. the home owner is the boss"; "if it is not set for schedule, then any schedule has no impact. it follows the mode set by the owner"; "yes all changes and give me a script" |
| Related | DR-014, DR-058, DR-079, DR-085 |

## Decision

Sleep or Awake, chosen by hand, stays until the owner chooses another Comfort mode. Nothing else changes it: not the sleep schedule, not sunrise, not an empty room. A choice made in an empty room is accepted.

## Why

On 15 October the living room was on Sleep, chosen by hand. At 05:12 sunrise ended it (DR-079); the room went to its daytime band, the unit was sent heat at 23 C, and the house woke. The owner sets the mode; the component follows it.

## Rejected

- **Keep the sunrise end with a later time.** Any time the component picks is a time the owner did not.
- **End it on an empty room only.** Also the component changing the owner's choice, and a room without a presence sensor would still need the ending for its own sake.

## Consequences

A room left on Sleep stays on Sleep until someone changes it. Schedule is the mode that follows the schedule. The Leave button (DR-088) is the way to say the room is empty.

## In the code

Checked against: 0.9.7. **Conforms.**

- `select.py:159` - `undone by the component (0.9.7, DR-083)` - the select sets the mode and nothing else
- `coordinator.py:1297` - `async def async_set_comfort_mode` - kept until the owner changes it
