# DR-092: The evaluation that sees a band change decides knowing it

| | |
|---|---|
| Status | Accepted; amends DR-084 and DR-085 |
| Since | 0.9.7 (2026-10-15) |
| Origin | Root-and-branch review, 2026-10-15, finding F3. Instruction, 2026-10-15: "go ahead and then review again" |
| Related | DR-084, DR-085 |

## Decision

When an evaluation changes a room's band, the room is evaluated again with the change, the ramp and waking already known. Waking ends only once the ramp is over.

## Why

The change was recorded after the decision. The first evaluation after Sleep ended used the daytime band in full, found the room below it, and heated: the 05:12 heat on 15 October. Waking also ended inside the ramping band, so the room was heated as the ramp passed it.

## Rejected

- **Work out the band change before the first evaluation.** The band comes from the evaluation; deciding twice is simpler than deciding it twice in two places.

## Consequences

An evaluation that changes band costs two evaluations of that room.

## In the code

Checked against: 0.9.7. **Conforms.**

- `coordinator.py:1406` - `if self._note_band_change(room.room_id, trace, now):` - decided again knowing the change
- `coordinator.py:1856` - `def _note_band_change` - the change recorded
- `coordinator.py:1906` - `ramp_over = changed_at is None or now - changed_at >= SLEEP_RAMP` - waking ends after the ramp
