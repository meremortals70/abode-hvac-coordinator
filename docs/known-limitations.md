# Known limitations

Written plainly, because a limitation you discover yourself is worse than one
you were told about.

## Nothing has been proven on real hardware

**No release from 0.6.0 onward has been confirmed working in Jason's house.**
284 tests pass, `mypy --strict` is clean across all 23 modules with Home
Assistant installed, and the integration loads and unloads cleanly in the test
harness. None of that tells you a compressor did the right thing.

0.6.0 crashed on setup. 0.8.0 crashed the evaluation loop the first time a
weather forecast was configured. Both are fixed and both had tests written
against them afterwards, which is the wrong order.

Treat the first week as a test and read this page before relying on it.

## Actuation is unproven against a real unit

Every service call was written against the service definitions read from
source. Reading a schema is not the same as watching a compressor start.

Two things reduce the blast radius, and neither removes it:

- Every call is capability-checked first. An HVAC mode the unit does not
  advertise is a rejection in the trace, not a failed service call.
- Unchanged decisions are not re-sent, so a stable room is not commanded every
  30 seconds.

A room in lockout should command nothing at all. That is the cheapest way to
confirm the gate works before trusting the rest.

## Covers are written directly, not through Adaptive Cover Pro

The architecture delegates cover control to Adaptive Cover Pro and has Layer 3
set intent only. The component instead calls `cover.set_cover_position`
itself, and Adaptive Cover Pro appears nowhere in it.

If you run Adaptive Cover Pro on the same covers, both write to them. Nothing
will error; the blinds will simply behave oddly. Either take those covers out
of Adaptive Cover Pro's scope, or leave them out of this controller's room
configuration — not both.

## Seeded comfort bands may not match your specification

Rooms are seeded occupied 24–27 HCI and sleep 21–24. Change them in the UI if
you want different numbers; they are starting values, not a recommendation.

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
independently.

Settled as not applicable here: the rooms concerned both face west, will be hot
at the same time, and rarely run together. Recorded rather than left open.

## Single instance

One config entry for the whole house. Rooms live inside it.

## Not in Home Assistant core

A custom integration cannot be awarded a quality scale tier — Custom is a
special tier alongside Internal and Legacy. The project tracks compliance
against all 54 rules in `quality_scale.yaml` so that submission remains
possible, but it is not graded today.
