# DR-047: A room's Automatic control switch holds its unit off

| | |
|---|---|
| Status | Superseded by DR-049 |
| Since | 0.8.14 (2026-10-01) |
| Origin | Instruction, 2026-10-01: "I also need an off switch for a room." |
| Related | DR-007, DR-016, DR-021, DR-049 |

## Decision

Every room has a switch, Automatic control, on by default. Turned off, the
room is evaluated as a lockout with the reason "Automatic control switched
off": its unit is commanded off every cycle and nothing in the room's decision
can start it, until the switch is turned back on. The choice is stored, and
survives a restart.

## Why

The only route to an off room was setting a lockout reason in the options
flow (DR-021), which reloads the whole entry and is not something to do from a
dashboard at the moment a room needs to be off.

## Rejected

- **Coordinator stops touching the unit.** A room left alone is left running
  if it was running, which is not what an off switch means.
- **Treat a change at the wall as intent** (DR-007). Unchanged: the wall is
  still re-asserted. The switch is the coordinator's own control, so DR-007's
  premise of one control surface holds.
- **Restore the state from the entity.** The coordinator's first refresh runs
  before any entity exists, and that refresh could start the unit. The choice
  is read from the store, which is loaded before the refresh.
- **Apply the short-cycle guard to it** (DR-016). The guard protects the
  compressor from this coordinator, not from the person who owns it. A stop
  that waits up to ten minutes is not an off switch. The stop is still
  recorded, so the guard's picture of the compressor stays true.

## Consequences

The Mode sensor reads Lockout and the trace says why. Turning the room back on
does not start it at once if the unit was stopped less than five minutes ago:
the minimum off time (DR-016) still applies to the start. The lockout dropdown
(DR-021) remains for a room that should stay out of control across
reconfiguration; this switch is for the day-to-day case.

## In the code

Checked against: 0.9.0. **Superseded by DR-049.** The switch, its store entry and its survival of a restart remain. What it did when off does not: the unit is no longer commanded off every cycle, and the guard bypass for a forced stop is gone.

- `switch.py:50` - `RoomAutomaticControlSwitch` - the entity
- `store.py:95` - `switched_off` - read before the first refresh
- `store.py:106` - `set_switched_off` - written within seconds
- `coordinator.py:497` - `_switched_off` - loaded from the store
- `coordinator.py:950` - `async_set_room_switched_off` - flips the choice and acts now
- `coordinator.py:1026` - `SWITCHED_OFF_REASON` - the reason shown while the switch is off; the room is evaluated and sent nothing
