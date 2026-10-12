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
| `no_grid_import` | a per-room power budget from the battery, the sun and the solar forecast — see below |
| `grid_charge_battery` | recognised, and deliberately ignored — batteries are not this controller's actuator |

Anything else is carried through into the decision trace and reported as a
repair issue naming it. That is not a gap. A constraint this controller does
not act on is a constraint some other system owns, and adding one to your plan
needs no code change here.

### `no_grid_import`: what it actually does

This controller has no control over where household power comes from. It never
writes to a battery or an inverter; it only reads. What it controls is how hard
each room's air conditioner works, and, for a room set to do so, whether it
runs at all.

**It is optional, and it is set per room.** The house needs five readings,
configured once under Settings → Devices & Services → Abode HVAC Coordinator →
Configure → House configuration → Power-aware operation: battery state of charge, battery
capacity, solar output, house load and a reserve margin. Leave any one unset
and the constraint is still read and shown, but nothing acts on it. Each room
then has its own Power management setting:

| Setting | What happens in a no-import window |
|---|---|
| Off (the default) | Nothing. The room holds its band as if the constraint were not there |
| Guidance | The budget is worked out and shown on the room's trace. It is never applied, and the unit is never turned off for power |
| Enforced | The budget is applied. Where even holding the room cannot be afforded, the unit is turned off until it can be (DR-063) |

**The budget.** The battery's energy above the reserve margin has to last
until the window ends. Where a solar production forecast is read (DR-061), the
forecast sun is counted hour by hour until then, and the allowance is the
highest steady draw for this room that keeps the battery above the reserve the
whole way, with the battery unable to store more than it has room for. Without
a forecast, the solar being produced now is counted, cut back by the cloud the
weather forecast expects later in the window (DR-039). The rest of the house's
draw is taken off first. Where the battery's maximum discharge power is set,
the allowance never exceeds what the battery can deliver on top of the sun.

**What the budget buys.** Each room's air conditioner has a learned draw at
four operating points, from holding at setpoint to pulling the room down
(finding 14). Until it has learned them it assumes 1.2 kW. The most
demanding operating point the allowance covers sets a ceiling on how far from
the room's own temperature the setpoint may be asked to go: 0.5 C, 1.5 C,
3.0 C or no limit. Under Enforced, the room loop's setpoint is held inside that
ceiling, and rounded towards the room rather than away from it so it stays
inside (DR-051). If the allowance covers no operating point at all, Enforced
turns the unit off.

**It does not flap.** The ceiling drops the moment the allowance falls. It
rises only after it has stood for ten minutes, and only to an operating point
the allowance covers with 0.3 kW to spare (DR-062). Outside a no-import window
the held ceiling is let go.

**What is never rationed.** Precool runs in a window chosen because energy is
free and is never throttled (DR-037). A coasting room the budget can afford is
left on its fan or drying; under Enforced it is turned off only when nothing
can be afforded.

**What you see.** The room's mode sensor carries `power_budget_kw`,
`power_bin` and `power_ceiling_c`, and its reasons say what the budget is,
how much forecast solar it counted, and what it did. Where a grid sensor is
configured, any import that happens in a window anyway is measured and reported
afterwards as a repair issue (DR-043).

### It fails open

**A budget the controller cannot work out never holds a room back** (DR-044).

| Case | Result |
|---|---|
| Any of the five power fields not configured | Power management does not run |
| The battery charge reading is missing, or older than fifteen minutes | No ceiling; the room runs as normal |
| The tariff series does not reach the end of the no-import window | No ceiling; the room runs as normal |
| A solar production forecast cannot be read | The budget uses the solar being produced now, and a repair issue names the forecast |

A missing solar or house-load reading is not a missing budget. Solar counts as
none and the house's other load as nothing, and the budget is worked out from
the battery. That is the cautious reading of solar and the generous reading of
the house, and a sensor that has failed is logged as unavailable.

Before 0.8.6 an unconverged thermal model also refused the compressor, which
stopped occupied rooms for whole windows on a fresh install. The budget has not
used the thermal model since 0.8.10: it uses the learned draw, which falls back
to the assumed figure until it is known.

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
