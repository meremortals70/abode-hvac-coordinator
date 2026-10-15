# DR-079: Sleep chosen by hand ends when the sleep schedule finishes, or at sunrise

| | |
|---|---|
| Status | Superseded by DR-083 in 0.9.7; amended DR-058 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Review of the component against its intent, 14 October 2026. Instruction, 2026-10-14: "yes" (approving the review fixes) |
| Related | DR-014, DR-058 |

## Decision

A Comfort mode of Sleep goes back to Schedule when the room's sleep schedule turns from on to off, as Awake does, or at sunrise in a room with no sleep schedule. It still ends when the room reads empty.

## Why

Sleep ended only when the room read empty. A room with no presence sensor never reads empty, so the living room, set to Sleep on the evening of 13 October, would have stayed on its sleep band all of the next day and every night after, until it was changed by hand.

## Rejected

- **Keep Sleep through the schedule's end (DR-058).** It was meant to cover a lie-in; it also covered every following day in a room without presence.
- **A wake-up time per room, as a setting.** The sleep schedule already is one; sunrise is the fallback that needs nothing configured.

## Consequences

A lie-in past the end of the schedule puts the room back on its occupied band; choosing Sleep again keeps it. A Sleep chosen during the day in a room with no schedule lasts until the next sunrise.

## In the code

Checked against: 0.9.7. **Superseded by DR-083.** The code was removed in 0.9.7: a Comfort mode chosen by hand is no longer ended by the schedule, sunrise or an empty room.
