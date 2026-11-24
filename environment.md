# Environment

What the machine an agent is running on has, and what it therefore cannot check.
`AGENTS.md` says *what* to run; this says what is possible where.

Two environments are used. **Confirm which one you are on before trusting a
command.** The Windows machine is `C:\Users\jason\Nextcloud\opencode\projects\`;
the Linux sandbox mounts the same folder at `/projects/`.

## The Windows machine

The one the project was developed on. Has the `.venv` described in
`AGENTS.md` (ruff 0.17.3, mypy 2.4.0, pytest-homeassistant), `py -3`, and the
`GITHUB_TOKEN` user environment variable that the PowerShell publishing snippets
expect.

Runs `ruff`, the pure suite and the simulation. It has the `.venv` with
`pytest-homeassistant-custom-component`, but `tests/ha` has not run reliably
there in practice — **CI is the runner for the Home Assistant suite on both
machines.**

## The Linux sandbox (from 17 November 2026)

Alpine 3.24, running as `www-data`, home `/opencode/home`.

| | |
| --- | --- |
| Python | **3.14.10** |
| git | **2.54.1** — `git push` is blocked, publish through the API |
| **`ruff`** | installable into a temporary venv: `python3 -m venv /tmp/rv && /tmp/rv/bin/pip install "ruff==0.17.3"` |
| **`tests/ha`** | **runs** from a temporary venv: `python3 -m venv /tmp/ha && /tmp/ha/bin/pip install "pytest-homeassistant-custom-component==0.13.375" pytest-cov` (Home Assistant 2026.10.4). Verified 24 November 2026 |
| `sqlite3` | absent — the recorder database is on the HA host, not here |
| `gh` | absent |
| `jq`, `node`, `perl` | absent; `awk`, `sed`, `grep` (busybox) only |

**So here: every suite runs.** Lint, pure, simulation and `tests/ha`, the last
two with coverage, from the venvs above. Earlier notes said otherwise; on
24 November 2026 a venv took `pytest-homeassistant-custom-component` and the setpoint
tests ran in seconds, which caught two of my own tests asserting nothing. **Check
the suite here before pushing, not in CI afterwards.** CI is still the only
place both Home Assistant versions run, and `mypy --strict` still needs it.

Timings, so a timeout can be chosen sensibly: pure suite ~112 s, simulation
~490 s (three tests). Both fit comfortably in a ten-minute timeout.

**No access to the HA host.** `/config/export_window.sh` and the recorder
database live on that machine. Recorder data reaches the agent only as a tarball
the user uploads to `tools/`.

### Line endings — read this before any git command

The worktree files and the blobs are **both LF** now (checked with
`git ls-files --eol` on 24 November 2026: `i/lf w/lf` for every file touched
that day), so a plain `git status` shows only real changes. Add with
`git -c core.autocrlf=false add <files>` and verify with `--numstat` before
committing: additions and deletions should be the same
count on files nobody touched.

`.opencode/memory/*.md` are LF, so they are unaffected.

### Credentials

`GITHUB_TOKEN` is in the environment. It is never printed — see the Credentials
section of `AGENTS.md` for the rules and for the two leaks of 17 November 2026.
The old Windows route (`[Environment]::GetEnvironmentVariable`) does not exist
here; read it from the environment inside the program instead.

### Tools that live here

- `tools/_rev17/review.py` — per-room review of an export: time in band, rule
  timelines, plan decisions, commands, learned curves.
- `tools/_rev17/timeline.py` — a 10-minute cross-entity timeline for a window.
- `tools/_rev17/release_0_10_13.py` — `issues`, `publish`, `release` stages; the
  release stage refuses to run unless CI is green for that commit.
- `tools/` is git-ignored, so all of the above are scratch, not shipped.
