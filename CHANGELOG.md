# Changelog

Every build that ships gets an entry here, newest first. Entries say what
changed for someone running the integration, and name the decision record
(`docs/decisions/`) where one applies.

Headings used: **Fixed**, **Changed**, **Added**, **Removed**.

## 0.8.14 - 2026-10-01

### Added

- **An Automatic control switch for every room.** On is normal. Turn it off
  and the room's air conditioning is switched off and the integration will not
  start it again until you turn the switch back on. It takes effect at once,
  even if the unit started only a moment ago, and it survives a restart of
  Home Assistant. The room shows as Lockout, with "Automatic control switched
  off" as the reason. Turning it back on does not restart the unit until it has
  been off for five minutes. (DR-047, which amends DR-007)
- Tests: six for the switch, and 27 for the decisions moved below.

### Changed

- **Four decisions moved out of the coordinator into the modules that hold
  decisions, with no change in behaviour:** the power allowance and the
  setpoint clamp under the power ceiling (`power.py`), the short-cycle
  arbitration between rooms on one outdoor unit (`regulate.py`), and the
  tariff half of the cheaper-window test (`tariff.py`). DR-002 now reads
  Conforms. (DR-002)
- Decision records: DR-047 added; all 48 re-checked against this build.

## 0.8.13 - 2026-10-01

### Fixed

- **Precool is no longer throttled by power management.** A room set to
  enforced that was precooling during a no-import window was held to the
  power ceiling. Precool runs in a window chosen because energy is free, so it
  is now exempt, and the room's trace says "power budget: precool is not
  rationed". (DR-037)
- **Grid import during a no-import window was overstated.** Each evaluation
  added a fixed 30 seconds of import, but evaluations also run whenever a
  watched sensor changes, so the same half-minute was counted several times
  over. Import is now weighted by the time since the previous reading, and a
  gap longer than 30 seconds counts as 30 seconds. (DR-043)
- **A no-import window was judged every half hour instead of as a whole.**
  The tariff arrives in 30-minute slices and each slice was treated as its own
  window, so the warning compared each half hour against the 0.05 kWh floor
  and reported only the last one. A window now runs from the first slice
  carrying the constraint to the last. (DR-043)
- **The shortfall warning never cleared.** It now clears when a later
  no-import window closes without importing. (DR-043)

### Changed

- **Shortfall warning text** now describes the three power management
  settings (off, guidance, enforced) and says when the warning clears.
  (DR-041, DR-043)

### Added

- **Decision records** in `docs/decisions/`: 47 records of the decisions behind
  the integration, with the options that lost, where each lives in the code,
  and whether this build matches it. `check_anchors.py` checks the code
  references.
- **This changelog.**
- Tests: precool exemption, and four for grid import measurement.

### Removed

- **The retired power refusal.** The code that could stop the compressor for
  a power reason had been unreachable since 0.8.10. The field and both
  branches are gone, along with the five tests that exercised them. (DR-036)
