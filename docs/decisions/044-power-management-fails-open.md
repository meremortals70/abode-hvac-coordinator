# DR-044: Power management fails open

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.6 (2026-08-21), carried into 0.8.10 |
| Origin | Finding 3 |
| Related | DR-008, DR-018, DR-037 |

## Decision

The power budget applies only when every reading it needs is configured and
current. Any missing or stale figure leaves the room unthrottled, exactly as
if the feature were not configured.

## Why

The first version returned "refuse" on every unknown. An unconverged model is
the state every fresh install is in, so turning power management on stopped
occupied rooms for a whole no-import evening, with a trace line blaming the
battery (finding 3). A number the controller cannot compute is not grounds
for constraining comfort.

## Rejected

- **Fail closed.** The defect above.

## Consequences

A dead battery sensor quietly disables throttling; the stale-feed list on the
room shows why.

## In the code

Checked against: 0.8.12 (`50e6abf`). **Conforms.**

- `coordinator.py:2137-2143` - `engaged=False` - any unconfigured input disengages
- `coordinator.py:2349-2363` - `context.engaged` - any missing reading returns unthrottled
