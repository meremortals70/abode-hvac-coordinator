# Drying against cooling

The thermal model learns two rates separately:

- `k_sensible` — degrees of dry bulb per hour, with the compressor cooling
- `k_latent` — points of relative humidity per hour, in dry mode

Learning them separately was the whole reason the model was built the way it
was. Until 0.8.0 the actuator ordering read neither, and chose dry mode on a
single humidity threshold: above 65%, dehumidify.

## Why a threshold cannot work

The comfort index is `Ta + 0.33e − 4.00`. How much a point of relative humidity
moves it depends entirely on temperature, because it acts through the
saturation vapour pressure:

| Room | A degree of cooling | A point of humidity |
|---|---|---|
| 22 °C, 65% | 1.344 HCI | 0.087 HCI |
| 30 °C, 65% | 1.520 HCI | 0.140 HCI |

Same humidity reading. A point of it is worth **60% more** at the warmer
temperature. A threshold sees "65%" both times and gives the same answer.

## What replaces it

Both routes are measured in the same units — index closed per hour — and
compared:

```
cooling  =  k_sensible  ×  dHCI/dT
drying   =  k_latent    ×  dHCI/dRH
```

`k_sensible` is degrees per hour and `k_latent` is humidity points per hour;
the sensitivities convert each into index per hour, so the comparison is
between two rates of the same thing rather than between a rate and a threshold.

Cooling has to be **25% faster** to be chosen. Dry mode runs the compressor at
lower duty for the same effect, so a near-tie goes to drying — cooling has to
actually win, not merely draw.

## Until the model converges

`k_sensible` and `k_latent` each carry their own variance and sample count. An
unconverged coefficient is reported as absent rather than as a number, because
handing over a value the filter has not settled on would look like knowledge
and behave like noise.

While either is absent the old 65% threshold applies, and **the trace says so**
— "the model has not converged, falling back to the humidity threshold". A
fallback that looks like a decision is worse than no fallback.

## When a room never dries

A room whose `k_latent` has converged at or near zero has shown no latent
response — the unit's dry mode does nothing measurable there. The trace says
that rather than repeatedly choosing an actuator that has never worked.

## Reading it

The mode sensor's `reasons` and `rejected` carry both rates and both routes,
in index per hour, with the estimated minutes to close the gap. A decision that
looks wrong can be checked against the numbers that produced it.
