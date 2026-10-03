# DR-053: A room coasts when its own physics will bring it back into band

| | |
|---|---|
| Status | Accepted; amends DR-032 (the end-point check is replaced; the one-hour hold for a room inside its band is unchanged) |
| Since | 0.9.0 (2026-10-03) |
| Origin | Instruction, 2026-10-03: if outdoors is warmer than the room's comfort level the aircon should never heat it, it should coast or fan and let natural heat exchange bring the room back in band, and the reverse when it is cold outside. Window direction, sun and outside weather are the inputs |
| Related | DR-006, DR-032, DR-035, DR-044, DR-051, DR-054 |

## Decision

The room's unaided temperature is projected forward over the coast horizon in
five-minute steps from the learned model:

    dT/dt = k_loss * (T_out(t) - T) + k_solar * sun(t)

`T_out(t)` is the forecast outdoor temperature at each step, or the current
reading where no weather forecast is configured. `sun(t)` is the sun on the room's
window at that step, from the sun's position and the window's direction, times the
forecast's clear-sky fraction. The projection gives three things: whether the room
is inside its band now, the first time it is back inside, and whether it stays
inside to the horizon.

A room outside its band is not driven by the compressor when the projection
returns it to band within the return limit and keeps it there to the end of the
projection. The return limit is the time the compressor itself is estimated to
need to reach the band plus ten minutes, or fifteen minutes where the model cannot
estimate that. The projection still looks one hour ahead, to check the room stays
in band. A room already inside its band and staying inside it coasts as before. A
coasting room has its unit off, and a cooling room tries the fan first, as the
actuator order already does. This holds in both directions, so heating into a
warm outdoors and cooling into a cold one follow from the projection and need no
separate rule.

The projection is recomputed from the room's actual temperature every cycle. A
room that is not moving as projected is driven as soon as the projection no longer
returns it in time. The predicted return time, or the reason there is no
prediction, is written into the trace.

Where the model has not converged, or a sunlit room has no converged solar term,
there is no projection. The fallback is the plain outdoor comparison: no heating
while the outdoor apparent temperature is at or above the band's lower bound, and
no cooling while it is at or below the upper bound unless direct sun reaches the
room. A missing outdoor reading means neither applies, and the room is driven as
today. PRECOOL and PRECONDITION are never replaced by a coast.

## Why

On 2026-10-03 at 11:08:38 the Office was sent Heat with the index 0.12 below its
floor, and the unit's own outdoor sensor read 25.0 C. In the source the direction
to heat or cool is decided from the comfort index alone. The only unaided
prediction was a straight line to the point an hour ahead, with one outdoor
reading and a yes or no for sun held fixed throughout. The mode machine coasted
any room where that end point landed in band, whether or not the room was in band
now, with no limit on how long the room could be left, and it could not see the
forecast, the sun moving across the window, or the drift slowing as the room nears
the outdoor temperature. It did not prevent the Heat.

## Rejected

- **A threshold rule on outdoor against the band alone.** It treats a room 0.1
  below its floor and one 5 below it the same, and ignores the rate, the sun, the
  window and the forecast. It survives only as the fallback while the model has not
  converged.
- **Keep the end-point check.** This is the 0.8.14 behaviour above.
- **A per-room Heating switch.** It hands the user a decision the physics can make,
  and it defaults wrong for every room never switched.
- **Compare against the room's current temperature instead of the band.** The band
  is the definition of comfort and the room must end inside it.
- **Keep the one-hour limit for a room out of band.** The instruction was that an
  hour is too long now that the room loop can hold a room gently.

## Consequences

The component can leave a room below its floor, or above its ceiling, for about as
long as the unit would have taken to bring it back, plus ten minutes, when the
projection says the weather will do it. The ten and fifteen minutes are
judgements, not measured values. The drift model has no term for people or
equipment, so the projection runs optimistic for cooling in an occupied room; the
every-cycle recomputation corrects that, at the cost of some minutes. Humidity is
held at its current reading across the horizon when the band is converted to
dry-bulb limits. The tariff's "coasting permitted" flag still gates a room that is
inside its band; it does not gate the weather coast of a room outside it, because
the free-power window is exactly when a room should be let to drift instead of
being run. The outdoor apparent temperature is compared in a second place,
`psychro.py`, and is still never an input to the room's own computation.

## In the code

Checked against: 0.9.0. **Conforms.**

- `thermal.py:483` - `def project_unaided` - the stepped projection
- `thermal.py:140` - `def unaided_outlook` - when the room is back in band for good
- `sun.py:142` - `def solar_position` - the sun at a moment that has not happened yet
- `coordinator.py:2249` - `def _unaided_inputs` - forecast, sun on the window and the return limit
- `coordinator.py:1459` - `def _predicted_to_hold` - the in-band hold now read from the projection
- `modes.py:538` - `def _weather_coast` - the coast decision and its return limit
- `psychro.py:242` - `def weather_works_against_compressor` - the fallback until the model converges
