---
description: Run this project's checks and report pass/fail
agent: build
---

Run the project checks from the repository root and report a compact pass/fail
table. Do not skip a check silently.

1. `.\.venv\Scripts\ruff check custom_components tests`
2. `py -3 -m unittest discover -s tests/pure -t .`
3. `py -3 -m unittest discover -s tests/sim -t .`
4. `.\.venv\Scripts\python -m pytest tests/ha`

Notes:
- Pure and simulation tests need only Python 3.14. If Home Assistant test
  dependencies are missing, say exactly which command failed and how to install
  them per `AGENTS.md`; still report checks 1-3.
- If `ruff` is missing, say so and continue with the tests.
- Report the first failing test and its traceback summary, not the whole log.
- Finish with one line: `READY` if everything passed, or `NOT READY: <reason>`.
