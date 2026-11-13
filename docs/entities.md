# Entities

Entity ids below are for a room named Office; yours follow your room's name.

## Each room

| Entity | Example id | What it shows or does |
| --- | --- | --- |
| Mode | `sensor.office` | The band rule in force: Locked out, Automatic control off, Left, Unoccupied, Precool, Sleep or Occupied — or the grace the room is waiting in: Occupied grace, Empty grace or Opening grace. Its attributes are the full decision trace (below) |
| Comfort index | `sensor.office_comfort_index` | The room's comfort index now. Attributes: band low, band high, and whether the room is below, in or above the band |
| Target temperature | `sensor.office_target_temperature` | The centre of the band in force, as a temperature at the room's humidity now. Diagnostic |
| Commanded setpoint | `sensor.office_commanded_setpoint` | The setpoint last sent (for a unit that takes only a range, the middle of the range sent); attribute: the setpoint wanted before whole-degree stepping. Diagnostic, disabled by default |
| Dew point | `sensor.office_dew_point` | The room's dew point. Attributes: outdoor dew point, outdoor apparent temperature, free cooling advised, condensation risk |
| Settings | `sensor.office_settings` | The number of air conditioners; attributes: every setting of the room (unset ones say so) and what the units can do. Diagnostic, disabled by default |
| Controller | `sensor.office_controller` | Plan and PID, or the learned controller; attributes: the plan in force and the arbiter's record. Diagnostic |
| Learning | `sensor.office_learning` | The last week's average error of the model's one-hour forecast, °C. Falls as the model learns. Diagnostic |
| Comfort in band | `sensor.office_comfort_in_band` | Yesterday's share of occupied time inside the band, %. Diagnostic |
| Near the centre | `sensor.office_near_the_centre` | Yesterday's share of occupied time within 0.3 of the band's centre, %. Diagnostic |
| Starts per running hour | `sensor.office_starts_per_running_hour` | Yesterday's compressor starts per hour run. Diagnostic, disabled by default |
| Cost of comfort | `sensor.office_cost_of_comfort` | Yesterday's cost of the energy the room used, in your currency. Diagnostic |
| Automatic control | `switch.office_automatic_control` | On: the component runs the room. Off: the unit is sent off once, then the room's controls are yours |
| Automatic vane control | `switch.office_automatic_vane_control` | On: the component sets the vertical vane. Off: the vanes are yours. Configuration |
| Comfort mode | `select.office_comfort_mode` | Schedule (follow the sleep schedule), Sleep or Awake. Stays until you change it |
| Unit mode | `select.office_unit_mode` | The unit's mode now. Writable only while Automatic control is off |
| Fan speed | `select.office_fan_speed` | The unit's fan speed now. Writable only while Automatic control is off. Only if the unit has fan speeds |
| Vertical vane | `select.office_vertical_vane` | Writable while Automatic vane control is off. Only if the unit has vane positions |
| Horizontal vane | `select.office_horizontal_vane` | Writable while Automatic vane control is off. Only if the unit has horizontal vane positions |
| Setpoint | `number.office_setpoint` | The unit's setpoint now, in the unit's range and step. Writable only while Automatic control is off |
| Leave | `button.office_leave` | Turns the room off at once. With a presence sensor, until someone comes back; without one, until you change Automatic control or Comfort mode (Precool needs a presence sensor) |
| Precool | `button.office_precool` | Starts Precool now. Only in rooms with a presence sensor |

The unit controls show the unit's live value as soon as it changes. Changing one while the component runs the room is refused with a message saying so.

### The decision trace

The mode sensor's attributes say what the room is doing and why, every 30 seconds:

| Attribute | What it holds |
| --- | --- |
| `room_id` | The room's id, for the `precool` and `cancel_precool` actions |
| `action` | What the unit is told: off, none (left as it is), rest, dry, cool or heat |
| `band_rule`, `band_reason`, `band` | The band in force, why, its low, high, centre (index and °C) and where the room sits in it |
| `comfort_index`, `occupied`, `sleeping`, `waking`, `precool`, `automatic_control` | The room now |
| `in_charge`, `control_reason` | Which controller is in charge, and its reason |
| `plan` | The plan in force: each half-hour's action and rate, the forecast index, minutes outside the band, its costs, and the first actions not taken with the reason |
| `pid` | The PID's terms and gains |
| `model` | What the model has learned: figures, their uncertainty, the sensor's noise, the forecast error, trust, the unit's delay and lag, and why the last step did not learn, if it did not |
| `power` | The power budget: allowance, ceiling ("no limit" where none applies) and reason |
| `delivery` | The setpoints wanted, free-cooling advice, condensation risk and reasons |
| `limits` | Every rule that changed the step, constraints in force, missing readings and reasons |
| `compressors` | Each head's compressor state, and which source said so |
| `stale` | Feeds too old to use |

`plan`, `pid`, `model`, `power`, `delivery`, `limits`, `compressors` and `stale` are not kept in the recorder's history, because they change every 30 seconds. `decision` is kept: each plan's first step, its actions, its forecast index and uncertainty every half-hour, and why its first step could not be a kind it was barred from (for example "the unit stopped less than its minimum rest ago: 3 min left"), so a night can be read back.

## The house

Under the device **Abode HVAC Coordinator**:

| Entity | Example id | What it shows |
| --- | --- | --- |
| Demand forecast | `sensor.abode_hvac_coordinator_demand_forecast` | kWh the rooms' plans will use until the next tariff change. Attributes: `until`, `to_tariff_change` (false when the plans end first) and the kWh in each tariff interval |
| Tariff rate now | `sensor.abode_hvac_coordinator_tariff_rate_now` | The price now, per kWh; attributes: the rate's name and the intervals read |
| Constraints in force | `sensor.abode_hvac_coordinator_constraints_in_force` | How many tariff rules are in force now, with the list |
| Projected cost | `sensor.abode_hvac_coordinator_projected_cost` | What the demand forecast is priced at |
| Outdoor temperature | `sensor.abode_hvac_coordinator_outdoor_temperature` | The outdoor reading in use |
| Outdoor apparent temperature | `sensor.abode_hvac_coordinator_outdoor_apparent_temperature` | How outdoors feels, with humidity and wind |
| Outdoor dew point | `sensor.abode_hvac_coordinator_outdoor_dew_point` | |
| Forecast peak | `sensor.abode_hvac_coordinator_forecast_peak` | The highest temperature in the hourly forecast |
| Stale feeds | `sensor.abode_hvac_coordinator_stale_feeds` | How many feeds are too old to use, with the list. Diagnostic |
| Rooms configured | `sensor.abode_hvac_coordinator_rooms_configured` | Diagnostic |
