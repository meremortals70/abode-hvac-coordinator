# DR-033: Precool ignores present occupancy and runs on the weather forecast

| | |
|---|---|
| Status | Accepted |
| Since | Occupancy rule from 0.6.0 (2026-08-16); forecast in 0.8.0 (2026-08-17) |
| Origin | Design v0.4 and v0.8 |
| Related | DR-012, DR-034, DR-037, DR-041 |

## Decision

Precool runs when the tariff declares a precool window and a cooling load is
forecast ahead, whether or not anyone is in the room. It drives to the bottom
of the precool band, banking thermal mass. The load ahead is judged from the
forecast peak over the next ten hours; without a forecast, from the current
outdoor reading.

## Why

The cheap window is typically the middle of the day, when rooms are empty, and
the load it banks against arrives in the evening. Gating on occupancy would
stop precool in exactly the window it exists for. The forecast replaced a
comparison of current outdoor against indoor temperature, which looked at the
wrong moment.

## Rejected

- **Gate on occupancy.** Stops precool when it is useful.
- **Current outdoor versus indoor (v0.5).** Compares now, not the load ahead.

## Consequences

Precool depends on the tariff declaring the window. Precool runs in a window
chosen because energy is free, so the power ceiling does not ration it
(DR-037).

## In the code

Checked against: 0.9.0. **Conforms** to this record.

- `modes.py:88-99` - `precool_opportunity` - not gated on occupancy
- `weather.py:42` - `DEMAND_LOOKAHEAD` - ten hours
- `weather.py:293` - `demand_ahead` - forecast load
- `coordinator.py:1567` - `PRECOOL_DEMAND_MARGIN_C` - fallback without a forecast
- `coordinator.py:2179` - `CONSTRAINT_PRECOOL_OPPORTUNITY` - the tariff declares the window
