# Session handoff

> Last updated: 24 Nov 2026 (end of session)

## Current task

**0.10.14 is shipped and closed out.** The next session starts on the filter's
tracking failure — see "Next move".

## What's done (24 Nov 2026)

### 1. Seven-day review of 17–24 Nov, autonomy only

Two exports, `abode_office_2026-11-24` and `abode_living_2026-11-24`, covering
local Tue 17 Nov 16:00 (when 0.10.13 loaded) to Tue 24 Nov 10:34.

- **Living room, Automatic control on (71h47m of the week, 8 spells, all
  overnight):** **86.6% in band** over 53h08m of banded time; sleep 88.5% of
  47h02m, waking 72.2% of 6h06m. Daily: Tue 86.6, Wed 89.5, Thu 91.6, Fri 94.7,
  Sat 70.1, Sun 89.0, Mon 73.7, Tue 80.4.
- 25 out-of-band spells, 474 minutes, almost all the first 25–45 minutes of a
  night while the room comes down to the sleep band. One that is not: Mon 23
  21:46–22:54, 68 minutes at up to 1.43 over.
- **Office:** Automatic control on only 10h06m of the week; 7h41m of that was
  `Leave` (778 cycles, one press at Sat 21 17:24 held it off until 00:26). It
  had a band for 3h02m only — 38.7% in band, a sample too small to judge.
  `script.leave_office` ran 58 times in the week (~10/day); that is what keeps
  the office `left`.
- Both 0.10.13 changes confirmed live: `limit`/`limit_reason` on every cycle
  from the first cycle of 0.10.13; the office's `opening` stop fires with the
  hall door, and the door is in `interior_openings`.

### 2. Issue #22 closed `not_planned` — not a coordinator defect

The head (`climate.living_room_aircon`, an **IntesisBox on local HTTP**) reports
the indoor unit's own state a few seconds behind. Proof: 49 of 52 round trips
coincide with exactly one coordinator cycle; `act.RoomAct.calls` can only emit
one `set_hvac_mode` per head per cycle; and on **Tue 24 Nov 01:10:15** the head
went `dry → cool → dry` with **no command sent at all** (mode, setpoint and fan
unchanged, vane control off).

The 0.2 s contradiction and the ~6 s return both fall between 30 s cycles. The
44 flaps that coincide with a fan change are simply the same event as a rest
boundary (fan `auto` ↔ `quiet` [D057]). Reordering the calls cannot help, and
that was checked and discarded. Comment:
https://github.com/meremortals70/abode-hvac-coordinator/issues/22#issuecomment-7119089291

A thread-based controller is planned that takes the IntesisBox out of the path.

### 3. Issue #23 fixed in 0.10.14 — the plan and the PID start from the measured room

- **No new D-record.** [D106] already required it ("the measured vapour pressure
  … and the measured temperature"); only the moisture half was implemented.
  0.10.14 made the code obey the rule it already had.
- `control.plan.start_temperature(measured, estimate)` — the reading wins, the
  filter's carried-forward estimate is used only where the room gives no fresh
  reading.
- Wired into `coordinator._plan_inputs` (`t_now`) and the PID's `t_est`.
- Tests: two pure (`StartTemperature`), one HA
  (`test_a_plan_starts_from_the_measured_room_not_the_estimate`, a filter forced
  to 20.0 against a sensor reading 25.0).
- **Verified:** pure 574 OK, sim 3 OK (486 s), CI run
  [`45306912346`](https://github.com/meremortals70/abode-hvac-coordinator/actions/runs/45306912346)
  green on all five jobs including both Home Assistant versions.
- Released: `main` and tag `v0.10.14` both at `c7b507d`; #23 closed `completed`
  with the test names and the CI run.

### 4. Defects found and NOT fixed (in the order I would take them)

1. **The filter does not track the room while the unit is idle** — the big one,
   below.
2. **Occupancy is not a heat or moisture source.** Mon 23 Nov: +2.6 g/kg of
   water in 45 minutes, two minutes after bed occupancy came on. The filter has
   a steady moisture source and a smooth two-harmonic heat pattern; neither
   knows about people.
3. **`number.<room>_setpoint` shows `unknown` while a head rests** — 143 rows
   in the living room, 18 in the office, in one week. `number.py:50` returns
   `None` when the head reports no target temperature. The commanded value is
   known (`sensor.<room>_commanded_setpoint`), so the control flashes to
   `unknown` about twenty times a day for nothing.
4. **Seven office control entities published an empty-string state `''`** at
   17 Nov 16:39:49, once, at setup. Invalid for a select or a number; cosmetic.
5. **Recorder churn:** `sensor.abode_hvac_coordinator_demand_forecast` writes
   17,287 rows a week (every 30 s, ~600 B of `intervals` attributes each) for a
   six-hour forecast that only changes with the plan. Candidate: throttle to the
   plan cadence, or keep `intervals` out of the recorder like `model`.
6. **`sensor.<room>_commanded_setpoint` is `unknown` for 38 hours of the week**,
   always while Automatic control is off. Probably intended (nothing is being
   commanded) but it writes 4,100 rows to say so. The user's intent is unstated.

## Next move

**Find out why the filter does not track the room.** Facts to start from:

- It is **not** paused. `_observe_and_learn` runs every cycle for every room,
  and `model.five_minutes(now)` steps the filter every five minutes, with or
  without Automatic control.
- The failure is one-directional (the estimate sits below the room) and decays
  over about an hour inside each autonomy spell:

  | Time into the spell | Gap (measured − plan's start) |
  | --- | --- |
  | 0–15 min | **+3.67 mean, +3.86 median** |
  | 15–30 min | +2.07 |
  | 30–60 min | +0.33 |
  | 1–2 h | +1.09 |
  | 2–4 h | +0.38 |
  | 4 h + | +0.20 (median +0.08) |

- Mon 23 Nov, in the raw trace: measured 29.38 vs the model's 23.48 at 21:10,
  closing over the following hour.

**Order of work, cheapest first**

1. **Two diagnostics downloads, one at +5 min and one at +30 min after
   switching Automatic control on.** `data.rooms[<id>].learned.heat.z[0]` is the
   model's room temperature and `learned.humidity.z[0]` its vapour pressure; the
   trace gives the measured index. That splits the gap into temperature and
   moisture in one shot, which the recorded history cannot do.
2. **Read `filter.py` for the innovation gate** (`FILTER_GATE_NIS = 10.83`). If a
   large correction is rejected outright, that alone explains a slowly-walked-in
   estimate; if Q is inflated instead, it is something else.
3. **Check `u`** — the unit input the filter is told about. With the Intesis box
   reporting `fan_only`/`off` and `hvac_action` unknown most of the day, the
   filter may be told the unit is doing nothing while the room is being heated
   by the sun and the house, and `s`/`m` (below) are frozen at their priors.
4. **Try to reproduce in the sim** — `tests/sim/intesis_loop.py` models the
   house's Intesis units (whole-degree reading and setpoint, its own thermostat)
   and is run as a regression by `tests/sim/test_intesis_loop.py`. If the sim's
   filter tracks through an idle day, the cause is in the real inputs.

**A second thread worth a look, not urgent:** `trace.model.figures` on 24 Nov
showed `s = 0.4` and `m = 0.1` — exactly their starting values — in **both**
rooms after three weeks, while `g0` and the harmonics differ between them. If
two figures have never moved, the heat balance is missing the two terms they
stand for, and the daily pattern is absorbing their work.

## Relevant files

- `custom_components/abode_hvac_coordinator/control/plan.py` — `start_temperature`
  (new), `PlanInputs.t_now`, `make_plan`, the index conversion at line ~437
- `custom_components/abode_hvac_coordinator/coordinator.py` — `_estimate_now`,
  `_plan_inputs` (`e_now` is already the measured vapour pressure),
  `_decide` (the PID's `t_est`)
- `custom_components/abode_hvac_coordinator/model/filter.py` — `start_state`,
  `_LOG_FIGURES`, `FILTER_GATE_NIS`, the correction step
- `custom_components/abode_hvac_coordinator/model/physics.py` — state indices
  (`I_T=0`, `I_LN_S=6`, `I_LN_M=7`, `I_LN_K=5`, `I_LN_VENT=27`, `I_DRY=28`)
- `custom_components/abode_hvac_coordinator/number.py:43-56` — `native_value`
  returning `None`
- `tools/_rev24/an.py`, `auto.py`, `deep.py` — the seven-day analysis
- `docs/decisions/D106.md` — the rule 0.10.14 made the code follow
- `tests/sim/intesis_loop.py` — the house's units, simulated

## Notes

### GitHub, without `gh`

- `GITHUB_TOKEN` is present and works. **Never interpolate it into a shell
  string.** Read it inside Python; print only facts (login, codes, URLs, counts).
- Publishing a local commit: `tools/_publish.py`, but it reads `GH_TOKEN`, so
  run it in-process:

  ```python
  import os, runpy, sys
  os.environ["GH_TOKEN"] = os.environ["GITHUB_TOKEN"]
  sys.argv = ["_publish.py"]
  runpy.run_path("tools/_publish.py", run_name="__main__")
  ```

  GitHub strips the message's trailing newline, so the rebuilt SHA differs.
  After moving the ref: `git fetch origin && git reset --soft origin/main`.
- Releases: `POST /releases` with `tag_name`, `target_commitish` = the commit,
  and the changelog section as the body. Then verify `/git/ref/tags/<tag>` and
  `/git/ref/heads/main` name the same commit.

### The CRLF trap, corrected (24 Nov)

The old note said `git -c core.autocrlf=true` avoids the whole-file rewrite. **It
does not here.** The worktree files are CRLF and the blobs LF; with
`core.autocrlf=true` the add still stored CRLF, and the staged diff was 1,524
lines of noise on a 19-line change.

What works:

```sh
python3 - <<'PY'
for f in ("CHANGELOG.md", "path/to/file.py"):
    b = open(f, "rb").read()
    open(f, "wb").write(b.replace(b"\r\n", b"\n"))
PY
git -c core.autocrlf=false add <the files you changed>
git diff --cached --numstat   # must show only your lines
```

Four files were normalised to LF on 24 Nov and are LF in the worktree now:
`CHANGELOG.md`, `control/plan.py`, `tests/ha/test_cycle.py`,
`tests/pure/test_control.py`, and `manifest.json`. Check with
`git ls-files --eol <path>` before assuming.

### Diagnostics

The download is `config_entry-abode_hvac_coordinator-<entry>.json` and its
payload is under **`data`** (not the top level): `data.rooms[<id>].learned.heat.z`
is the state vector, `data.commands` the last 200 commands with `by_user`. See
`project.md` → "Diagnostics download".

### Tests

- Pure: `python3 -m unittest discover -s tests/pure -t .` — 574 tests, ~110 s.
- Sim: `python3 -m unittest discover -s tests/sim -t .` — 3 tests, ~490 s.
- `ruff` and `tests/ha` cannot run in this sandbox. CI is the only runner for
  those; watch it with the API (`/actions/runs?head_sha=<sha>`).
- PowerShell/CI both print "Exited with code 1" on stderr even when the suite
  passes — always read the `OK` line.

## This environment (Linux sandbox)

- Python 3.14 and git are installed. **No** `ruff`, `pytest`,
  `pytest-homeassistant-custom-component`, `sqlite3`, `gh`.
- `GITHUB_TOKEN` is present (works — authenticated as `meremortals70`).
- The workspace is a copy of the user's Nextcloud folder; files the user
  uploads appear in `tools/` after Nextcloud syncs (the tarballs appeared
  mid-session on 24 Nov, several minutes after they were sent).
- Analysis scratch lives in `/tmp/rev24/` (csv, tarball extract) and is lost on
  restart; the scripts survive in `tools/_rev24/`.
- **PowerShell on the user's machine is slow and the Windows `python` name is
  shadowed by the Store alias.** Run long commands in the background and report
  when they finish.
