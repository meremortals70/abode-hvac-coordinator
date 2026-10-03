# DR-004: The tariff lives in Abode Power Tariffs; this integration only reads it

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.8; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.8, replacing the v0.5 in-house tariff |
| Related | DR-034, DR-035 |

## Decision

Periods, prices and constraints belong to the Abode Power Tariffs
integration. This integration reads a forward interval series from it through
one service call and holds none of it. Prices stay in the publisher's unit,
dollars per kWh, unconverted.

## Why

At v0.5 the tariff (windows, prices, feed-in, supply charge) was configured
here. A tariff is shared by everything in the house that cares about price;
holding a copy here means two sources of truth that drift.

## Rejected

- **Keep the tariff here (v0.5).** Superseded for the reason above.
- **Convert to cents.** A conversion is a place for a unit error to hide.

## Consequences

The integration runs without a tariff (no cost features, comfort unchanged),
and a failed or stale fetch raises a repair issue rather than blocking.

## In the code

Checked against: 0.9.0. **Conforms.**

- `coordinator.py:603` - `_async_refresh_tariff` - the single read path
- `tariff.py:166` - `TariffSeries` - the forward series, parsed not stored
