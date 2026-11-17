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
| **`ruff`** | **absent** — no pip and no packages installed |
| **`tests/ha`** | **cannot run** — no `pytest`, no `pytest-homeassistant-custom-component` |
| `sqlite3` | absent — the recorder database is on the HA host, not here |
| `gh` | absent |
| `jq`, `node`, `perl` | absent; `awk`, `sed`, `grep` (busybox) only |

**So here: pure and simulation suites run, `ruff` and `tests/ha` do not.** CI is
the only runner for the Home Assistant suite and the linter, which makes a red
CI run the normal way those two are checked — not a surprise.

Timings, so a timeout can be chosen sensibly: pure suite ~112 s, simulation
~490 s (three tests). Both fit comfortably in a ten-minute timeout.

**No access to the HA host.** `/config/export_window.sh` and the recorder
database live on that machine. Recorder data reaches the agent only as a tarball
the user uploads to `tools/`.

### Line endings — read this before any git command

The worktree has **CRLF working files against LF blobs**. A plain `git status`
therefore reports 216 files modified, and a plain `git add` would rewrite every
line of every file in the repository.

```sh
git -c core.autocrlf=true diff --stat     # only real changes
git -c core.autocrlf=true add <files>
git -c core.autocrlf=true commit ...
```

With that flag the noise disappears and the diff is exactly what changed. Verify
with `--numstat` before committing: additions and deletions should be the same
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
