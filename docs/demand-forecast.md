# Demand forecast

The controller publishes what it expects the air conditioning to draw. **It
never writes battery actuators.**

## The contract

`sensor.abode_hvac_coordinator_demand_forecast`

- **State:** projected kWh over the horizon (8 hours)
- **Attributes:** per-window breakdown, per-room breakdown, and whether every
  contributing room used a converged model

Window entries carry start, end, rate label, the constraints in force, hours
falling inside the horizon, and projected kWh.

**There are no vendor concepts in it.** No reserve, no operation mode, no
manufacturer names. Whoever owns the battery translates it into their own
primitives.

## Why the controller does not touch the battery

Two reasons, the second stronger.

**Two writers on one actuator fail silently.** If this controller set a reserve
and another automation overwrote it four minutes later, nothing would error —
the battery would simply behave oddly.

**Battery control is vendor-specific.** Different manufacturers expose different
primitives with incompatible semantics; a reserve slider that triggers a full
backup cycle above 80% has no equivalent elsewhere. Coding one in would tie this
project to a single brand.

## How the number is arrived at

Per room: how long the compressor needs to pull the room to target, then the
duty cycle needed to hold it there against the drift for the rest of the
horizon. Both come from the learned model.

Energy is spread across tariff windows in proportion to how much of the horizon
each window occupies. That is deliberately crude — it assumes an even draw,
which is wrong in detail and right enough for a reserve decision. Anything
better needs a per-window thermal projection, which is a great deal of machinery
for a number that only has to be roughly correct.

**A room that will not run contributes nothing.** Locked out, or unoccupied with
no heading-home request, means zero rather than a projection nobody asked for.

## When the model has not converged

The room is projected at an assumed 30% duty cycle and marked `modelled: false`
with the reason. `fully_modelled` on the forecast goes false.

A consumer that wants to be careful should treat the total as indicative
whenever `fully_modelled` is false, rather than trusting a confident-looking
number produced by an assumption.

## Using it

```yaml
alias: Hold reserve for the evening
triggers:
  - trigger: time
    at: "15:45:00"
actions:
  - variables:
      projected: "{{ states('sensor.abode_hvac_coordinator_demand_forecast') | float(0) }}"
      confident: "{{ state_attr('sensor.abode_hvac_coordinator_demand_forecast','fully_modelled') }}"
  - action: notify.mobile_app_phone
    data:
      message: >
        HVAC expects {{ projected }} kWh over the next 8 hours
        {{ '(modelled)' if confident else '(estimated)' }}.
```

What you do with that is yours. The reference implementation for any particular
battery belongs in your automations, not in this integration.
