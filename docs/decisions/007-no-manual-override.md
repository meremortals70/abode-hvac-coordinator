# DR-007: No manual override; re-assert from the entity's live state

| | |
|---|---|
| Status | Accepted |
| Since | Principle from design v0.4; live-state check in 0.8.11 (2026-08-24) |
| Origin | Finding 19a |
| Related | DR-009, DR-024 |

## Decision

The coordinator is the only control surface. Every cycle it compares its
decision with what the climate entity reports right now, and re-sends if they
differ. A change made at the wall is overridden on the next cycle. The fix for
an outcome nobody wants is to change the room's bands.

## Why

Until 0.8.11 the send cache compared only against the last command sent, not
the entity's state. A change at the wall could sit for one or more cycles
before something incidental caused a re-send.

## Rejected

- **Treat a wall change as intent and hold it.** That builds a second control
  surface into a system whose premise is that there is one.
- **Never re-check.** The pre-0.8.11 behaviour: the wall silently wins.

## Consequences

Anyone who wants a room's unit off has one route: set a lockout reason on the
room in the options flow (DR-021). There is no switch entity for it.

## In the code

Checked against: 0.8.13. **Conforms.**

- `actuator.py:124` - `_matches_live_state` - the live comparison
- `actuator.py:314-319` - `_matches_live_state` - skip only when memory and live state agree
