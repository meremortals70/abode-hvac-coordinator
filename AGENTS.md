# Abode HVAC Coordinator — working notes for agents

Home Assistant custom integration. It learns a thermal model of each room and
decides how to run that room's air conditioners: a plan every 5 minutes and a PID
every 30 seconds. Python 3.14, Home Assistant 2026.10.4+.

The design is specified in `docs/architecture.md`. Numbered design rules live in
`docs/decisions/D###.md`. Code comments and docs cite them as `[D023]`. **Read the
relevant section and rule before changing control behaviour.**

## Repository layout

- `custom_components/abode_hvac_coordinator/` — the integration
  - Pure decision layer (no Home Assistant imports): `hci.py`, `psychro.py`,
    `sun.py`, `comfort.py`, `economics.py`, `limits.py`, `delivery.py`, `act.py`,
    `observe.py`, `forms.py`, `capabilities.py`, `linalg.py`, `model/`, `control/`
  - Home Assistant layer: `__init__.py`, `coordinator.py`, `config_flow.py`,
    `entity.py`, `sensor.py`, `switch.py`, `number.py`, `select.py`, `button.py`,
    `diagnostics.py`
- `tests/pure/` — stdlib-only unit tests of the decision layer. `test_structure.py`
  fails if a decision module imports Home Assistant.
- `tests/ha/` — tests through Home Assistant, using `pytest-homeassistant` helpers.
- `tests/sim/` — whole-loop simulation of a room, about 75 seconds.
- `docs/decisions/` — one file per design rule. Do not renumber or rewrite old rules.
- `CHANGELOG.md`, `custom_components/abode_hvac_coordinator/manifest.json` — version and release notes.

## Commands

Run from the repository root unless stated.

| What | Command |
| --- | --- |
| Pure tests | `py -3 -m unittest discover -s tests/pure -t .` |
| Simulation | `py -3 -m unittest discover -s tests/sim -t .` |
| Home Assistant tests | `.\.venv\Scripts\python -m pytest tests/ha` |
| Coverage | `.\.venv\Scripts\python -m pytest tests/pure tests/ha --cov=custom_components/abode_hvac_coordinator --cov-branch` |
| Lint | `.\.venv\Scripts\ruff check custom_components tests` |
| Types (CI workaround) | see `.github/workflows/ci.yml` |

This machine's `python` name is shadowed by the Microsoft Store alias, so use the
`py -3` launcher outside the venv. On Linux/CI the commands are the same with
`python`/`python3`.

`ruff` is pinned to 0.17.3 and `mypy` to 2.4.0 in CI. Strict mypy needs
`custom_components` copied to a temp directory with an `__init__.py` added; the
exact steps are in the `lint-and-types` job.

One-time local setup:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install "ruff==0.17.3" "mypy==2.4.0" "pytest-homeassistant-custom-component==0.13.375" pytest-cov
```

Pure and simulation tests need nothing installed; run them first when the Home
Assistant test environment is unavailable.

## Defect workflow (GitHub Issues)

Issues are the source of truth. Use the `gh` CLI; the repo is
`meremortals70/abode-hvac-coordinator`.

1. **Pick and read.** `gh issue list --state open` then
   `gh issue view <number> --comments`.
2. **Restate the defect** in one or two sentences: what should happen, what
   happens, and the smallest reproduction.
3. **Find the layer.** Search the code and read the relevant part of
   `docs/architecture.md` plus any `[D###]` cited nearby. Check
   `docs/known-limitations.md` — some behaviour is deliberate.
4. **Reproduce with a failing test first.** Put stdlib-only tests in
   `tests/pure/`; put Home Assistant behaviour in `tests/ha/`. Run it and confirm
   it fails for the stated reason.
5. **Fix the cause, not the symptom.** Keep the change minimal. Do not change a
   design value, formula or decision without updating the D-record and explaining
   why in the issue.
6. **Verify.** Run the targeted test, the pure suite, the simulation, and the Home
   Assistant suite. Run `ruff check custom_components tests`.
7. **Record it.** Add a `### Fixed` entry under the next version in
   `CHANGELOG.md`, naming the D-record where one applies. Bump
   `manifest.json` and the changelog heading together only when the fix is being
   released.
8. **Link and close.** Comment on the issue with the cause, the fix, and the test
   name. Close it only when the fix is merged/verified, or with a written reason
   if it cannot be reproduced.

Git: branch `fix/<issue>-<slug>`, commit message `Fix #<issue>: <summary>` so
GitHub links the commit to the issue. `git push` is denied by policy — ask the
user to push or approve it.

Use `/defect <number>` to run this workflow end to end, and `/verify` to run the
checks.

## Change rules

- Every behaviour change needs a regression test that fails before and passes
  after.
- Keep the pure decision layer free of Home Assistant imports.
- Do not add runtime dependencies. `"requirements": []` in the manifest is
  deliberate; the component uses the standard library only.
- Preserve public entity IDs, service names, config-flow keys and stored data
  keys unless the issue explicitly asks to change them. Users have automations
  and history keyed to them.
- Docs and changelog use Australian/British spelling (`behaviour`, `licence`).
  Code identifiers and Home Assistant API names stay as upstream.
- Comments explain why, and cite the D-number when the reason is a design rule.
