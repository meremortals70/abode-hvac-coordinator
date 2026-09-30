# DR-006: Every room publishes why it is doing what it is doing

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4 |
| Origin | Design v0.4 |
| Related | DR-002, DR-022 |

## Decision

Every room publishes a decision trace each cycle: its mode, the actuator
chosen, the reasons for it, and every cheaper option rejected with the reason.
The trace is published as the attributes of the room's mode sensor and in
diagnostics.

## Why

A controller nobody can audit is a controller nobody should run. "The cheap
options were exhausted" is worthless without per-decision evidence.

## Rejected

- **Log at debug level only.** Invisible unless someone turns logging on
  after the fact.

## Consequences

Every new rule has to write its reason into the trace, including refusals by
guards. A guard that refuses silently makes the room look like it is ignoring
its own decision.

## In the code

Checked against: 0.8.12 (`50e6abf`). **Conforms.**

- `models.py:293` - `DecisionTrace` - the trace
- `models.py:374` - `as_attributes` - what is published
- `sensor.py:64` - `attributes_fn` - the mode sensor carries it
