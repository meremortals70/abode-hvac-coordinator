# DR-050: An open window or door has a per-room grace and spoken warnings before the unit stops

| | |
|---|---|
| Status | Accepted; supersedes DR-019 |
| Since | 0.9.0 (2026-10-03) |
| Origin | Instruction, 2026-10-03: the aircon turns off when a door is opened, with no grace period or announcement; "the grace should be five minutes" |
| Related | DR-016, DR-017, DR-019, DR-045 |

## Decision

While an opening in a room is open, nothing new is actuated. The grace before the
unit is stopped is a per-room setting, defaulting to five minutes. Where the room
has announcements on, a first warning is spoken one warning grace before the stop
(three minutes, the room's existing setting, so at two minutes with the default
grace, or at once where the grace is shorter than that) and a final warning
immediately before the stop, through the room's existing announcement targets. An
opening whose open time cannot be read holds the unit; it does not stop it.

## Why

In 0.8.14 the grace was a fixed two minutes and there was no announcement for
openings: the announcements that existed were the vacancy warnings and free
cooling. Where the age of an open opening was unknown, the coordinator commanded
the unit off at once. A door opened by the user stopped the unit with no notice.

## Rejected

- **Keep the fixed two minutes (DR-019).** The user's experience was that it is
  too abrupt and silent, and a fixed value gives no way to change it.
- **Stop immediately.** Cycles the compressor for every door.
- **Treat an unreadable age as old and stop.** This was the 0.8.14 behaviour. A
  sensor that cannot be read is not evidence the door has been open long.

## Consequences

DR-045 is set aside for this setting by the instruction above. A room with
announcements off still gets the grace and the hold, silently. The unit stops when
the grace expires, with the warnings falling inside it. The short-cycle guard
still applies to the stop: a unit started less than ten minutes earlier is stopped
when the minimum run has elapsed, not before. In the coordinator an open opening
always carries the time it last changed, so the unknown-age hold is a guard in the
decision and not a state the running component reaches.

## In the code

Checked against: 0.9.1. **Conforms.**

- `modes.py:182` - `DEFAULT_OPENING_GRACE_MINUTES` - five minutes
- `modes.py:369` - `if inputs.opening_open:` - hold, then off
- `grace.py:237` - `def evaluate_opening_warnings` - the first and final warnings, once each
- `coordinator.py:2936` - `def _warn_about_opening` - queues the warnings for the room's announce targets
- `forms.py:127` - `def opening_grace_from_input` - the room form's grace, five where blank
- `config_flow.py:53` - `CONF_OPENING_GRACE` - the field on the room form
