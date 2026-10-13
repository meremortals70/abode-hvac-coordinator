# DR-070: A room's temperature and humidity are trusted until they are unavailable

| | |
|---|---|
| Status | Accepted; amends DR-030 and DR-066 |
| Since | 0.9.6 (2026-10-13) |
| Origin | Incident, living room, 13 October 2026: the room on Sleep with Automatic control on and nothing sent for more than two hours. Instruction, 2026-10-13: "Why don't you trust it unless it is unavailable" |
| Related | DR-018, DR-030, DR-046, DR-066 |

## Decision

A room's own temperature and humidity readings have no age limit. They are used
for as long as Home Assistant shows them; only `unavailable` or `unknown` makes
them absent. Every other feed keeps the limit DR-030 gave it.

## Why

The living room's Eve Room reports only when the reading changes. On 13 October
it reported at 17:42 and next at 21:05, because the room did not change. Earlier
the same day it went 2 h 27 min and 2 h 24 min between reports. After two hours
the reading was treated as absent, the room had no comfort index, and nothing
was sent to its air conditioner, which stayed off while the room was set to
Sleep. A room sensor that really drops out is shown as unavailable by its own
integration, as the Eve Room was at 11:07 and 17:18.

## Rejected

- **A longer limit, six hours.** It moves the failure, it does not remove it: a
  steady room at night can be quiet for longer, and the room then stops for the
  same reason.
- **A limit per sensor, as a setting.** DR-030's reason holds: nobody can know
  the right figure for a sensor in advance.
- **Keep the two-hour limit and fall back to the unit's own reading.** A
  separate question; the unit's sensor is not the room.

## Consequences

A room sensor that stops reporting while Home Assistant still shows its last
value as available is trusted. That is listed in known limitations. Outdoor,
presence, contact and power feeds still carry a limit, and every read of a
house feed now names its own limit (a static test checks it), because the
default is now none.

## In the code

Checked against: 0.9.6. **Conforms.**

- `staleness.py:57` - `INDOOR_TOLERANCE: timedelta | None = None` - no age limit for room readings
- `staleness.py:111` - `if tolerance is None or age <= tolerance:` - no limit means fresh
