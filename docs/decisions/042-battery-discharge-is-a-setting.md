# DR-042: The battery's maximum discharge is a setup figure, not learned

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.10 (2026-08-23) |
| Origin | Finding 10 build |
| Related | DR-037 |

## Decision

The battery's rated maximum discharge power is entered once at setup and
bounds the power allowance.

## Why

It is a nameplate specification. It does not vary and does not need tracking.

## Rejected

- **Learn the discharge rate from readings.** Built once during the 0.8.10
  work and withdrawn: it measures something that is already known.
- **Assume it is unbounded.** Over-credits a battery that cannot deliver.

## Consequences

One more field on the power step. Without it, the allowance is bounded by
energy alone.

## In the code

Checked against: 0.9.1. **Conforms.**

- `config_flow.py:817` - `CONF_BATTERY_MAX_DISCHARGE_KW` - the field
- `power.py:115-118` - `max_discharge_kw` - bounds the allowance
