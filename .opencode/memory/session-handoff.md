> Last updated: 12 Nov 2026

## Current task

0.10.7: an opening is an input to the room's heat balance, and free cooling is
the plan's own counterfactual [D105]. Code and docs written, pure suite green
(530 tests).

## What's done

- `vent` (`I_LN_VENT = 27`, `STATE_SIZE = 28`) added to `physics.py`,
  `filter.py` (learned only while an opening is open; 27-state saves
  migrated), `forecast.py` (`drift_now`, `ForecastInput.opening`, per-opening
  `exact_step` cache).
- The 10-minute hold and the at-once stop removed from `plan.py`,
  `delivery.py`, `limits.py`, `coordinator.py`; `psychro.free_cooling`
  reduced to `outdoor_air_dry` (the dew-point constraint).
- `plan._opening_advice` compares the room with an opening open and the unit
  resting against the plan, at every half-hour, on the plan's own cost, and
  needs the opening to move the index by more than the forecast's sigma.
- D105 rewritten, D073 superseded, architecture §4.1/§4.2/§4.6 rewritten,
  CHANGELOG 0.10.7, manifest 0.10.7.
- Night of 11–12 Nov exported and reviewed; the run commands and gotchas are in
  `project.md` under "Night review workflow (Living Room)".

## Next move

Committed and tagged v0.10.7. Next: update the component in HA, restart, and
watch the Living Room's `vent` figure and its Free cooling attributes for a
day. A restored model, not a relearnt one, is the sign the 27 -> 28 state
migration worked.

## Relevant files

- `custom_components/abode_hvac_coordinator/model/physics.py` — heat balance
- `custom_components/abode_hvac_coordinator/control/plan.py` — `_opening_advice`
- `docs/decisions/D105.md`
- `.opencode/memory/project.md`

## Notes

- Tests: `py -3 -m unittest discover -s tests/pure -t .` (~3 min). No pytest
  locally, so `tests/ha/` cannot be run here.
- The script that runs on HA lives at `/config/export_night.sh`; the repo copy
  is an archive. `tools/` is git-ignored.
- The full design specification remains in `docs/architecture.md`.
- Numbered design rules remain in `docs/decisions/D###.md`.
