# DR-021: Lockout takes a room out of control, set by the user or forced by a conflict

| | |
|---|---|
| Status | Accepted |
| Since | User lockout from 0.6.0 (2026-08-16); conflict lockout in 0.8.6 (2026-08-21) |
| Origin | First release; finding 8 |
| Related | DR-017, DR-020 |

## Decision

A locked-out room is commanded off and takes no other action. A user locks a
room out by picking a reason from one dropdown on the room form (renovation,
disconnected, seasonal shutdown, and so on). Two rooms configured against one
climate entity are both locked out automatically, with a repair issue naming
the entity and the rooms.

## Why

A room sometimes has to stay in the configuration but out of use. One
dropdown answers both "is it locked out" and "why", with no toggle and no
second screen.

Two rooms on one climate entity each wrote their own setpoint every cycle,
silently (finding 8). The form now refuses it; older configurations are
locked out on load rather than failing the whole entry, so one bad pair does
not take the correctly configured rooms down with it.

## Rejected

- **A `ConfigEntryError` for the conflict.** Takes every room down for one bad
  pair.

## Consequences

Lockout is a configuration change, made in the options flow. There is no
switch entity to lock a room out from a dashboard.

## In the code

Checked against: 0.9.0. **Conforms.**

- `config_flow.py:278` - `CONF_LOCKOUT_REASON` - the dropdown
- `forms.py:174` - `_lockout_reason` - first option means not locked out
- `const.py:205` - `DEFAULT_LOCKOUT_REASONS` - offered reasons
- `coordinator.py:3024` - `_lock_out_shared_climate` - conflict lockout
- `modes.py:359-361` - `Mode.LOCKOUT` - off
