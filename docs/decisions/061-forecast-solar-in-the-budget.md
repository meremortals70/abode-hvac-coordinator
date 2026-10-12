# DR-061: The power budget counts the day's forecast solar, from house-level forecast sources

| | |
|---|---|
| Status | Accepted; amends DR-039 |
| Since | 0.9.3 (2026-10-12) |
| Origin | Living room overnight review, 11/12 October 2026, finding 8. Instructions, 2026-10-12: "yes it is meant to draw from the solar forecast" and "all of these need to be selectable fields under the components (not room) configuration" |
| Related | DR-037, DR-039, DR-042, DR-044 |

## Decision

The power step offers every solar production forecast Home Assistant has, and
any number may be chosen. Nothing chosen means the ones the Energy dashboard
uses. Their hourly forecasts are summed and walked through the no-import window
hour by hour against the battery: the allowance is the highest steady draw that
keeps the battery above empty, with the battery unable to hold more than it has
room for. Where no forecast can be read, the budget is worked out as before,
from the solar being produced now.

## Why

At 06:35 the battery was at 20 % and the sun was rising, and the unit was
stopped. The budget could never credit sun still to come: the sustained-solar
figure only ever lowered the solar reading, and the weather input was a cloud
factor, not generation.

## Rejected

- **A per-room forecast setting.** Solar belongs to the house.
- **One forecast source only.** A house with panels facing two ways has two.
- **Read the forecast integrations' sensors.** Their entity names and units
  differ by integration. The energy platform call is the one the Energy
  dashboard uses and is the same for all of them.
- **Count all forecast solar.** Sun a full battery cannot store does not pay
  for a later hour.

## Consequences

`async_get_energy_platforms` and the energy manager are not documented public
APIs. A failure to read them raises a repair issue naming the source and falls
back to the old budget. The forecast is read every fifteen minutes.

## In the code

Checked against: 0.9.3. **Conforms.**

- `power.py:302` - `def forecast_slices` - the forecast as hourly slices of the window
- `power.py:351` - `def forecast_allowance_kw` - the battery walk
- `power.py:378` - `ceiling_kwh = start + max(headroom_kwh, 0.0)` - no more stored than there is room for
- `power.py:284` - `def energy_dashboard_solar_forecasts` - the default sources
- `coordinator.py:890` - `async def _async_refresh_solar_forecast` - read and summed through the energy platform
- `coordinator.py:3155` - `allowance_kw = forecast_allowance_kw(` - used in the budget
- `config_flow.py:917` - `vol.Optional(CONF_SOLAR_FORECAST_ENTRIES)` - the house-level choice
