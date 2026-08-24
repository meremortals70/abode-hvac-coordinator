# Feels-like, free cooling and condensation

Two questions, two tests, and neither can be answered from dry-bulb
temperature.

## Will it feel better with the windows open?

Not "is it cooler outside". What comes through an open window is air at some
temperature, carrying some humidity, moving at some speed, and all three change
how the room feels. A 26 °C breeze beats a still 26 °C room and dry bulb cannot
see the difference.

So the comparison is **outdoor apparent temperature against the room's comfort
index** — the same Steadman formula evaluated under two conditions.

This is the **only** place the outdoor figure meets the indoor one. It is a
comparison, not an input: outdoor apparent temperature is never part of the
comfort index, never part of the dry-bulb target solved from it, and never part
of the thermal model. Three call sites in the whole component — this test, the
diagnostic sensor, and the definition itself.

| | Formula |
|---|---|
| Indoor comfort index | `Ta + 0.33e − 4.00`, plus the sun, still-air and heat-load corrections |
| Outdoor apparent temperature | `Ta + 0.33e − 0.70·ws − 4.00` |

That is the Bureau of Meteorology's non-radiation apparent temperature, and the
indoor index is the identical expression with the wind term dropped because
indoors wind is zero. They are one formula, not two indices that happen to
share a unit, which is what makes comparing them meaningful.

A test asserts they stay identical at zero wind. If that ever fails, the
comparison has silently stopped meaning anything.

The indoor side carries its corrections, so a sunlit room — which feels hotter
than its air temperature — is correspondingly more willing to open up.

**Outdoor wind is damped to 30% before it is believed.** The reading is taken
at ten metres in the clear. What actually reaches a person past a window
opening, a flyscreen and the furniture is a fraction of that. Using the full
figure would overstate the benefit by around three degrees on a windy day,
which is the difference between good advice and advice that opens your windows
on an evening it shouldn't.

With no wind sensor the term is zero, still air is assumed, and the reason
string says so rather than quietly pretending.

## Will I regret it in an hour?

A separate test, and it is the one apparent temperature gets wrong on exactly
the evenings it matters.

A Brisbane evening after rain can feel genuinely better on arrival — cool,
moving air — while carrying far more water than the room holds. The breeze
drops. The moisture stays. The air conditioner spends the next hour removing
it.

So the outdoor dew point must also be at least **1 °C below** the indoor dew
point. This is a **veto, not a trade**: it is not weighed against how much
better the air feels, because the felt benefit is transient and the latent load
is not. A gale of saturated air will fail this test no matter how good it feels
walking through the door.

Both tests must pass.

## Why not wind chill

The Bureau's apparent temperature above already contains the wind term. Wind
chill is not missing from it.

The separate JAG/TI wind chill index is defined only below 10 °C with wind
above roughly 4.8 km/h — conditions Brisbane essentially never sees — and
outside that envelope it returns numbers that are wrong rather than merely
unhelpful. Publishing both would give two "feels like" figures that disagree,
and the one people here recognise is the Bureau's.

## This does not open your windows

It is published, not actuated. This controller owns the air conditioner and the
covers. Windows are yours, or an automation you write against
`free_cooling_advised`.

If the room has media players configured under **Announce through**
([Configuration](configuration.md)), the moment it becomes advised is also
spoken there — once, not repeated every cycle the window stays open. Without
any media players configured, it stays silent in the sensor's attributes,
same as always.

## Will something sweat?

Condensation forms wherever a surface sits below the dew point of the air
touching it. Aggressive setpoints in humid weather put surfaces there, and the
result is mould behind furniture and inside ducts.

The commanded setpoint stands in for the coldest surface the room's air will
touch. That is an understatement — supply air off the coil is colder still —
which is why the margin is a wide 2.5 °C rather than a tight one.

Inside that margin the trace says so and recommends dehumidifying rather than
chasing the setpoint. Nothing is refused on it: the actuator ordering already
prefers dry mode for a latent load, and this explains why it did.

## Why dew point is per room and not house-wide

Because condensation is per room. Your ensuite at 75% and a closed-up bedroom
at 50% sweat at completely different setpoints, and a house-wide average of
rooms ten degrees apart at the dew point is the least useful number available
at the moment you need it.

Dew point is also not a comfort metric. Comfort is the index, which already
carries humidity through the vapour-pressure term. Dew point is here for
condensation and for the free-cooling veto, and both are per room.

## Reading it

| Entity | Value |
|---|---|
| `sensor.<room>_dew_point` | indoor dew point |
| its `outdoor_apparent_c` attribute | what outdoors feels like, damped wind applied |
| its `outdoor_dew_point_c` attribute | outdoor dew point |
| its `free_cooling_advised` attribute | whether both tests passed |
| its `condensation_risk` attribute | whether the setpoint is inside the margin |
| `sensor.outdoor_apparent_temperature` | house-wide, with `wind_ms` and `wind_included` |
| `sensor.outdoor_dew_point` | house-wide |

The decision trace names which test failed and by how much, in words.

## Units

Wind is converted from whatever unit your sensor reports. Steadman's formula
needs metres per second and most Australian feeds publish km/h, so assuming
would make the figure wrong by a factor of 3.6 — in the direction that opens
your windows. A unit nothing can convert logs a warning and falls back to still
air rather than guessing.

## The formulae

Steadman apparent temperature, non-radiation form, as published by the Bureau
of Meteorology. The radiation form exists but needs net absorbed radiation per
unit body area, which no household weather feed supplies; inventing it would
make the outdoor figure less trustworthy than the indoor one it is compared
against.

Dew point is Magnus–Tetens with the Sonntag 1990 coefficients, better than
0.1 °C across the range a house sees.
