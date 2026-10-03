# Known limitations

Written plainly, because a limitation you discover yourself is worse than one
you were told about.

## Real compressor behaviour is proven by one live room, not by this codebase

Every build runs continuously against a live air conditioner in Jason's own
office, used as the test room. Faults found there and reported back are what
has driven every real-world fix in this project's history. The 631 tests and
the clean install/unload are what an automated pass can confirm on their
own — they do not, by themselves, prove a compressor, blind or fan did the
right thing on real equipment; that proof comes from the office room, not
from the suite.

What the office room has not yet covered — a second climate zone, a
different unit brand, months rather than weeks of runtime — is still
unproven. Treat a build as validated only as far as it has actually run in
that room.

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

## This controller owns cover control directly

Layer 3 calls `cover.set_cover_position` itself; nothing is delegated to
another integration.

If you already automate the same covers some other way — another blind
controller, your own automation — both will write to them. Nothing will
error; the blinds will simply behave oddly. Either take those covers out of
the other automation's scope, or leave them out of this controller's room
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

The architecture is complete. Seven constants are not: the Layer 2 integral
gain, the half hour the room loop is given to close an error, the six minutes
of the room's own rate it is led by, the ten minutes (fifteen where the model
cannot estimate) a room may be left to come back on the weather's account, the
wind penetration fraction, the dry-mode advantage ratio and the precool demand
margin. Each has a stated basis and a documented direction, and each will be
wrong by some amount until the thing has run through a summer.

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

## The integral gain is one number for every room

Since 0.9.0 the room loop is a PID built on what the room has learned: the
feed-forward and the proportional term come from the room's own learned rate
against approach, and the derivative from its own measured rate of change. The
**integral** gain (`INTEGRAL_GAIN_PER_HOUR`) is still one constant for every room.
A room with a very fast response and a room with a very slow one converge on
their trim at the same rate, which means the slow one takes longer than it
strictly needs to.

This is not a deliberate trade-off. The thermal model already learns `k_loss`
per room, which is the room-response figure the integral gain should be sized
against. It is not wired to it yet, and doing so needs a considered
control-loop change tested against real cycling, not a quick constant swap.

## The setpoint step is trusted from the integration that publishes it

The setpoint is rounded to the step the climate entity advertises (DR-051). The
Intesis integration publishes a step of 1.0 as a hardcoded default, not read
from the unit. That is right for the Office unit, which holds whole degrees and
nothing else: its recorded setpoints show fractions lasting seconds and whole
degrees lasting hours. A different unit through the same integration that holds
finer steps would be rounded to whole degrees all the same, and one that holds
whole degrees but advertised half would see the old resend loop return. Nothing
yet compares the setpoint a unit reports back with what was sent.

## The weather coast cannot see people or equipment

The projection a room coasts on (DR-053) is built from heat loss and the sun. It
has no term for the people or the equipment in the room, so for cooling in an
occupied room it runs optimistic: it can say the weather will bring the room
down when a room full of people will not let it. It is recomputed from the
room's real temperature every cycle, so the cost is some minutes of a room left
warm, not a room abandoned, and the limit (the compressor's own estimated time
plus ten minutes) bounds it. Humidity is held at its current reading across the
hour when the band is turned into dry-bulb limits.

## A slow sensor limits what the room loop can do

The derivative term works on the room's own reading, and a sensor that reports
every five or six minutes gives it a stepped signal. The rate is only updated
when the reading moves and decays between readings, so one step does not read as
a rate, but a loop led by a rate that is updated every six minutes is a gentler
loop than one fed every thirty seconds. A faster room sensor improves it.

## The short-cycle guard is not told about changes made by hand

A stop or a start made from the room's controls, the wall or the remote does not
pass through the compressor guard, so for up to ten minutes after you turn
Automatic control back on its picture of the compressor can be wrong, and a stop
the coordinator makes inside the minimum run of a run you started by hand is not
refused.

## A room configured before 0.9.0 waits for its air conditioner

Such a room has no record of what its air conditioner offers. It is read the
first time Home Assistant starts on 0.9.0 and the result is kept. If the air
conditioner has not reported by then, which happens when Home Assistant has just
started and the unit's own integration has not connected, nothing is sent to the
room until it does. The room's trace says it is waiting. It is not turned off.

## Test coverage is 92%, not the 95% the silver tier asks for

The whole suite covers 92% of the integration's lines at 0.9.0. The gaps are in
the actuator (77%), the integration setup and unload (76%) and the coordinator
(84%), mostly failure paths: a service call that raises, a feed that goes stale
mid-evaluation. The pure decision modules are at 98% or better.

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
