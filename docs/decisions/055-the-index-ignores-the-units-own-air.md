# DR-055: The comfort index is never changed by what the component itself is doing

| | |
|---|---|
| Status | Accepted; amends DR-010 (the still-air correction no longer follows the unit's own state) |
| Since | 0.9.1 (2026-10-04) |
| Origin | Incident, Office, 2026-10-04, from the Home Assistant recorder. Instruction: "the component cannot artificially change the hci" |
| Related | DR-006, DR-010, DR-022, DR-057 |

## Decision

The still-air correction to the comfort index follows only an air-movement entity
the user configured, such as a ceiling fan. With none configured, air is assumed
still. The air conditioner's own state, running or not, in any mode, never
changes the index.

## Why

With no air-movement entity configured, 0.9.0 and earlier decided whether the
room's air was moving from the unit itself: any mode but off counted as moving
air. Still air adds 1.0 to the index. So the index fell by a full point the moment
the unit started and rose by a full point the moment it stopped.

On 2026-10-04 the Office showed it in the recorder. At 13:06:37 and 13:06:47 the
room sensor read 23.12 both times, and the index went from 25.29 to 24.29. At
12:19:45 the unit was running and the index read 23.23, below the 24.0 floor, so
the room demanded heat and coasted, and the unit was commanded off. Ten seconds
later the unit was off and the index read 24.23, in band. The room then warmed
until the index passed the top, the fan and compressor started, the index dropped
by a point again, and it repeated. The three stops at 12:19, 12:56 and 13:33 are
37 minutes 6 seconds apart. The sensor is over a metre to the side of the unit and
deliberately out of its airflow, so the unit's wind was never reaching the seat
the index describes. The user had narrowed the band to stop the room swinging from
too cold to too hot, which made a point of swing most of the band.

## Rejected

- **Keep the unit's state as the fallback.** It is the defect: the controller's own
  action changes the number it is deciding from.
- **Assume moving air when nothing is configured.** The index would read a point
  low whenever the unit is off, and the room would be left hotter than its band.
  Comfort is the constraint, so the fallback fails toward still air.
- **Smooth the index over time.** It hides a feedback loop instead of removing it,
  and delays every real change as well.
- **Widen the band.** The user narrowed it deliberately, and the loop would still
  be there.

## Consequences

The index is a measurement of the room taken where the sensor is, and the
component cannot move it by acting. The fan step keeps its reasoning that air
movement should carry a room that is marginally above its band, but it now
decides on a stable index and no longer changes it. A room whose sensor does sit
in the unit's airflow can configure that fan as its air-movement entity. The
covers still change the index through the radiant correction, because a blind
changes the heat reaching the room; that is physical, not a reading of the unit.

## In the code

Checked against: 0.9.1. **Conforms.**

- `coordinator.py:1781` - `def _air_moving` - only a configured entity counts; otherwise still
- `hci.py:108` - `STILL_AIR_HCI` - the correction, 1.0
