# DR-010: The comfort index is Steadman shaded apparent temperature, wind zero

| | |
|---|---|
| Status | Accepted |
| Since | Design v0.4 |
| Origin | Design v0.4, replacing the index in use at v0.3 |
| Related | DR-009, DR-026 |

## Decision

The index is `HCI = Ta + 0.33 * e - 4.00`, Steadman's shaded apparent
temperature with the wind term set to zero, where `e` is vapour pressure.
Radiant load (sun on glass), still air and equipment heat are added as
separate terms. The outdoor figure uses the same formula with wind, so the two
can be compared.

## Why

The index actually in use at v0.3 fell as humidity rose. That is backwards:
humid air at a fixed temperature is less comfortable, because sweat evaporates
less readily. A controller on an inverted index concludes a muggy room is fine
and does nothing on exactly the night it should be dehumidifying.

## Rejected

- **The v0.3 index.** Inverted with respect to humidity.
- **Heat index or humidex.** Covered in `docs/comfort-index.md`.

## Consequences

Bands calibrated against the old index are not transferable. The index is one
opinion; `docs/known-limitations.md` says so.

## In the code

Checked against: 0.8.12 (`50e6abf`). **Conforms.**

- `hci.py:162` - `comfort_index` - the indoor index
- `hci.py:215` - `apparent_temperature` - the outdoor form, with wind
