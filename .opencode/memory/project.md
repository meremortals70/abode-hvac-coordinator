> Project memory for Abode HVAC Coordinator. Update at the end of each session.

## Current objective

Maintain and extend the Abode HVAC Coordinator Home Assistant custom integration. It learns a thermal model of each room and decides how to run that room's air conditioners.

## Active work

- Nightly Living Room performance reviews from the recorder export (see below).

## Night review workflow (Living Room)

Verified 12 Nov 2026 by running it for the night of 11–12 Nov.

**The script that runs lives on the HA host**, not on Windows:
`/config/export_night.sh`. The Windows copy in this repo
(`tools\export_night.sh`) is an archived copy only — `tools/` is git-ignored.

Run it in the HA SSH add-on (Advanced SSH & Web Terminal). It needs `sqlite3`
(`apk add sqlite` if missing) and reads `/homeassistant/home-assistant_v2.db`
(the script's `CFG` falls back to `/config` when `/homeassistant` is absent).

The window is **hardcoded in UTC**. Brisbane is +10 with no daylight saving, so
local 21:00 → 08:00 is UTC 11:00 → 22:00 of the same calendar day:

```sh
sed -i "s/^START=.*/START='2026-11-11 11:00:00'   # 21:00 Wed 11 Nov local (UTC)/; s/^END=.*/END='2026-11-11 22:00:00'     # 08:00 Thu 12 Nov local (UTC)/" /config/export_night.sh
sh /config/export_night.sh
```

Success prints `OK: upload /homeassistant/abode_night.tar.gz (132.0K)`. Upload
that tarball to `C:\Users\jason\Nextcloud\abode-hvac-coordinator\tools\` and
extract it; it contains `abode_night.csv` and `abode_night_summary.txt`.

### Gotchas (all hit on 12 Nov 2026)

- The tarball lands in `/homeassistant`, not `/config`, whenever that directory
  exists. Take the path from the `OK:` line, not from the script's variables.
- The summary's `window local:` line is a hardcoded `echo` and keeps the old
  date. It is **not** evidence of the exported window — check the first
  `local_time` in `abode_night.csv` instead.
- `abode_night.csv` begins with a stray `10000` line (sqlite3 echoing
  `.timeout 10000`). Skip it before parsing: `next(f)` before `csv.DictReader`,
  or `Get-Content | Select-Object -Skip 1` in PowerShell. `Import-Csv` alone
  reads that line as the header and returns empty fields.
- Update `START`/`END` in the local `tools\export_night.sh` too, so the archive
  copy stays in step with what runs on HA.
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
| 12 Nov 2026 | 0.10.7: an opening is a model input (`vent`); free cooling is the plan's own counterfactual [D105] |
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
