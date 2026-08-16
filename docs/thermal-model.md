# Thermal model

Per room, learned from observation. **The system works on day one and improves**,
rather than needing a training period before it does anything.

## What it learns

| Coefficient | Meaning | Learned from |
|---|---|---|
| `k_loss` | How fast the room drifts toward outdoor conditions, per °C of difference per hour | Intervals with the compressor off |
| `k_solar` | How much the sun raises the room while it is on the glass, °C/hour | Sunlit intervals with the compressor off |
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

A scalar Kalman update per coefficient, one observation per evaluation
interval: what the room actually did, measured at both ends, against what the
model predicted.

Each coefficient learns only from intervals where it was the thing driving. An
interval with the compressor running teaches nothing reliable about passive heat
loss, because the compressor swamps it.

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
| `PRECOOL` | Is the room forecast to warm later? |
| Heading home | How long to reach comfort? |
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
