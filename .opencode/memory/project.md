# Abode HVAC Coordinator — working notes for agents

> Project memory for Abode HVAC Coordinator. Update at the end of each session.

## Current objective

Maintain and extend the Abode HVAC Coordinator Home Assistant custom integration. It learns a thermal model of each room and decides how to run that room's air conditioners: a plan every 5 minutes and a PID every 30 seconds.

## Active work

- **0.10.14 is in service (released 24 Nov 2026, `c7b507d`).** The plan and
  the PID now start from the measured room, not the model's estimate of it
  (#23, [D106]). Tag `v0.10.14`, release and `main` all name `c7b507d`.
- **The standing technical question: the filter does not track the room while
  the unit is idle.** Over 17–24 Nov the plan started each night's automatic
  run **3.7 comfort-index points below the measured room** (median 3.86, worst
  5.9 at Mon 23 Nov 21:10 — 29.4 measured against 23.5 from the model), and
  agreed to within 0.08 only after four hours. **The filter is not paused for
  this**: `_observe_and_learn` runs for every room every cycle with no
  Automatic-control gate, and `model.five_minutes()` steps the filter every
  5 min. So this is a *tracking* failure, not a stall. Suspects, untested:
  - the innovation gate `FILTER_GATE_NIS = 10.83` rejecting a large correction
    as an outlier and waiting for the model's own dynamics to catch up;
  - the humidity state (the index is 1/T vapour pressure; the diagnostics
    showed model and reading agreeing at a calm moment, so this is unproven);
  - the daily heat pattern absorbing what the physics should explain.
  **0.10.14 only stops the estimate reaching the unit; it does not fix this.**
  Next session's job, with a test that fails first.
- **One change a day** (stated 17 Nov). Do not stack proposals, commits or
  releases in a single day. 24 Nov's change was 0.10.14.
- **Occupancy is a heat and moisture source** (agreed 24 Nov, not built). On
  Mon 23 Nov the living room gained ~2.6 g/kg of water in 45 minutes, starting
  two minutes after bed occupancy came on. The filter has a *steady* indoor
  moisture source (`HUMIDITY_START_Q_HPA_PER_H`) and a smooth two-harmonic
  daily heat pattern; neither knows when someone gets into bed. Per [D104]
  this is a missing term, so give the model the term — do not widen a band.
- **Queued behind that:** the `number.<room>_setpoint` shows `unknown` while a
  head rests (143 rows/week living room, 18 office), because `number.py:50`
  returns `None` when the head has no target temperature.

## Night review workflow (Living Room)

Verified 12 Nov 2026, and used again for the 17–24 Nov seven-day review.

`/config/export_window.sh` (archive copy: `tools\export_window.sh`) exports any
room over any window. Arguments: room substring, output tag, start UTC, end UTC,
and a label for the summary:

```sh
sh /config/export_window.sh living_room living_2026-11-24 '2026-11-17 06:00:00' '2026-11-24 00:34:09' 'Tue 17 Nov 16:00 -> now'
```

It writes `abode_<tag>.csv`, `abode_<tag>_summary.txt` and `abode_<tag>.tar.gz`
and prints `OK: upload <path>`. `/config/export_night.sh` is the older
living-room-only script, still on the host.

**The script that runs lives on the HA host**, not on Windows; the copies in this
repo are archives only — `tools/` is git-ignored.

Run it in the HA SSH add-on (Advanced SSH & Web Terminal). It needs `sqlite3`
(`apk add sqlite`). Windows are **in UTC** on the command line. Brisbane is +10
with no daylight saving, so local 21:00 → 08:00 is UTC 11:00 → 22:00.

For a window that ends "now", pass `"$(date -u '+%Y-%m-%d %H:%M:%S')"` as the
end argument — the script feeds it to `strftime('%s', ...)`, which accepts it.

### Gotchas

- **A pasted script arrives with CRLF and will not parse.** Run
  `sed -i 's/\r$//' /config/export_window.sh` before the first use, then
  `sh -n` it.
- The tarball lands in `/homeassistant`, not `/config`, whenever that directory
  exists. Take the path from the `OK:` line.
- The summary's `window local:` line is a hardcoded `echo`. Ignore it.
- `abode_night.csv` (the old script) begins with a stray `10000` line from
  sqlite3. The window script's csv does not.
- A seven-day export is ~60 MB of csv, ~3–4 MB gzipped. A night is ~0.15–0.6 MB.
- The recorder keeps 10 days by default, so a seven-day review must run before
  the first day purges.

### Review metrics

- **The band is in comfort index, not dry-bulb** [D009]: `band.low` / `band.high`
  on `sensor.<room>` are Steadman apparent temperature. `band.centre_c` is the
  dry-bulb equivalent and moves with the room's humidity — do not read it as
  the band moving.
- **Judge only the cycles where Automatic control was on**, and only where a
  band was actually in force: a room unoccupied, `left`, or with automatic
  control off has `band: null`, and carrying the last band across those gaps
  inflates every percentage (this bug was made and fixed on 24 Nov).
- Dry-bulb (`sensor.<room>_temperature`) updates only when the value changes —
  every few minutes at night, hours apart by day. Do not compute rates from it
  over short windows.
- `band_rule` says why a band applies (`sleep`, `occupied`, `unoccupied`, `left`).
- Count setpoint writes (`number.<room>_setpoint`), mode changes
  (`select.<room>_unit_mode`, `climate.<room>_aircon`) and fan changes
  (`select.<room>_fan_speed`).

### The review scripts

`tools/_rev24/` holds the seven-day analysis used on 24 Nov — `an.py` (per-day
table, rules, commands, plan, curves, gaps), `auto.py` (everything restricted to
Automatic-control intervals) and `deep.py` (commanded setpoint, flap context,
compressor sources). They take a csv path and a room prefix.

## Recently completed

| Date | What |
| ---- | ---- |
| 24 Nov 2026 | 0.10.14 released (`c7b507d`): the plan and the PID start from the measured room (#23, [D106]) |
| 24 Nov 2026 | #22 closed `not_planned`: the head is an IntesisBox reporting the indoor unit's state a few seconds behind; reproduced with no command sent |
| 24 Nov 2026 | Seven-day review of 17–24 Nov, autonomy-only; living room 86.6% in band overall, 88.5% of 47h02m asleep |
| 18 Nov 2026 | 0.10.13 released (`de4d6c6`): the forced stop names its rule; an interior door is an opening for the stop only [D111] |
| 17 Nov 2026 | Daily review of 16–17 Nov; the office door stop verified as [D050] working as designed |
| 15 Nov 2026 | 0.10.11 released: an outdoor unit's own meter is a compressor source [D110] |

## Decisions

| Date | Decision | Why |
| ---- | -------- | --- |
| Design | Comfort index (HCI) is Steadman apparent temperature with wind zero | Occupants feel humidity, not just dry-bulb temperature |
| Design | Two-store thermal model (air + mass) | Captures building inertia and solar gain |
| Design | No runtime dependencies | `requirements` is empty; stdlib only |
| 12 Nov 2026 | An opening is an input to the heat balance with a learned coupling (`vent`) [D105] | A sealed double-brick room cannot use outdoor air, so the model must say so rather than a rule |
| 24 Nov 2026 | The present is measured; the future is modelled [D106] | The filter's estimate can be degrees from the room, and a plan that starts there rests when the room is already above its band. **D106 already said this**; 0.10.14 made the code obey it |

## Model facts (do not re-litigate)

- `k` **is** in the heat model and **is** learned: `physics.py:4`
  (`dM/dt = c (T - M) + k (T_out - M) + m S`), state index `I_LN_K = 5`
  (`physics.py:33`), started at 0.1/h and estimated by the filter.
- `h` is the moisture exchange with outdoor air, also learned
  (`humidity.py:158`, `docs/architecture.md:164`).
- `vent` (`I_LN_VENT = 27`, `STATE_SIZE = 34`) is the air an opening changes
  with outdoors, per hour [D105]. It is learned only from minutes an opening
  was open; a room with no outdoor openings keeps its start value (the living
  room's is exactly 3.0/h, never used). The office's learned value is
  **0.087/h** — a sealed room, and evidence [D111] is keeping the hall door out.
- The plan starts from the **measured** vapour pressure (`e_now`) and, since
  0.10.14, the **measured** temperature `control.plan.start_temperature`.
  The filter supplies the figures the forecast moves with, not the start.
- `s` and `m` in `trace.model.figures` sat at exactly `0.4` and `0.1` — their
  starting values — in **both** rooms on 24 Nov, after three weeks of running,
  while `g0` and the harmonics differ between rooms. Unexplained; possibly
  unobservable at night (no sun), possibly a prior that never moves. Worth a
  look alongside the tracking question.
- Free cooling is **not** a rule: the plan carries the room forward with an
  opening open and the unit resting, beside the plan (`plan._opening_advice`).
- Announcements are blocked while the room is asleep, and "asleep" is the
  Comfort mode or the sleep schedule (`comfort.py:115-122`), not a clock.

## Diagnostics download

- Settings → Devices & Services → Abode HVAC Coordinator → ⋮ → Download
  diagnostics. It lands as `config_entry-abode_hvac_coordinator-<entry>.json`.
- **The payload is under `data`**, not at the top level: `data.rooms[<id>]` →
  `settings`, `learned` (`heat`/`humidity` filter states, with the `z` vector),
  `plan`, `trace`, `arbitrary`. `data.commands` is the last 200 commands, with
  `by_user` — the only way to tell a user's command from the component's.
- `trace.model.figures` decodes the state vector into a, b, c, k, s, m, g0 and
  the harmonics; `trace.model.curves` gives the cool/heat/dry curves in C/h.

## Blockers

- None. `GITHUB_TOKEN` is present and works (`gh` is not installed).

## Known issues

See `docs/known-limitations.md` and GitHub issues at `https://github.com/meremortals70/abode-hvac-coordinator/issues` (none open as of 24 Nov 2026).

## Glossary

| Term | Meaning |
| ---- | ------- |
| HCI | Human Comfort Index, derived from Steadman apparent temperature |
| TiTe model | Two-store thermal model: room air + building mass |
| Plan | 5-minute lookahead decision |
| PID | 30-second setpoint adjustment |
| Pure layer | Decision modules with no Home Assistant imports |
| D-record | Numbered design rule in `docs/decisions/D###.md` |
| Autonomy | Automatic control on; the component is in charge of the unit |
