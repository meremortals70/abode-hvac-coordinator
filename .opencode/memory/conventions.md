> Coding conventions specific to Abode HVAC Coordinator.

## General

1. Solve the smallest version first.
2. Prefer explicit over clever.
3. Keep functions small and focused.
4. Name things after the domain, not the implementation.

## Home Assistant conventions

- Keep the pure decision layer free of Home Assistant imports.
- Public entity IDs, service names, config keys and stored data keys are preserved unless an issue explicitly asks to change them.
- Comments explain why, and cite the D-record number when the reason is a design rule.
- Docs and changelog use Australian/British spelling (`behaviour`, `licence`). Code identifiers and HA API names stay as upstream.

## Testing

- Every behaviour change needs a regression test that fails before and passes after.
- stdlib-only tests go in `tests/pure/`.
- HA behaviour tests go in `tests/ha/`.
- Whole-loop simulation tests go in `tests/sim/`.
- Run the full check suite: `ruff`, pure tests, simulation tests, HA tests.

## Design rules

- Do not renumber or rewrite old D-records in `docs/decisions/`.
- Add a new D-record when a new design rule is established.
- Do not change a design value, formula or decision without updating the D-record and explaining why.

## Proposals and findings

- One proposal open at a time.
- Ask for approval before changing released code, bumping versions, altering repo structure, or pushing to `main`.

## Footguns already mapped — do not rediscover these

Each of these cost time on 24 November 2026 and none of them is new; they are
consequences of the design as written.

- **A filter learns nothing from a reading that never moves.** `HeatFilter` and
  `HumidityFilter` learn their own sensor step from a *change* between readings,
  and `five_minutes` refuses to correct until it has one. Every test that needs
  a correction must feed two different readings first, or it silently measures
  nothing.
- **The plan's inputs must be the model's inputs.** `PlanInputs.occupied` and the
  occupancy the moisture filter is told are the same fact in production, because
  `_plan_inputs` passes `comfort.occupied`. A fixture that tells the plan one
  thing and the model another is measuring its own mistake — that is what made
  the whole-loop sim look like a control failure.
- **`q` is the net steady moisture term, not a source.** Negative where the
  fabric absorbs more than any source adds. Clamping it at zero deletes the
  model's only sink and makes both simulations worse; the architecture says so
  and now says it louder.
- **`decision.index[k]` and `decision.actions[k]` are 30-minute steps**
  (`points[5]`, `points[11]`, …), so `index[0]` is the forecast *half an hour
  ahead*, never "now". Comparing it with the current reading measures the
  expected cooling.
- **`RoomState(...)` is built positionally in tests.** Adding a field shifts
  every later argument and the failure appears somewhere unrelated. Use
  keywords.
- **A guard that suppresses its own evidence deadlocks.** The occupancy source
  was withheld until the room was "identified", and identification needed an
  observed occupied minute — which the guard suppressed. Gate the *prediction*,
  never the observation.
- **`ruff` is installable here**: `python3 -m venv /tmp/rv && /tmp/rv/bin/pip
  install "ruff==0.17.3"`, then `/tmp/rv/bin/ruff check custom_components tests`.
  Three CI failures in one day were lint, not behaviour.
