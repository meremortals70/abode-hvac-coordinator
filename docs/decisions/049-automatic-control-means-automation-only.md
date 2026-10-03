# DR-049: Automatic control off stops the automation and leaves the unit alone

| | |
|---|---|
| Status | Accepted; supersedes DR-047; amends DR-007 |
| Since | 0.9.0 (2026-10-03) |
| Origin | Instruction, 2026-10-03: "Turning off automation is just that, turning the automation off." |
| Related | DR-007, DR-016, DR-021, DR-045, DR-047, DR-048 |

## Decision

A room's Automatic control switch decides whether the coordinator acts on the
room. On, the coordinator runs the room and the room's controls show the live
state of its units and refuse writes. Off, the coordinator sends nothing to the
room's units and the controls are writable, each command going to every head in
the room. The controls are built from the room's capability profile (DR-048):
mode, setpoint, fan speed, vertical vane, and horizontal vane where the profile
has one.

The vanes also have a switch of their own, Automatic vane control, per room, on
by default. On, the coordinator positions the vertical vane while Automatic
control is on, and the vane controls show the live position and refuse writes.
Off, the coordinator never sends a vane command to that room, and the vane
controls are writable at any time, including while Automatic control is on. The
coordinator never moves a horizontal vane at all. The state of both switches
survives a restart. Fan speed follows Automatic control only.

## Why

DR-047 made the switch command the unit off every cycle, so turning off
automation switched off the air conditioning, and left the user no way to run
the unit by hand while the automation was paused. The instruction is that the
switch means the automation is off, nothing more. Vane position is a matter of
whether people like air flowing over them and what stands in the way of the
vanes, and an autonomous controller cannot know either.

## Rejected

- **Off commands the unit off (DR-047).** It removes the user's ability to
  operate the unit while the automation is paused, which is the purpose of
  turning the automation off.
- **Remove vane automation, leaving vanes to the user.** The instruction was
  that automation remain available as a choice the user makes.
- **Vane automation as a setup-time option.** Airflow preference changes with who
  is in the room and what blocks the vanes. It has to be a switch on the
  dashboard, not a decision made once at setup.
- **Greyed controls through entity availability.** An unavailable entity shows
  "Unavailable", not the unit's state. The controls show the live state and
  refuse writes; the grey appearance is a dashboard conditional card keyed to the
  switches (see `docs/examples.md`).
- **Route the user's commands through the short-cycle guard (DR-016).** The guard
  protects the compressor from this coordinator, not from the person who owns it.

## Consequences

A room with Automatic control off keeps whatever state the unit is in, including
running. The Mode sensor and trace say the room is not under automatic control. A
change at the wall is shown, not re-asserted, while the switch is off; while it is
on, DR-007 still applies. The stored switch state is read before the first
refresh, as DR-047 required. DR-045's rule that a setting exists only where a
correct result is impossible without it is set aside for the vane switch by the
instruction above. The short-cycle guard is not told about a stop or a start made
by hand, so for up to ten minutes after turning Automatic control back on its
picture of the compressor can be wrong.

## In the code

Checked against: 0.9.0. **Conforms.**

- `switch.py:59` - `class RoomAutomaticControlSwitch` - the Automatic control switch
- `switch.py:92` - `class RoomAutomaticVaneControlSwitch` - the Automatic vane control switch
- `select.py:118` - `class RoomSelect` - mode, fan speed and vane controls
- `number.py:57` - `class RoomSetpoint` - the setpoint control
- `entity.py:107` - `def _require_writable` - the refusal, with a translated reason
- `coordinator.py:976` - `def controls_writable` - which controls the two switches open
- `coordinator.py:1025` - `inactive_reason` - a switched-off room is evaluated and sent nothing
- `coordinator.py:959` - `def is_room_vanes_manual` - read by the actuator before it moves a vane
- `actuator.py:622` - `def async_user_command` - a user's change goes to every head, checked against the profile
- `store.py:116` - `def manual_vanes` - the vane choice, read before the first refresh
