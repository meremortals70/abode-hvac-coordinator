# DR-096: The power trace says "no limit" where there is none

| | |
|---|---|
| Status | Accepted |
| Since | 0.9.7 (2026-10-15) |
| Origin | Live trace, living room, 2026-10-15 08:30: "ceiling 99.0 C approach, comfort reduction in effect". Root-and-branch review, 2026-10-15, finding F7 |
| Related | DR-006, DR-037 |

## Decision

The pull-down bin's ceiling is reported as "no limit on the setpoint", and "comfort reduction in effect" only when the ceiling is actually reducing comfort.

## Why

The pull-down bin's stand-in value of 99 C was printed, with a reduction claimed that was not happening.

## Rejected

- **None.** The text was wrong.

## Consequences

None beyond the trace.

## In the code

Checked against: 0.9.7. **Conforms.**

- `coordinator.py:3900` - `unlimited = trace.power_ceiling_c >= self._BIN_CEILING_C[-1]` - no limit, in words
