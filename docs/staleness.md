# Stale feeds

Home Assistant tells you when an entity is `unavailable`. It does not tell you
when an entity is reporting a number from four hours ago.

## The failure this catches

A battery-powered Zigbee sensor drops off the mesh. It does not go
`unavailable` — it keeps its last state indefinitely. `hass.states.get` returns
23.4. The coordinator reads 23.4. The comfort index is computed from 23.4. The
room is regulated against a number from before lunch, and Layer 2 patiently
integrates against an error that stopped being real hours ago.

Nothing anywhere reports a fault, because from Home Assistant's point of view
nothing is faulty.

## What happens instead

Every reading this controller acts on carries an age. A reading older than its
feed's tolerance is treated as **absent**, not as a value.

Absent is already handled everywhere in this project: the room holds, the trace
says why, the comfort index is not computed, and no actuator moves on a guess.
Layer 2 stops integrating, because its anti-windup gate needs a target and a
room reading and now has neither.

## Tolerances

One number per feed class, not per entity. A tolerance is a property of what
the feed measures and how it reports, not of the particular device — making it
a setting would mean asking a question nobody can answer.

| Feed | Tolerance | Why |
|---|---|---|
| Indoor temperature, humidity, illuminance | 2 hours | most report on change with a heartbeat well inside an hour |
| Outdoor temperature and humidity | 3 hours | often a weather integration on a fifteen- or thirty-minute poll; has to clear a couple of misses |
| Presence | 6 hours | an mmWave sensor in a room nobody enters legitimately says nothing for a long time |
| Openings and covers | 26 hours | contact sensors are quiet by nature but heartbeat daily |

They are generous on purpose. This catches a feed that has died, not one that
reports slowly.

Two cases are deliberately treated as fresh: a reading with no timestamp, which
means a test fixture rather than a real entity, and a timestamp in the future,
which is clock skew between a device and the host and not a fault worth taking
a room offline for.

## Reading it

`sensor.stale_feeds` counts them house-wide and lists them in its `feeds`
attribute, naming the entity, its age and its tolerance. Each room's decision
trace carries its own `stale_feeds` list, so a room that is holding tells you
which sensor caused it.

A stale feed is also logged once on the transition, not on every evaluation.
