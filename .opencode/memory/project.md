# Abode HVAC Coordinator — working notes for agents

> Project memory for Abode HVAC Coordinator. Update at the end of each session.

## Current objective

Maintain and extend the Abode HVAC Coordinator Home Assistant custom integration. It learns a thermal model of each room and decides how to run that room's air conditioners: a plan every 5 minutes and a PID every 30 seconds.

## Active work

- **0.10.14 is in service (released 24 Nov 2026, `c7b507d`).** The plan and
  the PID now start from the measured room, not the model's estimate of it
  (#23, [D106]). Tag `v0.10.14`, release and `main` all name `c7b507d`.
- **The model is calibrated; the plan is not followed.** Measured correctly on
  24 Nov, the forecast is accurate whenever its plan is carried out: at
  +30 minutes the mean error is **+0.12** for a cool plan held 95–100% of the
  half hour, **+0.02** for dry, **−0.32** for rest. The large errors appear only
  where the executed action is not the planned one.
  **An earlier claim in these notes — "the plan starts 3.7 index points below
  the room" — was wrong and is withdrawn:** `decision.index[0]` is the forecast
  thirty minutes ahead (`evidence.py:144` samples `points[5]`), not the room
  now. The estimate was never shown to be wrong, and 0.10.14 rests on [D106]
  alone.
  **The open question is plan-versus-execution:** when the plan says cool, the
  component cools for less than a quarter of the half hour **56%** of the time;
  the first action changes between consecutive plans 231 times in 1,847
  (dry↔rest 186 of those; median action lifetime 12 min). Candidates: the
  short-cycle guard's min run 10 / rest 5, the 5-minute replan overwriting the
  plan before it can run, and the limits turning a cool into a rest or an off.
- **0.10.15 is built, unreleased, and awaiting its day.** Two fixes are in
  `main` and in the changelog under `0.10.15 - unreleased`:
  - **#25** — the demand forecast is read once per five-minute boundary
    (`coordinator._demand_now`), not every cycle: 17,287 recorder rows a week
    become about 2,000.
  - **#27** — occupancy is a learned moisture source [D113], carried into the
    plan's six hours and the trust check's forward run, and held back until the
    room has been seen both occupied and empty (a room with no presence sensor
    counts as occupied for ever, and then `q` and `q_occ` cannot be told apart —
    a four-day replay dragged `h` from 0.50 to 0.365/h).
    **The first version of that guard deadlocked**: it suppressed the occupied
    flag until the room was identified, and identification needs an occupied
    minute, so nothing was ever priced. The observation now keeps the raw flag
    and the *prediction* leaves the term out; `HumidityFilter.priced_occ` is the
    single place that decides when people are priced.
    **The prior is a guess** (1.0 hPa/h). In the whole-loop sim it costs 24
    minutes below the band, all in the 08:00-09:00 hour of the first day, before
    the filter learns down (to 0.26 there, where nobody really adds vapour). The
    house should show what it settles at; do not move the starting value without
    that measurement.
  Release both together on the next release day, then close #25 and #27.
- **Review precedes every commit** (global instruction, 24 Nov): verify, then
  the full `review` skill — correctness, security, architecture, AI-tell, fresh
  eyes — then commit. The fresh-eyes pass is the one that pays: on #27 it found
  the identifiability failure, which no reading of my own diff would have
  shown. Hand it the artefact, never the conversation.
- **`ruff` is installable here** after all: `python3 -m venv /tmp/rv && /tmp/rv/bin/pip
  install "ruff==0.17.3"`, then `/tmp/rv/bin/ruff check custom_components tests`.
  Three CI failures on 24 Nov were lint, not behaviour (unused `noqa` RUF100, an
  inline lambda PLC3002, a quoted annotation UP037). `mypy --strict` still
  cannot run without the Home Assistant stack; CI is its only runner.
- **One release a day** (corrected by the user, 24 Nov — the earlier note said
  "one change a day" and was wrong). Fixes and commits can stack; **only one
  release per day**. 24 Nov's release was 0.10.14, so anything finished today
  ships in the next release, not another one today.
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
| 24 Nov 2026 | The present is measured; the future is modelled [D106] | The forecast must open from the reading, not the filter's estimate. **D106 already said so**; 0.10.14 made the code obey it. No wrong-estimate measurement is claimed — the earlier one was misaligned and is withdrawn |

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
- **Reading the trace:** `decision.index[k]` and `decision.actions[k]` are
  **30-minute steps** — `evidence.py:144` samples
  `points[PLAN_POINTS_PER_STEP - 1::PLAN_POINTS_PER_STEP]` with six five-minute
  points per step. So `index[0]` is the forecast **+30 min**, not the room now,
  and `actions[0]` is what the plan intends for the next half hour. `sigma`
  follows the same sampling. Comparing `index[0]` with the current reading
  measures the room's expected cooling, not any error — this mistake was made
  on 24 Nov and cost a false claim.
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
