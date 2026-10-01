# DR-002: Decisions live in pure modules; the coordinator gathers and acts

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4 |
| Origin | Design v0.4 |
| Related | DR-001, DR-006 |

## Decision

Every decision the system makes lives in a module that imports nothing from
Home Assistant. `coordinator.py` gathers readings, calls those modules, and
carries out the result. If a decision ends up in `coordinator.py`, it is in the
wrong place.

## Why

A decision that can only be reached through Home Assistant can only be tested
with fixtures and mocks, and is rarely tested at all. A pure decision path can
be exercised in a plain Python session, which is why the core suite runs 367
tests in under a second.

## Rejected

- **One coordinator class holding the logic.** The usual shape for an
  integration. Rejected because every decision would then need a running Home
  Assistant to test.

## Consequences

Adding a decision means adding or extending a pure function first, then wiring
it. The price is some plumbing: the coordinator builds a `RoomInputs` snapshot
every cycle.

## In the code

Checked against: 0.8.14. **Conforms.** At 0.8.13 this was **Partly**: four
decisions had grown inside `coordinator.py`. In 0.8.14 each moved into a pure
module with its behaviour unchanged, and each has core tests with hand-worked
expected values. What the coordinator keeps is gathering inputs, writing the
trace and carrying out the result.

- `modes.py:513` - `evaluate_room` - the pure decision entry point
- `power.py:163` - `budget_allowance_kw` - the power allowance arithmetic
- `power.py:216` - `held_setpoint` - the setpoint clamp under the ceiling
- `regulate.py:275` - `arbitrate_cycling` - compressor arbitration across rooms on one outdoor unit
- `tariff.py:341` - `hours_until_cheaper_interval` - the tariff half of the cheaper-window test
- `coordinator.py:2350` - `_power_ceiling` - gathers the inputs, calls the above, writes the trace
