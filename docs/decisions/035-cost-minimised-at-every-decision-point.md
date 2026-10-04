# DR-035: Every decision point looks for the cheapest way to deliver the band

| | |
|---|---|
| Status | Accepted |
| Since | 0.8.11 (2026-08-24) |
| Origin | Findings 12 and 19b |
| Related | DR-008, DR-032, DR-033 |

## Decision

Price is not confined to precool. A room nearing the edge of its band coasts
instead of running when a strictly cheaper tariff interval starts within the
hour and the model says the band holds until then. It fails toward comfort:
no converged model, no price, or a window that forbids coasting, and the room
is corrected now.

## Why

Until 0.8.11 every price was fetched and parsed and used by no decision
(finding 19b), and every decision except precool was greedy per cycle
(finding 12).

## Rejected

- **A separate cost mechanism.** Folded into the existing coast test instead,
  with its own trace reason.

## Consequences

The horizon is the same one hour the coast test trusts, never further.

## In the code

Checked against: 0.9.1. **Conforms.**

- `tariff.py:294` - `cheaper_interval_ahead` - the next cheaper interval
- `coordinator.py:1477` - `_cheaper_window_imminent` - holds until then
- `modes.py:113-127` - `cheaper_window_imminent` - gated on coasting being permitted
