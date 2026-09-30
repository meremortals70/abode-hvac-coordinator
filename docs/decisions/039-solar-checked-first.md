# DR-039: Solar pays down the house first; the battery binds only on the shortfall

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.11 (2026-08-24) |
| Origin | Finding 18 |
| Related | DR-037 |

## Decision

In the power budget, solar first covers the rest of the house. Whatever solar
is left goes to the room directly, free of the battery's energy and discharge
limits. Only the remainder draws on the battery. The solar credit is derated
by the weather forecast's worst clear-sky fraction across the rest of the
window.

## Why

The 0.8.10 rewrite dropped solar from the calculation entirely. That was a gap,
not a choice: the veto it replaced had checked solar directly. Assuming the
current solar holds for the whole window over-credits a window that runs past
sunset or into forecast cloud.

## Rejected

- **Instantaneous solar for the whole window.** Over-credits.

## Consequences

Without a weather entity, the instantaneous reading is used unchanged.

## In the code

Checked against: 0.8.12 (`50e6abf`). **Conforms.**

- `power.py:135` - `solar_offset_kw` - the split
- `coordinator.py:2267` - `_sustained_solar_kw` - the derating
