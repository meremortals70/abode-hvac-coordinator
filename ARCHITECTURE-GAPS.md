# Architecture gaps

The settled ledger. Every gap identified against the v0.3 proposal, with what
happened to it. Cite a section here rather than re-arguing a closed question.

**Version 0.8.0. The architecture is complete.** Eleven gaps were identified at
v0.5.3. All eleven are closed.

What remains is not architecture. It is tuning constants against a real house,
and only running it produces those.

---

## Closed

### 1. Layer 2 regulation — CLOSED in 0.6.0

Was the largest single gap. The plan was to adopt an existing over-climate
regulator; rejected on inspection, because what was needed is one narrow thing
and adopting a regulator would have imported presence handling, interlocks and
preset machinery Layer 3 already owns — two controllers on one actuator.

Built in `regulate.py`: an integral-only outer loop trimming the commanded
setpoint until the room sensor reaches the target, plus short-cycle protection.
See `docs/regulation.md`.

### 2. Weather and irradiance forecast — CLOSED in 0.8.0

Built in `weather.py`. An hourly forecast, refetched every thirty minutes,
answering the question the thermal model could not: not whether it is hot now
but whether the afternoon is coming.

This was the gap with a concrete cost. Precool decided demand from current
conditions, so at 11:00 on the day of a 38 °C afternoon — 26 °C outside, 25 °C
inside — it saw no load and left the free window unused on exactly the day it
was worth the most.

Cloud cover and UV index give a clear-sky fraction. Irradiance in W/m² is not
used because no household feed publishes it. The forecast does not feed the
model's learning: the filter learns from what the room did, not from what the
Bureau predicted. See `docs/forecast-driven-precool.md`.

### 3. Feed staleness — CLOSED in 0.6.0

Built in `staleness.py`. Every reading carries an age; one tolerance per feed
class; too old is treated as absent, which every path already handles.
See `docs/staleness.md`.

### 4. Free cooling and condensation — CLOSED in 0.6.0, revised in 0.7.0

Built in `psychro.py`. The 0.6.0 test compared dry bulb to dry bulb, which was
wrong: what matters is how the room will feel with the windows open, and that
depends on temperature, humidity and wind together.

0.7.0 compares outdoor apparent temperature against the room's comfort index —
one Steadman formula evaluated under two conditions — with the dew point kept
as a separate veto. Wind is damped to 30% before it is believed. See
`docs/free-cooling.md`.

Wind chill was considered and rejected: the Bureau's apparent temperature
already contains the wind term, and the JAG/TI index is undefined above 10 °C.

**Settled by Jason:** apparent temperature is the correct basis for the
free-cooling comparison.

Proposal v0.3 §5's rule is that the outdoor figure is **never part of the
internal computation** — not the comfort index, not the dry-bulb target solved
from it, not the thermal model. That stands, and the code honours it: three
call sites, being the free-cooling test, the diagnostic sensor, and the
definition.

What §5 does not forbid is the comparison itself, and one is required — whether
to open the windows. That question is how a room will feel with outdoor air in
it against how it feels now, and it cannot be answered otherwise. The dew point
remains a separate veto.

### 5. Deadline-aware preconditioning — CLOSED in 0.6.0

Built in `scheduling.py`. The thermal model already answered how long a pull
takes; that answer plus a fifteen-minute margin now decides when to begin.

### 6. Sleep band ramp — CLOSED in 0.6.0

Built in `scheduling.py`. The band interpolates across a mode transition over
one hour rather than stepping three degrees at bedtime.

### 7. Multi-head arbitration — CLOSED as not applicable

**Settled by Jason, and not to be reopened.** Both rooms face west and will be
hot at the same time in summer; a dramatic divergence between them is unlikely,
and it is rare that both run at once in any case. There is no arbitration to
build for this house.

If a future install puts two heads with genuinely opposing loads on one outdoor
unit, this reopens with that as the new evidence. Nothing else reopens it.

### 8. Latent and sensible split — CLOSED in 0.8.0

`DRY_MODE_RH_THRESHOLD` was a single humidity number standing in for a decision
the thermal model already had the data to make.

Both routes are now measured in comfort index closed per hour — `k_sensible`
times the index's sensitivity to temperature, against `k_latent` times its
sensitivity to humidity — and compared directly. Cooling must be 25% faster to
be chosen, because dry mode achieves the same effect at lower duty.

A point of relative humidity moves the index by 0.087 at 22 °C and 0.140 at
30 °C, which is why one threshold could never have worked. While either
coefficient is unconverged the old threshold applies and the trace says so.
See `docs/latent-and-sensible.md`.

### 9. Illuminance threshold — CLOSED in 0.8.0 by removal

Recorded, acted on by nothing, and correctly so: a semi-transparent blind reads
bright when fully closed. The field, its config option and its string are gone.
Sun position is established from geometry, which is what `sun.py` was for.

### 10. Tariff ownership — CLOSED in 0.6.0

The controller held its own plan: windows, prices, feed-in, supply charge. It
now reads a forward interval series from Abode Power Tariffs and holds none of
it. Prices are dollars per kWh throughout, the publisher's unit.

### 11. Domain naming — CLOSED in 0.6.0

Renamed `hvac_coordinator` to `abode_hvac_coordinator`.

---

## Not gaps

Recorded so they are not raised again as though they were.

**Constants that want tuning against a real house.** The Layer 2 integral gain,
the wind penetration fraction, the dry-mode advantage ratio, the precool demand
margin. Each is a number with a stated basis and a documented direction. Wrong
values produce suboptimal behaviour, not wrong architecture, and no amount of
design work improves them — only running the thing does.

**Thermal model convergence.** Configuration and time, not structure. The
filter, its coefficients and its convergence reporting are built.

**Layer 1 hardware.** The ESPHome Matter-over-Thread adaptor is a separate
project. Layer 3 consumes a `climate` entity and does not care what is beneath
it, which is the point of the layer split.
