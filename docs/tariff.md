# Tariff

This integration does not hold your electricity plan. It reads one.

The plan — periods, rates, prices, feed-in, the daily supply charge, the
constraints that apply in each period — belongs to
[Abode Power Tariffs](https://github.com/meremortals70/abode-power-tariffs).
This controller asks it what is in force and what is coming, and acts on the
answer.

## Why they are separate

The plan is not an HVAC concept. A battery automation needs it. A hot water
timer needs it. A pool pump schedule needs it. Every one of those either
duplicates the plan or reads it from somewhere, and duplicating it means a plan
change has to be made in four places and will be made in three.

Splitting them also keeps the boundary honest. A tariff integration that
decided when the air conditioner ran would be making comfort decisions. A
comfort controller that held prices would be a billing system. Neither happens
here: one publishes what the plan says, the other decides what to do about it.

## What is read

One service call, `abode_power_tariffs.get_intervals`, asking for the next
twenty-four hours at half-hourly resolution. Each interval carries:

| Field | Used for |
|---|---|
| `start_time`, `end_time` | which interval is in force |
| `rate` | the rate label published on `sensor.tariff_rate` |
| `per_kwh` | costing the demand forecast, in **dollars** per kWh |
| `export_per_kwh` | read, not currently acted on |
| `constraints` | the rules below |
| `coasting_permitted` | whether COAST may be entered in this interval |

`duration`, `allowance_kwh`, `day_pattern` and `forecast` are published by the
tariff integration and are not read here. Nothing this controller decides turns
on them.

Consecutive intervals that agree on rate, constraints and coasting are
collapsed back into periods before the demand forecast sees them, so a
five-hour peak is one window in the forecast rather than ten slices.

## Constraints

A constraint is an **absolute rule**, not a price hint. It is never traded
against comfort and never traded against cost. If the plan says no grid import,
the room does not import from the grid, whatever it would cost to do otherwise.

Three are acted on here:

| Constraint | What this controller does |
|---|---|
| `precool_opportunity` | may enter PRECOOL, if the forecast shows demand ahead |
| `no_grid_import` | will not run the compressor from the grid in this interval |
| `grid_charge_battery` | recognised, and deliberately ignored — batteries are not this controller's actuator |

Anything else is carried through into the decision trace and reported as a
repair issue naming it. That is not a gap. A constraint this controller does
not act on is a constraint some other system owns, and adding one to your plan
needs no code change here.

## Refresh and failure

The series is fetched at startup and every fifteen minutes after. Boundaries
are resolved from the series itself, so the refresh interval is only about how
quickly a plan you have just edited is picked up — not about decision accuracy.

A failed fetch **holds the series already in hand** and raises a repair issue
naming the reason. Dropping to no tariff on one bad call would turn a momentary
reload of the tariff integration into every room losing its constraints, which
is worse than a series a few minutes old.

If the fetch keeps failing until the series runs out, intervals stop resolving
and the controller behaves as it does with no tariff configured. The repair
issue says which of the two you are in.

## Running without a tariff

Supported, and not a degraded mode with a warning attached. Leave the tariff
entry empty and:

- every room is still held to its comfort band, exactly as before
- COAST still works — it depends on the thermal model, not on the plan
- PRECOOL never fires, because nothing declares an opportunity
- `no_grid_import` is never in force
- the demand forecast still publishes kWh, but with no per-window cost

The house-wide configuration screen says so in those terms rather than leaving
the field blank.

## Units

Prices arrive in **dollars per kWh** and stay in dollars the whole way through.
Earlier versions of this integration held cents. Nothing converts now, and
`sensor.projected_cost` multiplies the published price by the projected energy
directly.

## Setting it up

Settings → Devices & Services → Abode HVAC Coordinator → Configure → House
configuration → Tariff, then pick the Abode Power Tariffs entry.

That is the entire tariff configuration in this integration. There is nothing
else to enter here and nothing to keep in step.
