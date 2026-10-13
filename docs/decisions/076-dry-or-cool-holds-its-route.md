# DR-076: Dry or cool holds its route, and an unconverged room well above its band is cooled

| | |
|---|---|
| Status | Accepted; amends DR-027 and DR-029 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Living room, 13 October 2026, 21:05 and 23:51–00:02. Instruction, 2026-10-14: "i want you to roll everything into 0.9.6" |
| Related | DR-026, DR-027, DR-029, DR-059 |

## Decision

Once the unit is drying, the 65% fallback line moves down three points and the learned comparison asks 15% more of cooling; once it is cooling, the line moves up three points and drying needs 15% more. While the model has not converged, a room more than 2.0 HCI above its band is cooled whatever the humidity.

## Why

With the room at 64–65% the unit changed between cool and dry six times in eleven minutes. At 21:05 the room, seven points above its band at 73%, was put on dry by the fallback line.

## Rejected

- **A wider single line.** It only moves where the flipping happens.
- **Drop the fallback.** Until the model converges it is what there is.

## Consequences

The figures are judgements. A room near the line stays on the route it is on a little longer than the line alone would keep it.

## In the code

Checked against: 0.9.6. **Conforms.**

- `modes.py:314` - `DRY_FALLBACK_HYSTERESIS_RH = 3.0` - the hysteresis
- `modes.py:402` - `if overshoot > DRY_FALLBACK_MAX_OVERSHOOT_HCI:` - too far above band to dry on the fallback
- `modes.py:410` - `threshold -= DRY_FALLBACK_HYSTERESIS_RH` - the line moves
- `modes.py:453` - `advantage *= DRY_ROUTE_HYSTERESIS` - and the learned comparison
