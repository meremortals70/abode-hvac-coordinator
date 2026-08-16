# Entities

Each room is a **device**. Three entities sit under it.

## `sensor.<room>_mode`

The current mode, and the whole reason behind it.

- **State:** one of `lockout`, `unoccupied`, `occupied`, `sleep`,
  `precondition`, `precool`, `coast`
- **Device class:** enum
- **Always available** once the room has been evaluated

Attributes:

| Attribute | Meaning |
|---|---|
| `room_id` | The room's id, for use in actions |
| `evaluated_at` | When this decision was made |
| `mode` | Same as the state |
| `base_mode` | The mode coast displaced, when coasting |
| `hci` | Comfort index at evaluation time |
| `band_low`, `band_high` | The band in force |
| `band_position` | `below`, `within` or `above` |
| `target_dry_bulb_c` | What the air conditioner would be asked for |
| `demand` | `cool`, `heat`, or none |
| `actuator` | `none`, `covers`, `fan`, `dry` or `compressor` |
| `reasons` | Why it is doing this |
| `rejected` | What was considered and ruled out, with why |
| `model` | The room's learned coefficients, their variance, sample count and whether each has converged |
| `hci_air_only` | The index before the radiant, still-air and heat-load corrections |
| `radiant_fraction` | How much solar load is reaching the room, 0 to 1 |

**`reasons` and `rejected` are the point of this integration.** Between them
they explain every decision, including which cheaper actuators were skipped and
on what grounds.

## `sensor.<room>_comfort_index`

- **State:** the comfort index, one decimal
- **Unit:** `HCI` — deliberately not `°C`
- **State class:** measurement, so it is recorded and graphable
- **Unavailable** when the temperature or humidity sensor has no reading

Attributes: `band_low`, `band_high`, `band_position`.

## `sensor.<room>_target_dry_bulb`

The dry bulb setpoint derived from the band and the measured humidity.

- **State:** temperature in °C
- **Device class:** temperature
- **Category:** diagnostic
- **Unavailable** when there is no band or no humidity reading

This is what the air conditioner would be asked for. Watching it against the
comfort index is the clearest way to see the humidity correction working: the
same band produces a lower setpoint on a humid night.

## `sensor.<room>_commanded_setpoint`

What was actually sent to the unit, which is not the same as the solved target.
Layer 2 trims the setpoint until the **room** sensor reaches the target, because
the unit regulates against its own return-air thermistor and that is not where
anyone sits.

Its attributes carry `solved_target_c` and `regulation_trim_c`, so the
difference is visible rather than silent. A trim settling at −1.8 means Layer 2
has found a real 1.8 °C offset between your sensor and the unit's, and is
correcting it every cycle. See [Regulation](regulation.md).

## `sensor.<room>_dew_point`

The room's dew point. Its attributes carry the outdoor apparent temperature, the outdoor dew point,
`free_cooling_advised` and `condensation_risk`.

Dew point is per room because condensation is per room: an ensuite and a
closed-up bedroom sweat at different setpoints. It is not a comfort metric —
comfort is the index, which already carries humidity.

Free cooling is an advisory, not an action: this controller owns the air
conditioner and the covers, not your windows. See
[Free cooling](free-cooling.md).

## `sensor.abode_hvac_coordinator_demand_forecast`

One per installation, not per room. Projected HVAC energy over the next eight
hours, in kWh, with a per-window and per-room breakdown in its attributes.

This is the published contract with whatever owns the battery. **It carries no
vendor concepts.** See [Demand forecast](demand-forecast.md).

## The coordinator device

Everything house-wide appears as a sensor on a single **HVAC Coordinator**
device, so a setting you entered once is visible without reopening the form
that set it.

| Sensor | Shows |
|---|---|
| Demand forecast | Projected kWh over the horizon, with per-window and per-room breakdown |
| Tariff rate | The rate label in force now; the collapsed periods, the source entry, when the series was fetched and how far it reaches, in its attributes |
| Active constraints | Which constraints apply in the current interval |
| Projected cost | The forecast energy priced per period, in dollars |
| Outdoor temperature | The configured outdoor feed |
| Outdoor apparent temperature | What outdoors feels like, on the comfort index scale, with wind |
| Outdoor dew point | Computed from the outdoor feeds, where both are configured |
| Forecast peak | The hottest hour the forecast can see, with when it falls |
| Stale feeds | How many feeds answered with a reading too old to act on, and which |
| Rooms configured | How many, and which |

Prices, feed-in rates, the daily supply charge and the per-window price sensors
are **not here**. They belong to Abode Power Tariffs, which publishes them
itself. Two copies of a price is one copy that goes stale.

## Per-room settings sensor

Each room device carries a **Settings** sensor. Its state is a one-line summary;
its attributes are that room's entire configuration, with anything unset stated
as "Nothing selected" rather than left blank.

It exists so a configuration can be read without opening the form that set it.

## Repair issues

| Issue | Raised when |
|---|---|
| Rooms with no comfort bands | A room has no bands and no lockout reason, so it can never actuate |
| Tariff constraints not acted on | A declared constraint is for another system to consume |
| The tariff could not be read | Abode Power Tariffs did not return a plan. The last series is kept and used until a fetch succeeds |
| The weather forecast could not be read | Precool falls back to current conditions. The last forecast is kept |

Neither is an error. Both exist so that a room quietly doing nothing is visible
rather than mysterious.

## Diagnostics

**Device page → three-dot menu → Download diagnostics.**

Contains every room's configuration, which tariff entry is being read and when
its series was last fetched, unrecognised constraints, the learned thermal
model, and the current decision trace for every room. Entity IDs are included: they are how
your configuration is identified, and a diagnostics download without them cannot
explain a wrong decision.
