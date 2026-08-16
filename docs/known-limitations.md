# Known limitations

Written plainly, because a limitation you discover yourself is worse than one
you were told about.

## Nothing has been run

**v0.4.0 has never been installed in Home Assistant, never loaded, and never
actuated anything.** The decision logic is unit tested — 128 tests over the pure
modules — but the Home Assistant surface has not been exercised. Treat the first
install as a test.

## Actuation is wired but unproven

The controller now carries out its decisions: it sets the climate entity's HVAC
mode, temperature and fan mode, and moves covers.

**It has never done this against a real unit.** Every service call was written
against the service definitions read from source — Home Assistant's own climate
and cover components — but reading a schema is not the same as watching a
compressor start.

Two things reduce the blast radius, and neither removes it:

- Every call is capability-checked first. An HVAC mode the unit does not
  advertise is a rejection in the trace, not a failed service call.
- Unchanged decisions are not re-sent, so a stable room is not commanded every
  30 seconds.

Watch the first day. A room in lockout should command nothing at all, which is
the cheapest way to confirm the gate works before trusting the rest.

## Two modes cannot be entered

| Mode | Waiting on |
|---|---|
| `COAST` | The thermal model. `predicted_to_hold` is always unknown |
| `PRECOOL` | The demand forecast. `forecast_demand_ahead` is always false |

## There is no demand forecast

The vendor-neutral projected-energy sensor described in the architecture is not
built, so nothing downstream can read what this controller expects to draw.

Both are implemented and tested. They are waiting on inputs.

## The deadline on heading home is recorded, not acted on

The model can now answer how long a room takes to reach comfort, but
preconditioning still starts immediately rather than working backwards from the
deadline. The arithmetic exists; the scheduling around it does not.

## Two thresholds are placeholders

The dry-mode humidity threshold and the solar gain lux threshold are stand-ins
for decisions the thermal model should make.

The lux threshold is the weaker of the two, and it **will be wrong in rooms
whose illuminance sensor is not near the window** — what a sensor reads depends
entirely on where it sits. Detail in [Actuator ordering](actuator-ordering.md).

## Wind penetration is a stated assumption, not a measurement

The free-cooling test damps outdoor wind to 30% before applying it, on the
grounds that a ten-metre reading in the clear is not what reaches a person in a
room. That fraction depends on which windows you open, whether they are on
opposite walls, and where the furniture is. It is one number for every room.

Confidence that 30% is right for any particular room: **50%**. It is right in
direction — using the raw figure would be badly wrong — and approximate in
magnitude.

## Nothing has been tuned against a real house

The architecture is complete. Four constants are not: the Layer 2 integral
gain, the wind penetration fraction, the dry-mode advantage ratio and the
precool demand margin. Each has a stated basis and a documented direction, and
each will be wrong by some amount until the thing has run through a summer.

That is tuning, not design. It is listed here so it is not mistaken for either.

## Wind is not in the thermal model

`k_loss` is learned as a single coefficient, but a windy day genuinely leaks
faster than a still one. The wind feed is used for the free-cooling advice and
nothing else. Adding wind to the model is a real improvement and has not been
made.

## Free cooling is advice, not action

The controller works out when outdoor air would genuinely help — cooler *and*
drier at the dew point — and publishes it. It does not open anything. Windows
are not an actuator this project owns, and it will not command one it cannot
also close when the weather turns.

## The outer loop has no per-room tuning

Layer 2 uses one integral gain for every room. A room with a very fast response
and a room with a very slow one converge at the same rate, which means the slow
one takes longer than it strictly needs to. Deliberate: a per-room gain is a
setting nobody can answer correctly, and the cost of the shared value is time
rather than accuracy.

## The tariff is a separate integration

Precool windows, the no-import rule and the costed forecast all require
[Abode Power Tariffs](https://github.com/meremortals70/abode-power-tariffs) to
be installed and configured. Without it the controller runs on comfort alone,
which is supported but is less than it can do.

This is not a soft dependency that degrades quietly: the house configuration
screen states what is lost, and a fetch that fails raises a repair issue naming
the reason.

## Sun detection is one compass direction per room

The controller works out sun-on-glass from the sun's position and the direction
the room's windows face. That is right for a room with windows on one wall and
wrong for a corner room, a room with a verandah, or one shaded by a tree at
certain hours.

Where that matters, point the room's sun-on-window setting at your own binary
sensor instead — it overrides the calculation entirely.

A room with no direction and no sensor never uses its blinds, because the
controller will not move them on a guess.

## Announcements are text-to-speech only

The announcement before shutting a room down calls `tts.speak` at the media
players you choose. There is no notification option, no choice of voice, and no
way to change the wording without editing the source.

## The comfort index is one opinion

Steadman apparent temperature with wind zero is defensible and behaves correctly
with humidity, but comfort depends on clothing, metabolic rate, air movement and
radiant temperature, none of which are measured. The band table is derived from
ASHRAE 55's assumptions about a seated person, and those assumptions may not be
yours.

Confidence that the band table suits any particular household: **70%**. Adjust
against how the room actually feels rather than trusting the numbers.

## It never writes to your battery

Deliberate, and not changing. See [Architecture](architecture.md).

## Multi-head units

No arbitration between heads sharing a compressor. Each room is evaluated
independently. In a climate where rooms on a shared compressor will not want
opposing modes this is harmless; elsewhere it is a real gap.

## Single instance

One config entry for the whole house. Rooms live inside it.

## Not in Home Assistant core

A custom integration cannot be awarded a quality scale tier — Custom is a
special tier alongside Internal and Legacy. The project tracks compliance
against all 54 rules in `quality_scale.yaml` so that submission remains
possible, but it is not graded today.
