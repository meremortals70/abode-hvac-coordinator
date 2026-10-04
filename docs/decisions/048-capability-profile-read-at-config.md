# DR-048: What a room's units can do is read when the room is configured

| | |
|---|---|
| Status | Accepted; amends DR-024 (the list a command is checked against is the one read at setup) |
| Since | 0.9.0 (2026-10-03) |
| Origin | Instruction, 2026-10-03: "The component needs to know how it can control the aircon at setup" |
| Related | DR-020, DR-021, DR-024, DR-049, DR-051 |

## Decision

When a room is added, or edited, the setup flow reads each head's live state and
stores a capability profile with the room: HVAC modes, fan modes, vertical and
horizontal swing modes, temperature range and step, and whether the unit takes a
single target or a range. A room with two heads stores the intersection. Setup
refuses a room if any head is unavailable or is not reporting. Every command and
every control the integration offers for the room is built from the stored
profile.

## Why

In 0.8.14 nothing was read at setup. The room form took climate entity ids and
nothing else. Capabilities were found at command time from whatever the entity
reported at that moment, matched against fixed lists of names. A unit whose fan
or swing names were not on those lists was silently sent nothing, and there was
no record of what the unit offered to build a control from.

## Rejected

- **Keep resolving from live state at command time.** This was the 0.8.14
  behaviour and the defect: with no record of what the unit can do, no control
  can be built from one, and a unit that briefly stops advertising a mode
  changes the decision.
- **Save the room with the profile marked "not read" when a head is
  unavailable.** A room whose capabilities are unknown cannot have controls
  built, and the flow cannot show what it read. Refusing is the only outcome that
  leaves a room with a complete profile.
- **Ask the user to choose fan and vane values during setup.** The instruction
  was that everything is read at config, not entered.
- **Hardcode names per make of unit.** The profile is whatever the entity
  advertises; nothing in the integration is written for one manufacturer.
- **A config-entry version bump and migration.** The profile is an optional
  field on the room. A room configured before 0.9.0 has none, is read on first
  load, and the result is kept in the store, so there is no stored shape to
  migrate and no reload that can strand an existing install.

## Consequences

Controls, and the actuator's mode, fan and vane commands, can only offer values
the unit advertised at setup. A unit whose capabilities change (a new dongle,
firmware) is re-read by editing the room. A room with no profile yet, which is
every pre-0.9.0 room until its unit reports, is still evaluated and its decision
published, but nothing is sent to it. The profile is shown on the room's
Settings sensor and in diagnostics. The profile is only as accurate as the
integration that publishes the entity: the Intesis integration's setpoint step
is a hardcoded 1.0, not read from the unit.

## In the code

Checked against: 0.9.1. **Conforms.**

- `capabilities.py:36` - `class RoomCapabilities` - the stored profile
- `capabilities.py:122` - `def intersect` - what two heads can both do
- `actuator.py:131` - `def read_capabilities` - one entity's profile, None if it reports nothing
- `actuator.py:176` - `def profile_from_states` - a whole room's profile, None if any head is missing
- `config_flow.py:401` - `climate_not_reporting` - setup refuses a room whose unit is not reporting
- `coordinator.py:2207` - `def _async_ensure_profiles` - first-load read for a room with no profile, kept in the store
- `store.py:146` - `def set_profile` - the kept profile
- `actuator.py:214` - `def resolve_hvac_mode` - modes resolved against the profile
- `coordinator.py:1025` - `inactive_reason` - a room with no profile is evaluated and sent nothing
