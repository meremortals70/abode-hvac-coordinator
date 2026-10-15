# DR-072: A unit bringing a room down is not put on its fan short of the band

| | |
|---|---|
| Status | Accepted; amends DR-022 |
| Since | 0.9.6 (2026-10-14) |
| Origin | Living room, night of 13–14 October 2026. Instruction, 2026-10-14: "i want you to roll everything into 0.9.6" |
| Related | DR-016, DR-022, DR-059 |

## Decision

Where the room's unit is already cooling, drying or heating and the room is still outside its band, the fan step is skipped and the unit carries on. The fan is chosen only to start a room that is marginally out of band.

## Why

From midnight to 05:56 the living room went round about ten times: drying brought the comfort index to within 0.5 of the band top, the ladder chose the fan as cheaper, the compressor stopped, the five-minute minimum off held it while the room climbed back to about 25.5, and drying started again. The room was above its Sleep band all night and never reached it.

## Rejected

- **A smaller fan margin.** It moves the point the pull-down is abandoned; it is still abandoned short of the band.
- **Lengthen the minimum off time.** It makes the climb back worse.

## Consequences

A unit started for a room keeps working until the room is inside its band, then the room loop holds it there (DR-056). A room that would have been carried by air movement from the edge of its band after a long run is now finished by the compressor.

## In the code

Checked against: 0.9.7. **Conforms.**

- `modes.py:270` - `WORKING_MODES = frozenset` - the modes that count as working the room
- `modes.py:363` - `elif inputs.unit_mode in WORKING_MODES:` - the fan step skipped
- `coordinator.py:2662` - `def _unit_mode` - the unit's mode, read each evaluation
