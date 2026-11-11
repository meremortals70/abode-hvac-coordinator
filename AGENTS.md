# Abode HVAC Coordinator — working notes for agents

Home Assistant custom integration. It learns a thermal model of each room and
decides how to run that room's air conditioners: a plan every 5 minutes and a PID
every 30 seconds. Python 3.14, Home Assistant 2026.10.4+.

The design is specified in `docs/architecture.md`. Numbered design rules live in
`docs/decisions/D###.md`. Code comments and docs cite them as `[D023]`. **Read the
relevant section and rule before changing control behaviour.**

## New session workflow

The user is a vibe coder with limited GitHub/coding experience but is an
experienced architect. Treat them accordingly:

- Be patient and kind. Do not assume prior knowledge of git, GitHub, Python or
  Home Assistant internals.
- Always tell the truth. If something is wrong, say so plainly.
- Challenge them when needed, but do it respectfully and explain why.
- Explain what you are doing and why before doing it.
- Ask for approval before changing released code, bumping versions, altering
  repository structure, or pushing to `main`.

### Starting a session

1. Open the project folder: `C:\Users\jason\Nextcloud\abode-hvac-coordinator`.
   Project files live under `C:\Users\jason\Nextcloud\` and nowhere else — never
   the OneDrive folder or the stray `C:\Users\jason\Projects\` copies.
2. Check the current state:
   ```powershell
   git status
   git log --oneline -3
   ```
3. The expected branch is `main`. If the folder is on another branch, ask before
   switching it.

### Before making changes

1. Run the fast checks from the repository root:
   ```powershell
   .\.venv\Scripts\ruff check custom_components tests
   py -3 -m unittest discover -s tests/pure -t .
   ```
2. If these fail before you change anything, stop and tell the user.

### Pushing changes

`git push` is blocked at the OpenCode tool level. Push through the GitHub API
with the token in `GITHUB_TOKEN` (user environment variable):

```powershell
$token = [Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "User")
$headers = @{
  Authorization = "Bearer $token"
  Accept = "application/vnd.github+json"
  "X-GitHub-Api-Version" = "2022-11-28"
}
```

A ref cannot be pointed at a commit GitHub has never seen, so a local commit is
published by rebuilding it there:

1. `POST /git/blobs` with each changed file's exact bytes (`git cat-file blob
   HEAD:<path>`, base64). The blob SHAs then match the local ones.
2. `POST /git/trees` with `base_tree` = the remote head's tree and those entries.
3. `POST /git/commits` with the local message and the local author and committer
   dates, `parents` = the remote head.
4. `PATCH /git/refs/heads/main` with the new SHA and `force: false`.

When the tree and metadata match, the commit SHA is identical to the local one,
so the local and remote branches stay in step. Verify `git ls-remote` after.

### Creating a release

1. Update `manifest.json` and `CHANGELOG.md`.
2. Commit the changes locally.
3. Publish with the API (see above).
4. Create the release:

```powershell
$body = @{
  tag_name = "v0.10.6"
  target_commitish = "<full-40-char-commit-sha>"
  name = "v0.10.6"
  body = "<the changelog section for this version>"
  draft = $false
  prerelease = $false
} | ConvertTo-Json
Invoke-RestMethod `
  -Uri "https://api.github.com/repos/meremortals70/abode-hvac-coordinator/releases" `
  -Method POST -Headers $headers -Body $body -ContentType "application/json"
```

The tag and `main` must end up on the same commit.

### Project scope

The user's work spans Home Assistant apps, components, ESPHome configuration, HA
configuration and local LLMs. This repo is the Abode HVAC Coordinator custom
component. For ESPHome or other projects, look for their own `AGENTS.md` or ask
the user for their current goals.

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
- `tools/` — offline diagnostic scripts (recorder export, history dump). Not shipped.
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

Issues are the source of truth. `gh` is not installed on this machine; use the
REST API with the same headers (`GET /repos/meremortals70/abode-hvac-coordinator/issues`),
or ask the user to run `gh`.

1. **Pick and read.** List open issues, then the chosen issue and its comments.
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
GitHub links the commit to the issue. Publish through the API (see above).

Use `/defect <number>` to run this workflow end to end, and `/verify` to run the
checks.

## Model, not rules

The room and the house are models; the plan optimises within them [D104].

- **A constraint is a fact about the equipment or the world:** a unit's setpoint
  step and range, the modes it advertises, a compressor's minimum run and rest, a
  tariff boundary, a sensor that does not exist, a head's delay and lag.
- **A comfort or economic judgement is priced by the model, never branched on.**
  `if rh > 60`, `if price < 0.2`, `if index > band + 0.3` in `comfort.py`,
  `economics.py`, `limits.py`, `delivery.py` or `control/` are defects. The
  comfort index carries humidity, the economics carry the tariff, `c_start`
  carries the cycle; the optimiser decides.
- **If a behaviour needs a threshold, the model is missing a term.** Add the term
  and let the optimiser choose; a threshold that suppresses the symptom hides the
  term that should have priced it. The 60 % RH gate on dry mode, added and removed
  on 11 November 2026, is the worked example [D103][D104].
- **Record it as a D-record** in `docs/decisions/`, with a test that fails before
  and passes after.

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
