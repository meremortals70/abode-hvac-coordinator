> Last updated: 12 Nov 2026 (end of session)

## Current task

0.10.7 is out; the next session checks it in service — the **office** after its
daytime run, the **living room** after tonight's run.

## What's done

- 0.10.7 released: tag `v0.10.7`, release notes, issue #5 documented and closed
  with `Closes #5` in the notes. `main` at `9066f75`; the only uncommitted
  changes are this session's memory files, and `Long-lived` is untracked and
  not ours.
- The change: an opening is an input to the heat balance (`vent`, state 27 → 28),
  no hold and no at-once stop, free cooling is the plan's own counterfactual
  [D105]. 530 pure tests OK before the commit; `tests/ha` updated but never run
  (no pytest here).
- Living Room FP2 reviewed and configured: `presence_sensor` =
  `binary_sensor.presence_sensor_fp2_6a04_presence_sensor_2`, occupied 2 min,
  **empty 120 min**. Numbers and reasoning in `project.md`, section
  "Living Room presence (Aqara FP2)".

## Next move

**Office, daytime run.** Export the office's window with `tools/export_night.sh`
on HA (set `START`/`END` in UTC around the run, `sh /config/export_night.sh`),
upload the tarball to `tools/`, and check:

- the room's heat model was **restored, not relearnt** (a relearnt one resets
  its forecast trust);
- `vent` sits at its starting 3.0/h and never acts, since the office has no
  opening sensor;
- free cooling is never advised for a sealed room, whatever outdoors is doing;
- nothing is held: a plan to cool starts at once.

**Living room, tonight.** Same export for 21:00 → 08:00 local, which is UTC
`2026-11-12 11:00:00` → `2026-11-12 22:00:00`:

- `vent` unchanged all night (the window was shut) and no free-cooling advice;
- the plan responds to the sleep band as before, with no 10-minute hole at the
  first start;
- the room's presence reads `on` throughout — if it drops for more than 120
  minutes the room will count as empty and the unit stop, which is the signal
  to pull the sensor back out.

## Relevant files

- `custom_components/abode_hvac_coordinator/model/physics.py` — the heat balance
- `custom_components/abode_hvac_coordinator/control/plan.py` — `_opening_advice`
- `docs/decisions/D105.md`, issue #5
- `tools/export_night.sh`, `tools/export_presence.sh`
- `.opencode/memory/project.md`

## Notes

- Tests: `py -3 -m unittest discover -s tests/pure -t .` (~3–5 min). PowerShell
  prints "Exited with code 1" on stderr even when the suite passes — read the
  `OK` line.
- The script that runs on HA lives at `/config/export_night.sh`; the repo copy is
  an archive. `tools/` is git-ignored. The tarball lands in `/homeassistant`
  whenever that directory exists — take the path from the `OK:` line.
- `abode_night.csv` starts with a stray `10000` line; skip it before parsing.
- The band is in comfort index, not dry-bulb [D009]. Never compare
  `sensor.living_room_temperature` with band numbers.
- The full design specification remains in `docs/architecture.md`; numbered
  decisions in `docs/decisions/D###.md`.
