# DR-022: Cheapest first: covers, fan, dry, compressor, every skip traced

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4; in the code from 0.6.0 (2026-08-16); unknown cover position skipped in 0.8.6 |
| Origin | Design v0.4; finding 1 |
| Related | DR-006, DR-023, DR-024, DR-029 |

## Decision

Outside its band, a room works through covers, then fan, then dry mode, then
the compressor. Direction is worked out first: heating skips fan and dry,
because neither adds heat. Each step that is skipped writes its reason into
the trace. The fan is tried only when the room is at most 0.5 index above its
band.

## Why

A blind closed against the sun, a fan or dry mode each cost far less than the
compressor, and in a humid climate the problem is often humidity rather than
heat. Without per-step reasons, "the cheap options were exhausted" cannot be
checked.

Covers with no reported position used to be treated as able to help, so a
room whose blinds never reported position chose covers every sunny cycle with
the unit off throughout (finding 1, 0.8.6). An unknown position now skips the
step with its own reason.

## Rejected

- **Compressor first, others as extras.** Spends the most expensive actuator
  on problems a cheaper one solves.

## Consequences

A room can take several cycles to reach the compressor. Covers that are
already where they need to be are skipped, or the ordering would pick covers
forever.

## In the code

Checked against: 0.9.1. **Conforms.**

- `modes.py:346` - `select_actuator` - the ordering
- `modes.py:467-474` - `cover_position is None` - unknown position skips covers
- `modes.py:491-499` - `demand == "heat"` - heating skips fan and dry
- `modes.py:158` - `FAN_MARGIN_HCI` - 0.5
