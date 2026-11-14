> Last updated: 14 Nov 2026 (end of session)

## Current task

0.10.10 is out. The next session checks it in service: the **office** first,
because dry is what changed, then the **living room**.

## What's done

- **0.10.8** (13 Nov): the plan forecasts from the measured room [D106]; the
  mode sensor names its grace [D107]; `occupied_after`/`empty_after` renamed
  to `occupied_grace`/`empty_grace` with a read-once migration. Issues #6-#8.
- **0.10.9** (14 Nov): dry priced at its own share of the cooling floor
  [D108]; `empty_grace` shown in a sleeping room; `decision.blocked` records
  why the plan's first step could not be one of the kinds. Issues #9-#11.
- **0.10.10** (14 Nov): dry runs on a **curve of its own** [D109], which
  supersedes D108's share; the plan chooses the dry rate; `delivery` asks for
  the rate chosen; a power ceiling is read on the effort a dry rate asks.
  Issues #9 (reopened and closed), #12, #13. `main`, tag and release all on
  `1415186`.
- **The CI caught a hole in the simulation** (#14): the simulated unit had no
  dry mode, so a dry command was learned as cool at that effort and the plant
  delivered cooling's rate. `SimRoom` now takes `command_kind` and in dry
  delivers 0.4 of what cooling delivers at the same effort (the office's
  measurement). With it the loop reads 100 %/100 %/100 % in band, dry chosen
  twice. The tag was moved to `1415186` so tag, main and release agree.
- 547 pure tests, 3 simulation tests (`tests/sim`, ~16 min), `ruff` clean.
  `tests/ha` cannot run on this machine; the CI is the only HA runner, and it
  is green on `1415186` (all five jobs).

## The measurement that decided it

The office was put in dry manually on 14 Nov, 11:03-12:23, setpoint 18, doors
shut, room empty: **28.09 → 24.33 °C = −2.56 °C/h**, 63 % → 43 % RH, house
load 0.9 → 2.8-3.0 kW, the unit's own reading 28 → 24 (effort 6-7).

At the floor — the only dry the component used to ask for — the same room
gained **+0.14 °C/h** (12 Nov, effort 0-2) and the living room **+0.65 °C/h**
(13-14 Nov, effort ~1). One share of the cooling curve cannot say both; a
curve can. Numbers in issue #9, D108 and D109.

## Next move

**Office, on 0.10.10.** Export a window with `tools/export_window.sh` on the
HA host (the archive copy is in `tools\`), upload the tarball to `tools\`, and
check:

- `model.curves.dry` in the diagnostics (or the mode sensor's `model`
  attribute) — it starts at 0.4, 0.8, 1.2, 1.6, 2.0, 2.4 °C/h and should move
  up on a room that cools while dry runs;
- the plan choosing dry at a rate above its first step, not only at the floor;
- `number.office_setpoint` during a plan-chosen dry run: it should now be the
  setpoint for the rate chosen, not always the floor's;
- the room's heat model restored across the restart, not relearnt
  (`sensor.office_learning` unchanged).

**Living room, tonight.** Same export for 21:00 → 08:00 local = UTC
`2026-11-13 11:00:00` → `2026-11-13 22:00:00` for the previous night; for
tonight use the date of the run:

- in-band share, and whether the 00:50-class excursion (plan on dry with 0
  minutes outside forecast) is gone;
- `empty_grace` should appear if presence drops at night;
- `decision.blocked` should say why any rest happened.

## Relevant files

- `custom_components/abode_hvac_coordinator/model/physics.py` — the curves
- `custom_components/abode_hvac_coordinator/model/filter.py` — `acting`, the
  state migration
- `custom_components/abode_hvac_coordinator/control/plan.py` — `dry_rates`,
  `draw_of`, `_first_bar`
- `custom_components/abode_hvac_coordinator/delivery.py` — the dry setpoint
- `docs/decisions/D108.md`, `D109.md`, issue #9
- `tools\_office_trial.py` — the trial analysis (start/end as arguments)
- `tools\_publish.py`, `tools\_release_1010.py` — the API push and release

## Notes

- Tests: `py -3 -m unittest discover -s tests/pure -t .` (~3 min). PowerShell
  prints "Exited with code 1" on stderr even when the suite passes — read the
  `OK` line.
- The script that runs on HA lives at `/config/export_window.sh`; the repo copy
  is an archive. `tools/` is git-ignored. The tarball lands in
  `/homeassistant` — take the path from the `OK:` line.
- Windows on that command line are **UTC = local − 10 h**.
- The band is in comfort index, not dry-bulb [D009]. Never compare
  `sensor.<room>_temperature` with band numbers.
- Push mechanics: fine-grained PAT in `GITHUB_TOKEN` (needs "Workflows: write"
  for `.github/workflows/`); GitHub strips the message's trailing newline, so
  the rebuilt SHA differs — move the ref, then `git fetch` + `git reset
  --soft origin/main`.
- `tests/ha` is the only suite that cannot run locally.
