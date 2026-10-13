# DR-067: The power budget measures the rest of the house, averages its readings, and waits before turning a room off

| | |
|---|---|
| Status | Accepted; amends DR-037, DR-039, DR-062 and DR-063 |
| Since | 0.9.4 (2026-10-12) |
| Origin | Incident, Office, 12 October 2026, 16:30–18:00. Instruction, 2026-10-12: "you fucking better fix everything" |
| Related | DR-016, DR-037, DR-039, DR-044, DR-061, DR-062, DR-063 |

## Decision

The budget takes the rest of the house from the house load measured while the
room's own unit was not running, averaged over the last fifteen minutes, so the
room's own draw is never counted against it. House load and solar are both
averaged over fifteen minutes. An enforced room that was running is turned off
only once the budget has fallen short for ten minutes, held at the gentlest
setting until then. Without a solar forecast, the battery's energy over the
window is reduced by what the rest of the house will draw, as the forecast walk
already did.

## Why

The Office, set to Enforced, was turned off the minute after each start, at
17:06, 17:18 and 17:34, and the room rose from 25.4 C to 26.9 C. With the
unit off, the allowance was about 0.5 kW. A minute after it started, the house
load rose to about 1.45 kW, and the budget took off only the room's estimated
draw, so the rest of what the unit drew was counted as the rest of the house
for the six hours left. The allowance fell below 0.1 kW, and one reading turned
the room off, which DR-062 then took ten minutes to undo.

The no-forecast budget had the opposite fault: it spread the battery's energy
over the hours left without taking off the house's own use, so a room was
allowed the house's share as well.

## Rejected

- **A better estimate of the room's draw.** An inverter's draw changes minute
  to minute with what it is doing; any estimate is wrong while it ramps.
- **A per-room power sensor.** Most units do not have one, and a setting that
  most rooms cannot fill breaks DR-045.
- **The house load at the moment.** One reading is a kettle; the budget
  projects it for hours.
- **Turning off at once and recovering faster.** Restarting a unit is held by
  the short-cycle guard (DR-016); a stop costs at least five minutes.

## Consequences

While a room runs, its measure of the rest of the house is what it was before
the room started. A load that comes on after that is not seen until the room
stops, though the battery reading still falls with it and the budget follows.
A room that has run since Home Assistant started has no such measure yet and
uses the house load less its estimated draw, as before. The two fifteen- and
ten-minute figures are judgement constants, listed in known limitations.

## In the code

Checked against: 0.9.6. **Conforms.**

- `power.py:490` - `READING_AVERAGE_WINDOW = timedelta(minutes=15)` - the averaging window
- `power.py:495` - `OFF_HOLD = timedelta(minutes=10)` - how long a shortfall lasts before off
- `power.py:498` - `def mean_recent` - the average, measured back from the newest reading
- `power.py:514` - `def hold_before_off` - held at the gentlest bin, then off
- `power.py:127` - `max(available_kwh, 0.0) / hours_until_clear - max(other_house_draw_kw, 0.0)` - the house's share taken off
- `coordinator.py:3399` - `def _note_power_samples` - readings kept, and each room's own while it is off
- `coordinator.py:3431` - `def _rest_of_house_kw` - the rest of the house as measured
- `coordinator.py:3614` - `bin_index, since = hold_before_off(` - applied to enforced rooms
