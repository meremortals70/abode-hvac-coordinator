# DR-062: The power ceiling drops at once and rises only when the allowance is clear

| | |
|---|---|
| Status | Accepted; amends DR-037; amended by DR-067 in 0.9.4 |
| Since | 0.9.3 (2026-10-12) |
| Origin | Living room overnight review, 11/12 October 2026, finding 9 |
| Related | DR-016, DR-037 |

## Decision

A move to a less permissive ceiling happens the cycle it is needed. A move to a
more permissive one waits until the ceiling has stood for `BIN_RAISE_HOLD` (ten
minutes), and then goes only as far as bins whose draw the allowance clears by
`BIN_RAISE_MARGIN_KW` (0.3 kW).

## Why

Between 06:31 and 07:13 the setpoint flipped between 18 C and 24 C about twenty
times, because the allowance hovered at a bin's draw and the bin was worked out
afresh every evaluation.

## Rejected

- **A slower evaluation.** Every other decision would slow with it.
- **Hysteresis in both directions.** Holding a ceiling the budget cannot afford
  imports from the grid.
- **Smoothing the allowance.** It would lag a real drop in the same way it
  damps a false rise.

## Consequences

A room can sit one bin lower than the budget allows for up to ten minutes. Both
constants are judgement figures, listed in known limitations.

## In the code

Checked against: 0.9.7. **Conforms**, as amended by DR-067.

- `power.py:444` - `def held_ceiling_bin` - down at once, up when clear
- `power.py:437` - `BIN_RAISE_HOLD = timedelta(minutes=10)` - the hold
- `power.py:432` - `BIN_RAISE_MARGIN_KW = 0.3` - the margin
- `coordinator.py:3812` - `bin_index = held_ceiling_bin(` - used in the budget
