# DR-080: A heading-home request ends once the room is occupied, or an hour after its deadline

| | |
|---|---|
| Status | Accepted; amends DR-013 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Review of the component against its intent, 14 October 2026. Instruction, 2026-10-14: "yes" (approving the review fixes) |
| Related | DR-012, DR-013 |

## Decision

A heading-home request ends once the room is occupied, after its presence grace, or one hour after its deadline if nobody has arrived. clear_override still ends it at any time.

## Why

Only clear_override ended a request. Until then the room stayed in Precondition, which outranks presence and sleep, so it was conditioned to the occupied band indefinitely and never turned off when it emptied again.

## Rejected

- **End at the deadline.** Arrivals run late; the hour covers that.
- **End on any presence reading.** The graced presence is what the rest of the room uses; a passer-by should not end it.

## Consequences

A request with no deadline, in a room with no presence sensor, still stands until it is cleared. HEADING_HOME_EXPIRY is a judgement.

## In the code

Checked against: 0.9.7. **Conforms.**

- `coordinator.py:1327` - `def _end_heading_home` - the rule
- `coordinator.py:1343` - `elif deadline is not None and now >= deadline + HEADING_HOME_EXPIRY:` - an hour past the deadline
- `scheduling.py:44` - `HEADING_HOME_EXPIRY = timedelta(hours=1)` - the hour
