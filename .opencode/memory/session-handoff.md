# Session handoff

> Last updated: 24 Nov 2026 (end of session)

## Current task (later on 24 Nov 2026)

**The setpoint control (#29) is being finished after a rollback.** `v0.10.16`
and its release were deleted on the user's instruction; the latest published
release is `v0.10.15` (`695f8c0`). `main` still carries the 0.10.16 work, the
manifest was set back to 0.10.15, and the changelog heading is
`0.10.16 - unreleased`.

### What the fix now does

- `number.<room>_setpoint`, while Automatic control is on and the head reports
  no setpoint: the setpoint last sent; with nothing sent since the spell began,
  the band's own centre (`comfort_result.centre_c`); blank while the room is
  being stopped (`off`/`none`), after a failed cycle, and whenever the user is
  in charge.
- The figure moved from `RoomRun.commanded_c` to `RoomState.commanded_c`, so it
  is saved with the room and a restart no longer blanks the control
  (`memory.py` round trip covered in `tests/pure/test_memory.py`). It is
  cleared when Automatic control goes off, and only the room's **first** head's
  setpoint is recorded (the control mirrors the first head).
- The step checked is `run.limits_result` (what the short-cycle guard allowed),
  not `run.step` (what was proposed).
- A house whose temperature unit cannot be converted now still shows the room's
  hold, which is already in C.

### Reviews already applied

The fresh-eyes pass on the shipped 0.10.16 found: the restart blank (BLOCKER,
fixed), the unknown-unit return suppressing the hold, `commanded_c` not cleared
on take-charge, the gate reading the pre-guard step, no `run.failed` check, the
two docs still describing the old rule, and the missing `### Fixed` entry. All
are in this change. It also caught `tests/ha/test_handed_on.py:72` still
reading `run.commanded_c`.

### Still to do

1. CI green on the commit (HA tests and the per-file coverage gate run only
   there).
2. One release, 0.10.16, **only after the run reports and the user says so.**
3. `.opencode/memory` was corrected on the way: 0.10.15 is the latest release,
   not 0.10.14.

## Earlier: current task

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
- Released: `main` and tag `v0.10.14` both at `c7b507d`; #23 closed `completed`.
- **Correction, same day.** The evidence published with #23 — "the plan starts
  3.7 comfort-index points below the measured room" — was **misaligned and is
  withdrawn**. `decision.index[0]` is the forecast **30 minutes ahead**
  (`evidence.py:144` samples `points[5]`), not the room now, so that comparison
  measured the room's expected cooling. The changelog, the release body and #23
  all carry the correction. The change stands on [D106] alone, and no claim
  about a wrong estimate is made.

### 4. Defects found and NOT fixed (in the order I would take them)

1. **The plan is not carried out.** The big one, below: when the plan says
   cool, the component cools for less than a quarter of the half hour 56% of
   the time. The model is not at fault — a plan that *is* carried out is
   forecast to +0.12 index at 30 minutes.
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

### 5. Occupancy as a learned moisture source (#27, [D113]) — built, unreleased

Two commits: `8e1fffb` (the term) and `d658650` + `73c0122` (what the review
found), all green on CI run
[`45316157695`](https://github.com/meremortals70/abode-hvac-coordinator/actions/runs/45316157695).

What it does: `q_occ`, a figure learned only from minutes the room was occupied,
in the moisture balance; carried into the plan's six hours and the trust check's
forward run; the state vector 6 → 7 with a **migration** that keeps a saved
model (a length mismatch otherwise discards the whole filter).

**The review changed it materially.** Correctness found the forecast path was
not wired (the term was learned and never priced). Fresh eyes found the worse
one: a room with no presence sensor counts as occupied for ever
(`comfort.evaluate`), so `q` and `q_occ` are unidentifiable; pinned true for four
days, `h` was dragged from 0.500 to 0.365/h and `q_occ` doubled. Now the figure
learns, and the source is priced, only once the room has been seen both occupied
and empty — so such a room behaves exactly as before.

Also fixed from the review: a quiet step's figure is asserted not to move;
diagnostics decodes `moisture {h, q, drying, cooling, rebound, occ, identified}`;
the first cycle after a restart uses the restored occupancy; `minute()` defaults
to true as `comfort.evaluate` does; both sims pass a 18:00–08:00 pattern.

**Open, noted, not filed:** `H_Q` is unbounded (linear), so `q` can go transiently
negative across a Leave; a clamp was suggested and not done. And `q_occ` is one
number for all occupied minutes, so a 07:00 shower is smeared over fourteen
hours — the term can match an evening's level, never its shape.

### 6. Second review pass on the release — the feature was inert (24 Nov)

The fresh-eyes pass on `v0.10.14..HEAD` found that the identifiability guard
added in the first review **deadlocked**: `_moisture_minute` suppressed the
occupied flag until `occupancy_identified`, and that needs an occupied minute —
so `q_occ` never learned and the plan never priced it. The changelog claimed a
behaviour that did not exist.

Two latent faults sat behind it, both released by the obvious fix: the ten-day
**seed replay** (`memory.py`) claimed the room was occupied throughout, and the
**first cycle after a start** recorded an empty minute (`bool(None)`), sticky and
enough to unlock the source for a room with no presence sensor.

Also from that pass: the trust check's forward run now carries the source; the
identifiability gate lives in one accessor (`HumidityFilter.priced_occ`) instead
of three copies; `docs/architecture.md` describes the term in all three places;
`known-limitations` states the approximation in both directions; the `seen` flags
have a round-trip test.

**The simulation failed when the term went live, and the failure was read, not
reverted.** 24 minutes below band, all of them 08:00-09:00 on day 1 — the hour
the source first activates and is priced at its 1.0 hPa/h guess. Days 2-3 were
100%, the figure learned to 0.26. Half the failure was the fixture: the sim told
the model occupancy at night and the plan "occupied" all day, which production
never does. Both sims now give the plan and the model the same occupancy.

Committed `a5edd38`; CI run
[`45324252038`](https://github.com/meremortals70/abode-hvac-coordinator/actions/runs/45324252038)
green on all five jobs.

**Lesson worth keeping:** a test that cannot fail is not evidence. The feature
passed 584 pure tests and a 3-test simulation while doing nothing, because no
test drove the live path. Two tests through `RoomModel` now do.

### 7. 0.10.15 released (24 Nov 2026, `695f8c0`)

Contains #25 (demand forecast cadence), #27 (the occupancy moisture source, after
two review passes), #26's test gap, [D114] (the plan expects the room's own
hours) and the settle gate. Tag, release and `main` all name `695f8c0`.

**Attempted and reverted, with the measurement recorded:** [D115] priced the stop
a plan makes, to damp the churn in #24. Charging every stop in the horizon at half
a start dropped the settled day of the whole-degree simulation to 73.4% in band
against 85%; charging only the first step at 5% still dropped the loop to 97.7%
against 99%. Both reverted. #24 is closed with that evidence and the reopen
conditions: a metered compressor to count starts, or a plan cost on a horizon that
does not slide.

**The rule that would not hold up without measurement.** Three of today's changes
were judged by the simulation rather than by reasoning: the q clamp (wrong), the
stop cost (wrong), and the fixture inconsistency (right). Each looked defensible
on paper.

## Next move

**Why is a plan not carried out?** This is the question left standing after the
correction. The model itself is calibrated.

24 Nov, a fresh plan's forecast at +30 minutes against the measured index at
+30 minutes:

| Plan's first action | Mean error (positive = warmer than forecast) | Optimistic | n |
| --- | --- | --- | --- |
| rest | **−0.32** | 33% | 451 |
| dry | +0.92 | 90% | 726 |
| cool | +2.85 | 93% | 839 |

and the cool plans split by how much of the half hour was actually spent
cooling:

| Time cooling | Mean error | n |
| --- | --- | --- |
| < 25% | +4.23 | 468 |
| 25–60% | +1.98 | 129 |
| 60–95% | +1.06 | 142 |
| **95–100%** | **+0.12** | 100 |

When the component does what the plan said, the forecast is right to a tenth of
a point. The error is the distance between plan and execution — and `dry` shows
the same shape (+0.02 at 95–100%).

Churn: 1,847 plans in the week, **231 first-action changes** (dry↔rest 186 of
them), median action lifetime 12 minutes, a new plan about every 5.3 minutes.

Hypotheses, cheapest first:

1. **The replan overwrites the plan before it can run.** `run.replan` is set by
   the 5-minute timer and `plan.first` is what the PID holds, so a new plan
   every five minutes means no 30-minute intent is ever executed as forecast.
2. **The short-cycle guard** (min run 10 / rest 5) holds a stop the plan asked
   for, or a start, so the executed action differs from the allowed step.
3. **The limits override the kind** — `empty_room`, `leave`, `opening`,
   `automatic_control` set the step to off or rest whatever the plan wanted.
4. **Delivery reads a rate below the unit's floor as the floor**, so a "cool at
   0.4 C/h" becomes a minimum run that moves more heat than the forecast
   assumes.

Test first: an HA test that holds a plan whose first action is cool, ticks the
coordinator twice, and asserts the executed action is still cool — that a second
plan does not silently replace a live one. If it passes, move to the guard and
then the limits.

**Do not change the model. It is not the problem.**

### A second thread, not urgent

`trace.model.figures` on 24 Nov showed `s = 0.4` and `m = 0.1` — exactly their
starting values — in **both** rooms after three weeks, while `g0` and the
harmonics differ between them. If two figures have never moved, the heat balance
is missing the terms they stand for and the daily pattern is absorbing their
work.

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

- Pure: `python3 -m unittest discover -s tests/pure -t .` — 600 tests, ~130 s.
- Sim: `python3 -m unittest discover -s tests/sim -t .` — 3 tests, ~505 s.
- **`ruff` and `tests/ha` DO run here** from temporary venvs (see
  `environment.md`). That is the correction of 24 November 2026: my own two
  setpoint tests were asserting nothing, and only running the HA suite here
  showed it. Run it before pushing, not in CI afterwards:
  `. /tmp/ha/bin/activate` (or use the absolute path) and
  `python -m pytest tests/pure tests/ha -q --cov=... --cov-branch`.
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
