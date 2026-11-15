> Last updated: 15 Nov 2026 (end of session)

## Current task

0.10.11 is out. The next session checks it in service: the **office** first,
because dry is what changed, then the **living room**. The per-unit power
meters are the outstanding hardware.

## What's done

- **0.10.11** (15 Nov): a unit's own power meter is a compressor source
  [D110]; the short-cycle guard times the minimum run from the compressor, not
  the command; a refused stop keeps the running command whole, fan included;
  the learned curves reach the recorder. Issues #15-#18, all closed.
- **The measurement that decided it** (office, 14 Nov): the plan asked
  `dry 0.81` at 17:15:15, the house load read 695 W before and 694 W a minute
  after, the compressor never started, the comfort index rose 27.94 → 28.11
  over the ten minutes the guard held the stop, and in the same cycle the
  plan's blocked reason said the unit stopped less than its minimum rest ago.
  Numbers in #16 and D110.
- **The ladder** now reads: head off/fan-only, frequency sensor, meter,
  `hvac_action`, house load, unknown. A metered unit's draw is measured (the
  reading above its resting level), not inferred.
- **Curves are recorded**: `sensor.<room>_learning` carries `curves` and
  `efforts` (cool, heat, dry in °C/h); `sensor.abode_hvac_coordinator_learned_draw`
  carries each outdoor unit's draw curve. The mode sensor's `model` attribute
  stays out of the recorder at 2-6 KB a cycle.
- **CI**: the first run of the release failed on both Home Assistant jobs
  because `tests/ha/test_entities.py` asserts the exact house entity set and
  the new sensor was not in it. Fixed in a second commit; that red run
  (`43681440155`, on `7bc9f16`) was left in the Actions tab because the token
  here lacks Actions write — a permission problem, not a permanent one. The
  commit that carries the fix was then rewritten to remove a byte-order mark
  from its subject, which `b89defc` and `46333d1` did not survive: `main`, the
  tag and the release now name the rewritten commit, and the run and SHA
  quoted in the release and the issues are the rewritten ones.
- 561 pure tests, 3 simulation tests, `ruff` clean. `tests/ha` cannot run on
  this machine; the CI is the only HA runner.

## Next move

**Office, on 0.10.11.** Export a window with `tools/export_window.sh` on the
HA host (archive copy in `tools\`), upload the tarball to `tools\`, and check:

- a `dry` (or `cool`) command the compressor does not follow is released
  inside a cycle or two instead of being held for ten minutes — the house has
  no measured source until the meters arrive, so this depends on the load step
  seeing the stop, which is the best effort the ladder has for now;
- `sensor.office_learning` attributes: `curves.cool`, `curves.heat`,
  `curves.dry` and `efforts` present in the recorder rows;
- `sensor.abode_hvac_coordinator_learned_draw` lists the units with their
  effort/kW/readings triples.

**Living room, tonight.** Same export for 21:00 → 08:00 local = UTC
`11:00:00` → `22:00:00` of the same calendar day.

**Meters.** One per isolator switch: the office circuit, the living-room
circuit, and the multihead's outdoor circuit. Configure each on the head's
air-conditioning screen once installed; every head on one outdoor unit names
the same sensor, or the save is refused.

## Relevant files

- `custom_components/abode_hvac_coordinator/observe.py` — the compressor
  ladder, `UnitMeters`
- `custom_components/abode_hvac_coordinator/limits.py` — `HouseGuard.apply`,
  `_seen`, `_keep_running`
- `custom_components/abode_hvac_coordinator/model/unit.py` — `DrawLearner`,
  `curve()`
- `custom_components/abode_hvac_coordinator/sensor.py` — `learned_draw`, the
  Learning sensor's attributes, `_UNRECORDED`
- `docs/decisions/D110.md`

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
- Push mechanics: `tools/_publish.py` rebuilds the local HEAD on GitHub and
  moves `main`; `tools/_publish_force.py` is the same with `force: true`, for a
  rewritten commit. GitHub strips the message's trailing newline, so the
  rebuilt SHA differs — move the ref, then `git fetch` + `git reset --soft
  origin/main`.
- Write a commit message file with
  `[System.IO.File]::WriteAllText($p, $text, (New-Object System.Text.UTF8Encoding($false)))`.
  PowerShell's `Set-Content -Encoding UTF8` writes a BOM, which lands in the
  subject line.
- `tests/ha` is the only suite that cannot run locally.
