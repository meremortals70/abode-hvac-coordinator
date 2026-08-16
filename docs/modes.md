# Modes

Every room is in exactly one mode. The mode determines which comfort band
applies and whether the room may actuate at all.

## Precedence

Evaluated top to bottom. The first match wins.

| Mode | Entered when | Behaviour |
|---|---|---|
| `LOCKOUT` | A lockout reason is chosen for this room | Never actuates. Beats everything |
| `PRECONDITION` | A heading-home request is active | Drives to the occupied band, ignoring presence |
| `PRECOOL` | A precool window is declared and demand is forecast ahead | Drives to the low bound to bank thermal mass |
| `COAST` | The thermal model predicts the band holds unaided, and the window permits coasting | No compressor |
| `SLEEP` | The sleep schedule is on | Sleep band |
| `OCCUPIED` | Presence detected, or presence unknown | Occupied band |
| `UNOCCUPIED` | No presence | **Off.** Not a wider band |

## The ones that surprise people

**An unoccupied room is off.** Completely. It is not held to a wider envelope,
and it does not run at reduced effort. The only thing that brings it back on is
a heading-home request. If you want an empty room conditioned, ask for it.

**Unknown presence holds occupied.** A presence sensor that has died, dropped
off the network or never reported reads as unknown, not absent. Treating unknown
as absent would let a flat battery in a sensor turn off the air conditioning in
an occupied house. The trace says `presence unknown, holding occupied` when this
happens, so it is visible rather than silent.

**The sleep schedule still applies when presence is unknown.** A dead presence
sensor at 2am should not put the room on the day band.

**Precondition beats presence, deliberately.** That is the entire point of it —
it conditions a room before anyone is there.

**Precool runs whether or not anyone is in the room.** This is the one thing
that overrides an unoccupied room being off, alongside a heading-home request.

That is the whole point of it. The free window is typically the middle of the
day, when the room is empty. The load it is banking against arrives in the
evening, when the room is not. Gating precool on someone being in the room now
would stop it doing its only job.

What it still needs is a load actually coming: `forecast_demand_ahead`. Without
one it is just spending energy early.

**Precool stops at the low bound.** It drives to the bottom of the band and then
stops, rather than continuing to run because free energy is available.

**Coast carries the band of the mode it displaced.** Coast has no band of its
own. A coasting room that was occupied is still held to the occupied band, which
is what the model is predicting will hold. Without that, there would be nothing
to compare against when deciding to leave coast.

## Modes that wait on the thermal model

`COAST` and `PRECOOL` both work, but neither fires until the model has learned
enough about that room. Until then the trace says
`coast: thermal model has not converged for this room` and the band is simply
held — the hysteresis fallback, working as intended rather than a fault.

When coasting starts and stops is covered in [Behaviour](behaviour.md).

## Lockout

Choosing anything other than "Not locked out" in the room's lockout dropdown
means the room is never actuated, whatever else is true.

It is one field, not a tick box and a second screen: the first option means not
locked out, so choosing a reason *is* switching lockout on. It cannot be set by
accident because it is never a free text box. A reason you type is stored for
the whole installation and offered on every room afterwards.

The reason appears in the decision trace, so a room that is doing nothing
always says why.

This exists for rooms under renovation, rooms whose unit is physically
disconnected, and rooms you want configured but not yet live.
