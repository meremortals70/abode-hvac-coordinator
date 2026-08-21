# Attribution

Recorded at the moment code was written or adapted, not retrofitted.

## Original work

All code in `custom_components/hvac_coordinator/` is original to this project
and released under Apache 2.0.

## Referenced, not copied

**Home Assistant core** — Apache 2.0
Read `homeassistant/helpers/storage.py`, `homeassistant/helpers/event.py`,
`homeassistant/config_entries.py`, `homeassistant/helpers/device_registry.py`
and `homeassistant/exceptions.py` on the `dev` branch to verify API signatures
rather than relying on documentation. No source copied.

**Home Assistant developer documentation** — the integration quality scale rule
set in `custom_components/hvac_coordinator/quality_scale.yaml` follows the rule
identifiers published in
`docs/core/integration-quality-scale/_includes/tiers.json`. The status and
comment against each rule are this project's own assessment.

## Prior art this project learns from

Named because the design is a reaction to them, not because code was taken.

**Dual Smart Thermostat** — the configuration discipline in this project exists
because of it. Its options list is individually defensible and collectively
unusable. It also drives toggle entities rather than climate entities, which is
what made it unsuitable here.

**Versatile Thermostat** — the regulation layer this design assumes at Layer 2.
Its centralised load shedding is deliberately not used: energy decisions depend
on tariff state and stored energy, which a regulator has no view of.

**RoomMind** — the thermal model approach. Per-room state estimation with an
extended Kalman filter, solar gain from sun position, a convergence criterion,
and hysteresis fallback until converged. The latent term is this project's
addition; models built for heating climates do not need one.

**Adaptive Cover Pro** — read for how cover automations reason about sun
geometry. This project **replaces** it rather than depending on it: cover
decisions, sun-on-glass and the commands themselves are all its own. No code
was taken.

## Science

**Steadman apparent temperature** — the comfort index. R.G. Steadman's shaded
apparent temperature formulation, evaluated at zero wind speed for indoor use.

**ASHRAE Standard 55** — the comfort band table is derived from its sedentary,
still-air comfort zone, converted onto the apparent temperature scale.

Neither is code and neither is copied; both are cited so the numbers can be
checked against their source.
