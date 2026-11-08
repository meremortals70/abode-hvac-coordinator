---
description: Work a GitHub issue end to end (reproduce, fix, test, record)
agent: build
---

Work GitHub issue $1 in this repository.

Follow `AGENTS.md` and the design in `docs/architecture.md`. Do not skip the
reproduction step.

1. Fetch the issue and its comments:
   `gh issue view $1 --comments`
2. Restate the defect in one or two sentences: expected, actual, smallest
   reproduction. If the issue is ambiguous, ask the user before changing code.
3. Search the code for the behaviour. Read the relevant part of
   `docs/architecture.md` and any `[D###]` cited nearby. Check
   `docs/known-limitations.md` before calling it a bug.
4. Write a failing regression test first:
   - stdlib-only / decision layer -> `tests/pure/`
   - Home Assistant behaviour -> `tests/ha/`
   Run it and confirm the failure matches the report.
5. Make the smallest fix that addresses the cause. Do not change design values,
   formulas or decisions without updating the D-record and saying why.
6. Run, in this order:
   - the targeted test
   - `py -3 -m unittest discover -s tests/pure -t .`
   - `py -3 -m unittest discover -s tests/sim -t .`
   - `.\.venv\Scripts\python -m pytest tests/ha`
   - `.\.venv\Scripts\ruff check custom_components tests`
7. Add a `### Fixed` entry under the next version in `CHANGELOG.md`, naming the
   D-record where one applies. Only bump `manifest.json` and the changelog
   heading if the user says this is being released.
8. Summarise for the user: cause, fix, test name, files changed, and anything
   that could not be verified.
9. After the user approves, comment on the issue with that summary:
   `gh issue comment $1 --body "..."`.
   Close it only once the fix is merged/verified:
   `gh issue close $1 --reason completed`.

Rules:
- Never claim a test passed unless you ran it.
- Keep public entity IDs, service names, config keys and stored data keys
  unchanged.
- Do not push. `git push` is denied; ask the user.
