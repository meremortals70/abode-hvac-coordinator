# Changelog

Every build that ships gets an entry here, newest first. Entries say what
changed for someone running the integration, and name the decision record
(`docs/decisions/`) where one applies.

Headings used: **Fixed**, **Changed**, **Added**, **Removed**.

## 0.9.0 - 2026-10-03

### Fixed

- **The air conditioner was sent a setpoint it could not hold, over and over.**
  The Office unit advertises a step of 1.0 and holds whole degrees. The
  integration sent 22.8, 22.9 and 22.2, the unit kept 22.0, and the integration
  saw the difference and sent again about every ten seconds. Setpoints are now
  rounded to the unit's own step and range, so a value that has landed is not sent
  again, and the outer loop's deadband is half the step. (DR-051)
- **A cooling trim was applied to heating.** On 3 October the Office had a target
  of 23.2 °C, a heat demand and a commanded setpoint of 22.8 °C, because a trim
  learned while cooling was carried into heating. The trim is now kept per
  direction. (DR-054)
- **A window or door stopped the unit after a fixed two minutes with no notice,
  and a door whose age could not be read stopped it at once.** The grace is now a
  setting for each room, five minutes by default, with a spoken warning three
  minutes before and another as it stops. An opening of unknown age holds the
  unit instead. (DR-050, replacing DR-019)
- **Turning Automatic control off switched the air conditioning off.** It now stops
  the automation and leaves the unit exactly as it is. (DR-049, replacing DR-047)

### Added

- **The room loop is a PID built on what the room has learned.** It adds a
  feed-forward that holds the room against its own drift, a proportional term that
  closes an error over half an hour, and a derivative on the room's measurement
  that eases the setpoint off before the room arrives, so it lands in band without
  overshoot. Until a room has learned enough it behaves as before. (DR-054,
  replacing DR-015)
- **A room coasts when its own physics will bring it back into band.** The room
  is projected forward with nothing running, from its learned heat loss and solar
  gain, the forecast outdoor temperature and the sun's position against its
  windows. If it returns within the time the compressor would have taken plus ten
  minutes, the compressor is not started: a cold room on a warm day is not
  heated, and a hot room on a cool day is not cooled. Until the room has learned
  enough, a simple outdoor comparison applies. (DR-053, amending DR-032)
- **What a room's air conditioners can do is read when the room is set up.**
  Modes, fan speeds, vane positions, temperature range and step. A room cannot be
  added for an air conditioner that is not reporting, and a room configured
  before 0.9.0 is read once when Home Assistant first starts on it. Nothing is
  migrated. (DR-048, amending DR-024)
- **Controls for every room:** mode, fan speed, vertical vane, horizontal vane
  (where the unit has one) and setpoint. They show the unit's live state and take
  a change by hand once the matching switch is off.
- **An Automatic vane control switch for every room.** Off, and the coordinator
  never moves a vane in that room. It survives a restart. (DR-049)
- **Every mode or setpoint command sent is recorded**, in the Home Assistant log
  at INFO level and, for the last 200, in the diagnostics download. A change to a
  unit with no matching entry was not sent by this integration. (DR-052)
- The mode sensor's attributes now carry `room_c`, `approach_c`,
  `feed_forward_c`, `wanted_rate_c_per_hour`, `unaided_return_minutes`,
  `unaided_return_limit_minutes` and `setpoint_step_c`.
- Tests: 480 became 631 (487 pure, 144 against Home Assistant), and every new
  decision was checked by putting the defect back. The suite now runs against
  Home Assistant 2026.8.3 on Python 3.14 (it ran against 2025.1.4 on Python 3.12
  until 0.8.14).

### Changed

- **A room's decision is published even when nothing is sent to it** (Automatic
  control off, or an air conditioner that has not yet reported). Its trace says
  why.
- **The coast check that decided any room whose end-of-hour straight-line
  temperature landed in band is replaced** by the stepped projection above. A room
  inside its band still coasts for an hour on the same terms. (DR-053)
- The short-cycle guard no longer has a bypass for a forced stop, because there is
  no forced stop. (DR-049)
- Decision records: DR-048 to DR-054 added; DR-015, DR-019 and DR-047 superseded;
  DR-007, DR-024, DR-032 and DR-045 amended; all re-checked against this build.

### Removed

- The fixed two-minute `OPENING_STOP_DEBOUNCE`.
- The behaviour of the 0.8.14 off switch that commanded the unit off every cycle.

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
