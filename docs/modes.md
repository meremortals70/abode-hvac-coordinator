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
| `COAST` | The weather will bring the room back into band, or its band holds unaided, and (for a room already in band) the window permits coasting | No compressor |
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

## Letting the weather do the work

A room that has drifted out of its band is not always worth running the
compressor for. If outdoors is warmer than the room's lower comfort bound, the
room will warm back up by itself; heating it is working against the weather.
The same is true the other way on a cool day. (DR-053)

So before the compressor is started for a room that is out of band, the
controller projects the room forward with nothing running, in five-minute steps
over the next hour, from what the room has learned:

> how fast the room drifts toward outdoors (`k_loss`), plus how much the sun
> adds while it is on the glass (`k_solar`)

driven by the **forecast** outdoor temperature at each step, and by where the
**sun** will be at each step against the direction the room's windows face,
scaled by the forecast's cloud. The room coasts if that projection puts it back
inside its band, and keeps it there, within a limit: **the time the compressor
itself would take plus ten minutes**, or fifteen minutes where the model cannot
estimate that. A cooling room tries the fan first, as always.

This is recomputed from the room's real temperature every evaluation. A room
that is not moving as projected stops being left alone as soon as the projection
no longer returns it in time.

**Until the model has learned enough, a simpler floor applies.** Outdoors feeling
at least as warm as the band's lower bound means no heating; outdoors feeling no
warmer than the upper bound, with the sun not on the room, means no cooling. A
missing outdoor reading means neither, and the room is driven as before.

What the projection cannot see: people and equipment in the room (it runs
optimistic for cooling in an occupied room, which the every-cycle recomputation
corrects), and humidity changing over the hour (it is held at its current value).

`PRECOOL` and `PRECONDITION` are never replaced by a coast. Their own reasons
for running the compressor are not the weather's.

## Modes that wait on the thermal model

`COAST` and `PRECOOL` both work, but neither fires until the model has learned
enough about that room. Until then the trace says
`coast: thermal model has not converged for this room` and the band is simply
held — the hysteresis fallback, working as intended rather than a fault.

**A sunlit room needs the solar coefficient too, not just heat loss.** Until
0.8.6 the drift prediction added the solar term only once `k_solar` had
converged, but returned a number either way — so a west-facing room in the
afternoon got a drift estimate built from heat loss alone, missing its largest
contribution. The model reported that the band would hold, and the room entered
`COAST` with the sun full on the glass. The prediction now refuses when the sun
is on the room and the solar term is not yet known, which the mode machine
already treats as *do not coast*. Absence of a converged coefficient is not
evidence that its contribution is zero.

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
