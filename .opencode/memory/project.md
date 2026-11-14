> Project memory for Abode HVAC Coordinator. Update at the end of each session.

## Current objective

Maintain and extend the Abode HVAC Coordinator Home Assistant custom integration. It learns a thermal model of each room and decides how to run that room's air conditioners.

## Active work

- Nightly Living Room performance reviews from the recorder export (see below).

## Night review workflow (Living Room)

Verified 12 Nov 2026 by running it for the night of 11–12 Nov.

`/config/export_window.sh` (archive copy: `tools\export_window.sh`) exports any
room over any window. Arguments: room substring, output tag, start UTC, end UTC,
and a label for the summary:

```sh
sh /config/export_window.sh living_room living_2026-11-13 '2026-11-12 11:00:00' '2026-11-12 22:00:00' '21:00 Thu -> 08:00 Fri'
```

It writes `abode_<tag>.csv`, `abode_<tag>_summary.txt` and `abode_<tag>.tar.gz`
and prints `OK: upload <path>`. `/config/export_night.sh` is the older
living-room-only script, still on the host.

**The script that runs lives on the HA host**, not on Windows; the copies in
this repo are archives only — `tools/` is git-ignored.

Run it in the HA SSH add-on (Advanced SSH & Web Terminal). It needs `sqlite3`
(`apk add sqlite` if missing) and reads `/homeassistant/home-assistant_v2.db`
(the script's `CFG` falls back to `/config` when `/homeassistant` is absent).

Windows are **in UTC** on the command line. Brisbane is +10 with no daylight
saving, so local 21:00 → 08:00 is UTC 11:00 → 22:00 of the same calendar day.

Success prints `OK: upload /homeassistant/abode_<tag>.tar.gz (132.0K)`. Upload
that tarball to `C:\Users\jason\Nextcloud\abode-hvac-coordinator\tools\` and
extract it.

### Gotchas

- **A pasted script arrives with CRLF and will not parse.** Run
  `sed -i 's/\r$//' /config/export_window.sh` before the first use, then
  `sh -n` it. A bare `{\r` is not the reserved word, so the group's closing `}`
  is reported as `syntax error: unexpected "}"` — 13 Nov 2026.
- The tarball lands in `/homeassistant`, not `/config`, whenever that directory
  exists. Take the path from the `OK:` line, not from the script's variables.
- The summary's `window local:` line is a hardcoded `echo` and keeps the old
  date. It is **not** evidence of the exported window — check the first
  `local_time` in the csv instead.
- `abode_night.csv` begins with a stray `10000` line (sqlite3 echoing
  `.timeout 10000`). Skip it before parsing: `next(f)` before `csv.DictReader`,
  or `Get-Content | Select-Object -Skip 1` in PowerShell. `Import-Csv` alone
  reads that line as the header and returns empty fields.
- `tools\ha_history_dump.py` exists but is not the nightly workflow.

### Review metrics

- **The band is in comfort index, not dry-bulb** [D009]: `sensor.living_room`
  attributes `band.low` / `band.high` are on the Steadman apparent-temperature
  scale (shade, wind zero) [D010]. On the night reviewed the sleep band was
  23.0–25.5 index, whose dry-bulb centre (`band.centre_c`) was 22.12 °C.
  Never compare `sensor.living_room_temperature` against these numbers.
- Time in band: time-weighted on `sensor.living_room_comfort_index` against
  `band.low` / `band.high`, holding the last value across gaps. Its own
  attributes carry `band_low` / `band_high` for cross-checking.
- Dry-bulb temperature (`sensor.living_room_temperature`) is reported as a range
  and mean only; it has no band.
- `band_rule` says why a band applies (`left`, `automatic_off`, `sleep`).
- Also count setpoint writes (`number.living_room_setpoint`), mode changes
  (`select.living_room_unit_mode`, `climate.living_room_aircon`) and fan
  changes (`select.living_room_fan_speed`).

## Recently completed

| Date | What |
| ---- | ---- |
| 12 Nov 2026 | 0.10.7 released (tag `v0.10.7`, [release](https://github.com/meremortals70/abode-hvac-coordinator/releases/tag/v0.10.7), issue #5 closed): an opening is a model input (`vent`); free cooling is the plan's own counterfactual [D105] |
| 12 Nov 2026 | FP2 presence reviewed; the room is to be configured with it and a 120-minute empty grace, with the zone/sensitivity fix still to do |
| 12 Nov 2026 | Night review of 11–12 Nov; workflow recorded above |
| Earlier | Shipped 0.10.4 |

## Decisions

| Date | Decision | Why |
| ---- | -------- | --- |
| Design | Comfort index (HCI) is Steadman apparent temperature with wind zero | Occupants feel humidity, not just dry-bulb temperature |
| Design | Two-store thermal model (air + mass) | Captures building inertia and solar gain |
| Design | No runtime dependencies | `requirements` is empty; stdlib only |
| 12 Nov 2026 | An opening is an input to the heat balance with a learned coupling (`vent`); no hold and no at-once stop; free cooling is the plan's own counterfactual [D105] | A sealed double-brick room cannot use outdoor air, so the model must say so rather than a rule |

## Model facts (do not re-litigate)

- `k` **is** in the heat model and **is** learned: `physics.py:4`
  (`dM/dt = c (T - M) + k (T_out - M) + m S`), state index `I_LN_K = 5`
  (`physics.py:33`), started at 0.1/h and estimated by the filter
  (`filter.py:171`, `_LOG_FIGURES` at `filter.py:205`).
- `h` is the moisture exchange with outdoor air, also learned
  (`humidity.py:158`, `docs/architecture.md:164`).
- `vent` (`I_LN_VENT = 27`, `STATE_SIZE = 28`) is the air an opening changes
  with outdoors, per hour: `dT/dt = (a + vent·o)(T_out − T) + …`, `o` = 1
  while an opening reads open [D105]. It is learned only from minutes an
  opening was open; a 27-state save is migrated on load.
- Free cooling is **not** a rule: the plan carries the room forward with an
  opening open and the unit resting, beside the plan, and advises when the
  opening moves the index by more than the forecast's own sigma and costs
  less over the same window (`plan._opening_advice`). The dew point margin
  (`psychro.outdoor_air_dry`) is the one measured constraint left, because
  there is no learned moisture coupling for an opening.
- Announcements are blocked while the room is asleep, and "asleep" is the
  Comfort mode or the sleep schedule (`comfort.py:115-122`), not a clock.
  With the window shut, a warning can still be spoken by day [D071].

## Living Room presence (Aqara FP2, 12 Nov 2026)

Reviewed from `tools/presence_2026-11-12/` (48 h of `binary_sensor.presence_living_room_zone1`).

- Two nights: 11–12 Nov **0** state changes (on throughout 21:13:28–07:05:43);
  10–11 Nov two short `off` blips (6 min 48 s, 2 min 32 s).
- Daytime, with her in the room: one **88.5-minute** `off` (09:37:51–11:06:24)
  and 22.9 minutes at 17:29. The office sensor had someone in the office for
  most of the first, so it may have been a true absence; unresolved.
- 126 real state changes in 48 h, median stretch 1.4 min, 53 under 60 s, and
  three `unavailable` spells (35 s, 17 min, 1 min) — the integration does mark
  dropouts, so a silent night is a live `on`.
- The FP2 is in HA **twice**: `binary_sensor.presence_sensor_fp2_6a04_presence_sensor_2`
  (native, leads) and `presence_living_room_zone1` (HomeKit bridge, 1 s behind).
  Same signal. Use the native one.
- A false "empty" is not a band nudge: `comfort.py:141-144` returns `off` with
  no band. So the room is configured with **empty grace 120 min** until the
  sensor is proven.
- Outstanding: raise presence sensitivity for the bed zone and redraw it to the
  mattress edges in the Aqara app; the FP2 exposes no absence delay to HA.

## Blockers

- None currently.

## Known issues

See `docs/known-limitations.md` and GitHub issues at `https://github.com/meremortals70/abode-hvac-coordinator/issues`.

## Glossary

| Term | Meaning |
| ---- | ------- |
| HCI | Human Comfort Index, derived from Steadman apparent temperature |
| TiTe model | Two-store thermal model: room air + building mass |
| Plan | 5-minute lookahead decision |
| PID | 30-second setpoint adjustment |
| Pure layer | Decision modules with no Home Assistant imports |
| D-record | Numbered design rule in `docs/decisions/D###.md` |
