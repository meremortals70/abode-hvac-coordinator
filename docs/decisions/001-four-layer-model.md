# DR-001: Four layers, and Layers 2 and 3 only see a climate entity

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.5; in the code from 0.6.0 (2026-08-16) |
| Origin | Design v0.5 |
| Related | DR-002, DR-003, DR-005 |

## Decision

The system has four layers: read-only inputs (L0), a replaceable device
driver (L1), regulation (L2) and the coordinator (L3). L2 and L3 consume a
`climate` entity and nothing below it.

## Why

The indoor units are driven through an adapter that may be replaced. If the
coordinator depended on anything under the climate entity, it could not be
built or judged until the driver work finished. With the boundary at the
climate entity, neither waits for the other, and replacing a driver is a
per-room change L3 does not notice.

## Rejected

- **Talk to the vendor protocol directly.** Better data (per-head power,
  compressor frequency, error codes), but it ties the coordinator to one
  brand and one adapter. A better driver can still expose those through the
  climate entity later.

## Consequences

Anything a better driver adds (power, frequency, error codes) improves the
model but is never required. The coordinator only learns what the climate
entity reports, which is why `hvac_action` matters so much (DR-025).

## In the code

Checked against: 0.8.12 (`50e6abf`). **Conforms.** Every command goes through Home Assistant climate and cover
services.

- `actuator.py:270` - `_async_command_head` - climate service calls only
- `coordinator.py:2097` - `_capabilities` - what the unit can do is read from the entity
