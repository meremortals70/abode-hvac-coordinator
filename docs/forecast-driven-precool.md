# The weather forecast

The thermal model learns from what happened. Until 0.8.0 it never looked at
what was about to happen, and the cost was concentrated in one decision.

## The failure

Precool banks thermal mass in a free or cheap window against a load arriving
later. Whether that load exists was decided by comparing the **current**
outdoor temperature against indoors.

At 11:00 on the day of a 38 °C afternoon, Brisbane is often 26 °C and the house
is 25 °C. The old test said no demand ahead. The free 11:00–14:00 window went
unused — on precisely the day it was worth the most, and then the compressor ran
through the 16:00–21:00 peak instead.

## What replaces it

An hourly forecast, refetched every thirty minutes, and one question: **what is
the hottest hour in the next ten, and is it at least 3 °C above the room right
now?**

Ten hours because an evening peak seen from a midday free window is about that
far out, and further ahead the forecast is not worth deciding on. Three degrees
because below that the room drifts up slowly enough that holding the band later
costs less than banking now.

The trace names the peak and when it falls, so a precool that fired can be
checked against the day that followed.

## What it deliberately does not do

**It does not feed the thermal model's learning.** The filter learns from
measured intervals. Folding a forecast into an observation would teach it what
the Bureau predicted rather than what the room did, and the whole value of the
model is that it knows your house rather than your postcode.

**It does not carry irradiance in W/m².** No household weather feed publishes
it. Cloud cover is what is available, and it is converted to a clear-sky
fraction: fully overcast still passes 20% as diffuse radiation, because a room
with west glass under heavy cloud is not the same as that room at night. Where
a feed publishes UV index but not cloud cover, UV is used instead — cruder, and
mainly useful for telling night from an overcast day.

**It does not require a forecast.** Leave the weather entity empty and precool
falls back to comparing current conditions, which is exactly what the
controller did before. The trace says it is falling back rather than silently
returning "no demand".

## Failure

A failed fetch **holds the trajectory already in hand** and raises a repair
issue naming the reason, for the same reason the tariff fetch does: a momentary
reload of a weather integration must not turn into every room losing its
precool decision.

## Setting it up

**Configure → House configuration → Weather forecast.** Any weather entity with
an hourly forecast.

## Reading it

| Where | What it tells you |
|---|---|
| `sensor.forecast_peak` | the hottest hour the forecast can see |
| its `peak_at` attribute | when that falls |
| its `fetched_at` / `covers_until` | how fresh, and how far ahead |
| the mode sensor's `reasons` | why a room is precooling, with the numbers |
| the mode sensor's `rejected` | why it is not |
