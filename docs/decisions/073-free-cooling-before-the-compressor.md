# DR-073: When outdoor air is better, an occupied room is not started, and an opening made for it stops the unit at once

| | |
|---|---|
| Status | Accepted; amends DR-050 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Office, 14 October 2026, 08:22–08:33. Instruction, 2026-10-14: "i want you to roll everything into 0.9.6" |
| Related | DR-008, DR-050, DR-071 |

## Decision

When outdoor air is judged better than the room's (free cooling advised), an occupied room's compressor is not started for ten minutes, so the windows can be opened; after that it starts. A unit already running is left running. An opening made while the advice stands stops the unit without the opening grace and without warnings.

## Why

At 08:23:15 the Office's door closed, the compressor was started, and in the same second the room was told that opening up would help. Jason opened the door and window and was then warned twice that the air conditioning would be turned off because something was open.

## Rejected

- **Never start while the advice stands.** Comfort is the constraint (DR-008); nobody may open the windows.
- **Hold sleeping rooms too.** Nothing is said in a sleeping room (DR-071), so nobody would know to open anything.
- **Keep the opening grace for an advised opening.** The grace exists for a door held open by accident; this one was opened on purpose.

## Consequences

An occupied room that could be cooled by opening up waits up to ten minutes before its compressor starts. `FREE_COOLING_WAIT` is a judgement.

## In the code

Checked against: 0.9.6. **Conforms.**

- `modes.py:326` - `FREE_COOLING_WAIT = timedelta` - the wait
- `modes.py:964` - `and not inputs.free_cooling_wait_over` - no start while it runs
- `modes.py:531` - `if inputs.free_cooling_advised:` - an advised opening stops the unit at once
- `coordinator.py:3843` - `announce=room.grace.announce and not inputs.free_cooling_advised,` - and is not warned about
- `coordinator.py:1985` - `self._free_cooling_since.setdefault` - when the advice began
