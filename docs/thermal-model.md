# Thermal model

Per room, learned from observation. **The system works on day one and improves**,
rather than needing a training period before it does anything.

## What it learns

| Coefficient | Meaning | Learned from |
|---|---|---|
| `k_loss` | How fast the room drifts toward outdoor conditions, per °C of difference per hour | Intervals with the compressor off and dry mode off |
| `k_solar` | How much the sun raises the room while it is on the glass, °C/hour | Sunlit intervals with the compressor off and dry mode off |
| `k_sensible` | How fast the compressor moves dry bulb, °C/hour | Intervals with the compressor running |
| `k_latent` | How fast dry mode moves humidity, percentage points/hour | Intervals with dry mode running |

## Why sensible and latent are separate

This is the difference between this model and one built for a heating climate.

A heating model learns heat loss, heating power and solar responsiveness — all
sensible-heat terms — because northern-hemisphere heating has no latent
component worth modelling. A humid subtropical climate does.

Rain is the case that separates them. Dry bulb falls while humidity climbs
toward saturation, so **sensible load drops as latent load rises**. A filter
fitting one coefficient to both is wrong on exactly the days the two diverge —
and the compressor may still need to run to hold the comfort band on a day that
feels cool.

## How it learns

A scalar Kalman update per coefficient: what the room actually did, measured at
both ends, against what the model predicted.

**An observation spans several evaluations, not one.** Evaluation runs every
thirty seconds and an interval shorter than a minute carries no information
over sensor quantisation, so the anchor is held until the interval is long
enough rather than replaced each cycle. It is also reset without observing
whenever the compressor or dry mode changes state inside the interval, because
an interval that spans a change teaches nothing reliable about either side of
it.

Each coefficient learns only from intervals where it was the thing driving. An
interval with the compressor running teaches nothing reliable about passive heat
loss, because the compressor swamps it.

**Whether the compressor is running is read from `hvac_action`, not from the
mode.** They are different questions. A head sitting at setpoint in `cool`
reports `cool` with the compressor idle, and until 0.8.6 all of that idle time
counted as an interval the compressor was driving — so `k_sensible` was an
average of running and idling and described neither. Where a climate entity
publishes no `hvac_action` at all the mode is used as a fallback, and which
source answered is recorded per room in diagnostics, because a room learning
from mode has a diluted sensible coefficient and that should be visible rather
than assumed.

**Dry mode is not a passive interval.** It energises the compressor and moves
both dry bulb and humidity. Passive learning previously ran whenever the
compressor direction was zero, and dry mode has no sensible direction, so every
drying interval was folded into `k_loss` and `k_solar` as though nothing had
been driving the room. Since 0.8.6 a drying interval teaches the latent
coefficient and nothing else.

Full matrix estimation is not used. Over short intervals the coefficients are
near-independent — heat loss acts when the compressor is off, compressor
authority when it is on — so the cross terms a matrix filter would estimate are
mostly noise.

Intervals are discarded when they carry no information: shorter than a minute
(sensor quantisation dominates), longer than an hour (something else changed
inside it), indoor and outdoor level (nothing driving), or the room moving
against the compressor (a door open, or a heat load — not the unit's lesson).

## Convergence

Each coefficient carries its own variance and sample count, and is trusted only
after **20 samples** and once its variance has fallen below **0.05**.

Until then, predictions are refused and the caller falls back to hysteresis:
the band is simply held. `COAST` is never entered on an unconverged model,
because "the model cannot say" must mean *do not coast*, never *yes*.

Process noise is small and non-zero, so the filter keeps listening. A house
changes — new curtains, a door left open, a season — and a filter with no
process noise eventually stops learning.

## What it is used for

| Consumer | What it asks |
|---|---|
| `COAST` | Does the band hold unaided over the next hour? |
| `PRECOOL` | How far to overshoot. Whether a load is coming is the weather forecast's answer, not the model's |
| Heading home | How long to reach comfort, and therefore when to start |
| Dry against cool | Which closes the comfort gap faster, from `k_sensible` and `k_latent` |
| Demand forecast | How much energy over the horizon? |

## Seeing its state

Every room's mode sensor publishes the coefficients in its `model` attribute —
value, variance, sample count and whether it has converged. A decision that
depended on the model can be checked against what the model actually believed
at the time.

The same appears in diagnostics.

## Persistence

Learned state is written to `.storage` at most every five minutes, with atomic
writes, and flushed when Home Assistant stops.

**Losing it costs convergence time, not correctness.** Unreadable stored state
starts fresh and the hysteresis fallback holds until it has learned again.
