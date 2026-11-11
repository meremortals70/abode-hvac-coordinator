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
