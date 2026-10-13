# DR-059: When the compressor stops, a humid room dries and a dry room stays on its fan

| | |
|---|---|
| Status | Accepted; amends DR-057 |
| Since | 0.9.3 (2026-10-12) |
| Origin | Living room overnight review, 11/12 October 2026, findings 5, 6 and 13. Instructions, 2026-10-12: "with high humidity it should have gone to dry not fan" and "if humidity is low any coasting should be fan only unless constrained by power" |
| Related | DR-016, DR-022, DR-029, DR-053, DR-057, DR-060, DR-063 |

## Decision

A coasting room whose humidity calls for drying is put in dry mode, judged by the
same test the dry-versus-cool choice already uses (DR-029). Otherwise it coasts
on its fan, as DR-057 says, and off only where the unit has no fan-only mode or
power management is allowed to stop it (DR-063). A restart refused by the
short-cycle guard leaves the unit on its fan, not off.

## Why

On the night of 11 October the living room coasted on its fan about twenty
times. Each time the room's humidity rose four to six points in the fan phase,
as water came back off the coil, and the comfort index left the band. The two
off commands at 06:35 and 06:52 were the short-cycle guard refusing a restart:
a refused start was commanded off, which turned a coasting unit off in the
middle of its fan phase.

## Rejected

- **Always coast on the fan (DR-057 unchanged).** Blowing room air over a wet
  coil puts the water back into the room.
- **Always coast off.** For a dry room, the user's instruction is fan only.
- **A fixed humidity threshold for the coast.** The dry-versus-cool test already
  learns the room's drying rate, and two thresholds would disagree.
- **Command nothing on a refused restart.** A unit moved into a running mode by
  something else would be left running.

## Consequences

Dry mode runs the compressor at low capacity, so a humid coast draws more power
than a fan coast. It is still a coast to the power budget and to the stop
tracking. The humidity judged is the humidity after the coil's rebound where
that has been learned (DR-060).

## In the code

Checked against: 0.9.5. **Conforms.**

- `modes.py:636` - `def coast_step` - dry where the room's humidity calls for it, otherwise fan, otherwise off
- `modes.py:677` - `route = _latent_route(judged, overshoot, scratch)` - the same test as dry versus cool
- `modes.py:671` - `inputs.rebound_relative_humidity > inputs.relative_humidity` - judged after the rebound
- `coordinator.py:1662` - `if self._capabilities(room)["can_fan_only"]` - a refused restart stays on the fan
