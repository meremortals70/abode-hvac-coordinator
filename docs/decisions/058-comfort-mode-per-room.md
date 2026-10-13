# DR-058: Each room has a Comfort mode - Schedule, Sleep or Awake

| | |
|---|---|
| Status | Accepted; amends DR-014 |
| Since | 0.9.2 (2026-10-11) |
| Origin | Instruction, 2026-10-11: "while some people may go to sleep and wake up based on a schedule, we do not. I need to have switches available in home assistant to set sleep mode"; "per room, and I don't want to lose the ability to schedule it"; "when presence clears it should turn off and return to normal mode" |
| Related | DR-007, DR-009, DR-012, DR-014, DR-045, DR-049 |

## Decision

Every room has a Comfort mode select with three options. Schedule is the
default and leaves sleep to the room's sleep schedule, exactly as before.
Sleep puts the room on its sleep band whatever the schedule says. Awake keeps
it on its occupied band whatever the schedule says.

Sleep and Awake go back to Schedule when the room is empty: graced presence
off and the presence sensor itself off. Awake also goes back when the sleep
schedule turns from on to off. Choosing Sleep or Awake in a room that reads
empty is refused with a reason. The choice is kept across restarts.

The Comfort mode only decides whether the room counts as sleeping. It never
turns the automation off - that is the Automatic control switch (DR-049) -
and it is writable whether Automatic control is on or off. Lockout, heading
home, precool, coast and openings outrank it exactly as they outrank the
schedule.

## Why

Sleep could only be entered through a schedule entity (DR-014). The
household does not go to bed on a schedule, so the sleep band was either
reached at the wrong time or never.

Reading the code before building found that "is the room sleeping" was
asked in three places, not one: the mode decision, the unaided-return
projection behind coasting (DR-053), and the cheaper-tariff wait (DR-035).
The last two read the schedule entity directly. A switch wired into the mode
decision alone would have had a room on the sleep band projected against the
day band. There is now one answer, `modes.sleeping`, and all three ask it.

## Rejected

- **One switch per mode.** Sleep and Awake on together has no meaning, and
  the coordinator would have to settle a conflict nobody intended.
- **Point the sleep schedule field at an input_boolean.** Already accepted by
  the field, but it replaces the schedule rather than sitting beside it,
  never clears itself, and lives outside the room.
- **End the choice on graced presence alone.** Graced presence also reads
  off for the first minutes after someone walks in and after every restart,
  while the start grace runs. A room put on Sleep would have dropped back to
  Schedule on the first evaluation after Home Assistant restarted, with the
  person still in bed.
- **End Awake whenever the schedule is off.** Awake chosen during the day
  would undo itself on the next evaluation. It ends on the schedule's change
  from on to off.
- **Accept Sleep in an empty room and hold it.** It would end on the next
  evaluation, or apply hours later to whoever walked in.
- **Put heading home on the same select.** Heading home carries an optional
  deadline, is set and cleared by its own services, and does not end on
  arrival. A select cannot hold a deadline.
- **Gate the select on Automatic control, like the unit controls.** It
  commands nothing; refusing it while the automation runs would refuse it
  exactly when it matters.

## Consequences

A room with no presence sensor keeps Sleep or Awake until it is changed by
hand, and the trace says the choice is in force. A room whose presence
sensor reads off at the moment Home Assistant starts ends a kept choice at
once, without waiting for the stop grace, because there is no graced history
to hold it. The one-hour band ramp (DR-014) applies to a change made from the
select, because it follows any change of mode. DR-045's rule that a setting
exists only where a correct result is impossible without it is set aside for
this control by the instruction above, as it was for DR-049 and DR-050.

## In the code

Checked against: 0.9.4. **Conforms.**

- `models.py:32` - `class ComfortMode` - Schedule, Sleep, Awake
- `modes.py:56` - `def sleeping` - the one answer to "is the room sleeping"
- `modes.py:71` - `def room_reads_empty` - graced and raw presence both off
- `modes.py:84` - `def comfort_mode_after` - empty ends both; the schedule turning off ends Awake
- `modes.py:120` - `def comfort_mode_refused` - Sleep or Awake refused in an empty room
- `modes.py:138` - `asleep = sleeping(inputs.comfort_mode, inputs.sleep_schedule_active)` - the mode decision
- `coordinator.py:2137` - `def _sleeping` - the coordinator's band lookups
- `coordinator.py:2145` - `return sleeping(` - the same answer as the mode decision
- `coordinator.py:1834` - `band = room.band_for(Mode.SLEEP if self._sleeping` - the cheaper-tariff wait
- `coordinator.py:2639` - `band = room.band_for(Mode.SLEEP if self._sleeping` - the unaided-return projection
- `coordinator.py:2516` - `self._update_comfort_mode(room, presence)` - settled before anything asks
- `coordinator.py:1236` - `def _update_comfort_mode` - ends the choice and says why
- `coordinator.py:570` - `_comfort_modes_from_store(` - read from the store before the first evaluation
- `store.py:205` - `def set_comfort_mode` - kept across restarts
- `select.py:134` - `class RoomComfortModeSelect` - always available, always writable
