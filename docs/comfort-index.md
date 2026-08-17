# Comfort index

## What it is

The Home Comfort Index is the Steadman shaded apparent temperature with wind set
to zero:

```
HCI = Ta + 0.33e - 4.00

e   = (RH / 100) * 6.105 * exp(17.27 * Ta / (237.7 + Ta))
```

`Ta` is indoor dry bulb in °C, `RH` is indoor relative humidity in percent, and
`e` is water vapour pressure in hPa.

It is computed from **indoor temperature and indoor relative humidity, and
nothing else.**

## Why this and not heat index or humidex

Both are undefined below roughly 26 °C. A sleep band sits below that, so neither
can express it. Steadman is continuous across the whole range a house operates
in, and collapses toward dry bulb as the air dries out.

It also moves in the right direction. **HCI rises with humidity at fixed
temperature**, because sweat evaporates less readily in humid air. Any index
that falls as humidity rises will tell you a muggy room is comfortable, and the
controller will sit there doing nothing on the night you most want it running.

## What air temperature and humidity cannot see

A person exchanges heat four ways: conduction and convection to the air,
evaporation of sweat, and **radiant exchange with the surfaces around them**.
Dry bulb and humidity describe the first two. They say nothing about the third.

That gap is why a room can read 24 °C on a wall sensor while someone sitting in
it is uncomfortably hot. Three corrections close it:

| Correction | Adds | When |
|---|---|---|
| Sun on the glass | up to +3.0 HCI | Only when the sun actually reaches the glass, scaled by how far the covers are closed |
| Still air | +1.0 HCI | No fan, and the air conditioner not running |
| Heat load in the room | +1.0 HCI | A configured heat-source entity is on |

**Eaves are checked before anything else.** A window under a soffit is fully
shaded whenever the sun is high enough that the eave's shadow reaches past the
bottom of the glass — in a subtropical summer, most of the middle of the day.
Give the room the eave's projection and its height above the glass and the
controller works this out; without them it assumes no overhang.

**A closed blind is not the same as no sun.** A 50% blind passes roughly half
the radiant load. Even a fully closed one passes about 15%, because it absorbs
the energy and re-radiates it inward — which is why a room behind a shut blind
on a hot afternoon is still warm.

Worked example, at 24 °C and 60% humidity:

| Conditions | HCI |
|---|---|
| Shaded, air moving | 25.9 — comfortable |
| Sun on the glass, no blind | 28.9 — warm |
| Sun, 50% blind | 27.6 — warm |
| Sun, 50% blind, still air, workstation running | 29.6 — warm |

The last row is an office on a sunny afternoon. The air-only index calls it
comfortable and it is not.

**The setpoint moves with it.** A sunlit room is asked for colder air to reach
the same felt comfort — the inverse solve uses the same corrections, so the
band means the same thing in every room.

Every trace publishes `hci_air_only` and `radiant_fraction` alongside `hci`, so
a surprising number can be read rather than argued with.

**These coefficients are physically motivated, not measured.** They are the
first thing to adjust if a room consistently feels wrong. Compare how a room
feels against its `hci`, `hci_air_only` and `radiant_fraction`.

## Two things it is not

**It is not a temperature.** It is reported in HCI, not °C. 25 HCI is not 25 °C.
At 50% humidity, 25 HCI is about 24 °C dry bulb; at 80% it is about 21.5 °C.

**It is not the BoM outdoor apparent temperature, and the outdoor figure never
enters this computation.** The comfort index is built from indoor temperature
and indoor relative humidity, plus the radiant, still-air and heat-load
corrections. Nothing outdoors is an input to it, and nothing outdoors is an
input to the dry-bulb target solved from it or to the thermal model.

That rule is about the *internal computation*. It does not forbid comparing the
two, and one comparison is required: whether opening the windows would help.
That question is precisely "how will this room feel with outdoor air in it
against how it feels now", and it cannot be answered any other way.

The comparison is legitimate because the two are one formula:

```
indoor  HCI = Ta + 0.33e            - 4.00   + corrections
outdoor AT  = Ta + 0.33e - 0.70·ws  - 4.00
```

The index is the Bureau's expression with the wind term dropped, because
indoors wind is zero. Restoring it gives the same quantity under different
conditions rather than a different quantity sharing a unit. A test asserts the
two stay identical at zero wind; if that ever drifts, the comparison has
silently stopped meaning anything.

So: never an input, once a comparison. See [Free cooling](free-cooling.md).

## The scale

Derived from the ASHRAE 55 sedentary comfort zone, still air, converted onto
this scale:

| Band | HCI | Meaning |
|---|---|---|
| Cold | below 20 | Below the winter comfort floor |
| Cool | 20 – 23.5 | Comfortable in a jumper |
| **Comfortable** | **23.5 – 27.5** | The zone. Centre 25.5 |
| Warm | 27.5 – 31 | Tolerable, not pleasant |
| Hot | above 31 | 27 °C at 60% and above |

ASHRAE's still-air sedentary zone runs about 23–26 °C at 50% RH. Those edges
land on 23.6 and 27.5. The winter floor of 20 °C at 50% lands on 19.8.

Clothing and metabolic rate shift the whole scale. Sitting in shorts moves it
down about 1; long sleeves move it up about 1.

## Starting bands

| Mode | Band | Why |
|---|---|---|
| Occupied | 24 – 27 | Inside the comfortable zone with headroom |
| Sleep | 21 – 24 | Sleeping comfort runs about 3 below sitting comfort |
| Precool | 24 – 27 | Same as occupied; precool drives to the low bound |

These are starting points, not recommendations you must accept. Adjust them
against how the room actually feels.

## Reference table

HCI at a range of indoor conditions:

| °C \ RH | 40% | 50% | 60% | 70% | 80% |
|---|---|---|---|---|---|
| 20 | 19.1 | 19.8 | 20.6 | 21.4 | 22.2 |
| 22 | 21.5 | 22.4 | 23.2 | 24.1 | 25.0 |
| 24 | 23.9 | 24.9 | 25.9 | 26.9 | 27.9 |
| 26 | 26.4 | 27.5 | 28.6 | 29.7 | 30.8 |
| 28 | 29.0 | 30.2 | 31.5 | 32.7 | 33.9 |
| 30 | 31.6 | 33.0 | 34.4 | 35.8 | 37.2 |

## Deriving the setpoint

The user sets a band. The controller derives the dry bulb target from the band
and the measured humidity:

- **Occupied, sleep, precondition** — target the middle of the band
- **Precool** — target the low bound, to bank thermal mass

The inverse has no closed form, because the vapour pressure term itself depends
on temperature. It is solved by bisection, which is safe here because the index
rises monotonically with temperature at fixed humidity.

**Setpoints are clamped to 5–40 °C.** A band that the measured humidity makes
unreachable is clamped and the clamp is written into the decision trace. It is
never passed to the hardware.

## Displaying it

A template sensor mirroring the integration's own calculation, for a dashboard
gauge:

```jinja
{%- if has_value('sensor.office_temperature') and has_value('sensor.office_humidity') -%}
  {%- set t = states('sensor.office_temperature') | float -%}
  {%- set r = states('sensor.office_humidity') | float -%}
  {%- set vp = (r / 100) * 6.105 * (2.718281828 ** (17.27 * t / (237.7 + t))) -%}
  {{ (t + 0.33 * vp - 4.00) | round(1) }}
{%- else -%}
  {{ none }}
{%- endif -%}
```

Set the unit to `HCI`, not `°C`. Calling it °C invites exactly the confusion
this document exists to prevent.

The integration already publishes this as `sensor.<room>_comfort_index`, so the
template is only needed if you want the figure for a room the integration does
not manage.

## The same formula, outdoors

The index above is the Bureau of Meteorology's non-radiation apparent
temperature with the wind term dropped, because indoors wind is zero:

```
indoor  HCI = Ta + 0.33e            - 4.00   + corrections
outdoor AT  = Ta + 0.33e - 0.70·ws  - 4.00
```

`apparent_temperature()` in `hci.py` is that second line. It exists so the
free-cooling test can compare how a room feels against how outdoors feels
without comparing two different quantities. A test asserts the two agree
exactly at zero wind; if it ever fails, the comparison has stopped meaning
anything.

Wind is not applied indoors. Air movement is a boolean penalty
(`STILL_AIR_HCI`) rather than a speed, because no household has an indoor
anemometer and a fan's effect on a person depends on where they are sitting.
That is a coarser treatment than the outdoor one and deliberately so.
