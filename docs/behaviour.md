# Behaviour

What actually happens, in the situations people ask about.

## A window or door opens

The moment any opening configured for that room reports open:

1. The next evaluation runs immediately — openings are watched, so this is
   within a second, not up to 30 seconds
2. Every actuator is refused. The trace says
   `all actuators: an opening in this room is open`
3. The air conditioner is **not** switched off

That last point is deliberate. The controller stops *commanding* the room, but
does not turn the unit off, because a door held open for twenty seconds while
someone carries washing through should not stop and restart a compressor. Short
cycling costs more than the twenty seconds of open door.

When the opening closes, the next evaluation resumes normally. Nothing is
remembered about the interruption.

**If you want the unit off when a window is open**, that is a two-line
automation on your own opening sensor calling `climate.turn_off`. It is not
built in, because whether that is right depends on your house and your unit.

## Coasting starts and stops

`COAST` is not a timer and there is no countdown. It is re-decided every
evaluation, at least every 30 seconds and immediately on any sensor change.

**It starts** when the thermal model predicts the room stays inside its band
unaided for the next hour, and the tariff window permits coasting.

**It stops** the moment any of those stops being true:

- the model no longer predicts the band holds
- the tariff window changes to one where coasting is not permitted
- the room becomes unoccupied, or occupied, or the sleep schedule changes
- a sensor stops reporting, so the model cannot answer

There is no question asked and no confirmation. It is a continuous decision, not
a state you enter and have to leave.

**The room is never allowed to drift out of band while coasting.** The prediction
is checked against the same band that would apply otherwise. If the prediction
turns out to be wrong — the model is learned, not perfect — the next evaluation
sees the room out of band and the compressor resumes, regardless of price.

## Someone leaves the room briefly

This is the case a raw presence sensor gets wrong, and it is handled with two
waiting periods you can change per room.

**Leaving to answer the front door, or make a coffee.** The room stays running.
The vacancy clock starts the moment they leave, and only after it expires
(default 10 minutes) is the room treated as empty. Anyone returning before then
cancels it entirely — the clock does not accumulate across separate absences.

**Arriving to drop something off and leaving again.** The room does not start.
Presence has to hold (default 2 minutes) before the room is treated as occupied.
Without that, putting a laptop on a desk starts a compressor for nothing.

Those defaults are seeded into every new room, so this works without anyone
having to reason about compressor cycling.

### Announcements

Off by default — a house that suddenly starts talking is a surprise. Turn it on
per room and pick which media players to speak through, and before a room is
shut down after a long absence you get two announcements: one when the vacancy
period expires, and a second after the warning period (default 3 minutes)
immediately before it shuts off.

Returning at any point during that cancels the shutdown and the warning. A
later absence warns again rather than shutting off silently.

### Why this is in the integration rather than an automation

Because the compressor decision and the occupancy decision are the same
decision. An external automation calling `climate.turn_off` fights the
controller: the controller re-evaluates 30 seconds later and turns it back on,
because as far as it knows the room is occupied and out of band.

## A room becomes unoccupied

Once the vacancy period has elapsed, the air conditioning goes **off**. Not a
wider band, not reduced effort.

Two things bring it back:

- **A heading-home request** — the `heading_home` action, from an automation on
  leaving work, a phone button, whatever suits you
- **A precool window** — if the tariff declares one and a load is forecast, the
  room precools whether or not anyone is in it. That is the point: the free
  window is the middle of the day when the room is empty, and the load it is
  banking against arrives in the evening

## The compressor is asked for something the unit cannot do

Nothing is sent. The unit's own capabilities are read from its `hvac_modes`, and
a mode it does not advertise is never chosen — the decision skips that step and
moves to the next one.

A cooling-only unit asked to heat does nothing, and the trace says
`compressor: this unit cannot heat`.

Where a fallback exists it is used and recorded: a unit with no dedicated `cool`
mode may still cool in `heat_cool` or `auto`, and the trace says
`cool unavailable, using heat_cool instead`.

## A sensor dies

**Presence unknown is not absence.** A dead presence sensor holds the room in an
occupied mode rather than switching the air conditioning off, and the trace says
`presence unknown, holding occupied`.

**A dead temperature or humidity sensor stops actuation entirely.** There is no
comfort index without both, and the controller will not act on a guess. The
comfort index entity goes unavailable rather than showing a stale number.

Either way the log records the transition once, when it happens and when it
recovers — not every 30 seconds.

## Blinds

Blinds are moved by this integration directly. It does not use, depend on, or
defer to any other cover integration.

They are only moved when the sun is actually on that room's glass, worked out
from the sun's position and the direction the windows face.

They are also only moved when they have somewhere useful to go. A blind already
within 5% of shut is treated as shut, and the ordering escalates to the next
step instead of commanding it again forever.

Choosing blinds turns the air conditioner off for that cycle — the point of
trying the free option first is to not spend compressor energy alongside it. If
the room is still out of band next cycle and the blind has no travel left, the
compressor is reached properly.

## Precool ends

Precool drives to the **lower** bound of the band, then stops. It does not keep
running because energy is free. Once the room is at or below that bound the
trace shows no demand and no actuator.

It also ends when the tariff window closes, because the constraint that
permitted it is gone.

## Two rooms want opposite things

Nothing arbitrates. Each room is evaluated independently against its own band.

For rooms on separate outdoor units that is correct. For two rooms sharing one
compressor it is a real gap — see
[Known limitations](known-limitations.md).

## Falling asleep

The occupied and sleep bands differ by three degrees on a seeded install.
Stepping between them the instant a schedule flips asks the compressor for the
whole gap at once — the loudest it will be all day, at the moment the room is
trying to get to sleep.

The band is instead **ramped across the transition over one hour**. The trace
says how far through the ramp the room is and how long is left. The same
applies waking up, in the other direction.

## Heading home from four hours away

A heading-home request with a deadline no longer starts the compressor the
moment it arrives. The thermal model already answers how long the pull takes;
that answer plus a fifteen-minute margin decides when to begin.

A deadline four hours out therefore costs nothing until it is about an hour
out. The room stays in PRECONDITION the whole time — the request stands — and
the trace says how long it is waiting and why.

Three cases start immediately regardless: no deadline given, a deadline inside
thirty minutes, or a thermal model that has not converged enough to estimate.
The last is the important one. An early start wastes energy; a late one defeats
the feature.
