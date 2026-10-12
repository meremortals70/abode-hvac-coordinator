# Changelog

Every build that ships gets an entry here, newest first. Entries say what
changed for someone running the integration, and name the decision record
(`docs/decisions/`) where one applies.

Headings used: **Fixed**, **Changed**, **Added**, **Removed**.

## 0.9.3 - 2026-10-12

From the living room's first night on Sleep, 11/12 October.

### Fixed

- **A choice made in Home Assistant was lost on reload.** Sleep set at 20:27 was
  gone after a reload at 21:40, and Automatic control off was lost the next
  morning. Nothing reached disk while the integration ran: every evaluation
  re-armed the five-minute save, and Home Assistant moves a pending save to the
  latest time asked for, so the write never came. Learned state now has one
  pending save at a time, a choice is written within five seconds, and
  everything is written when the integration unloads. (DR-064)
- **Sleep did not cool.** The loop asked for 0.2 C below a 25.9 C room, and
  rounding to the unit's whole degrees sent 26.0 C. Cooling now rounds down and
  heating rounds up. (DR-065)
- **A coast turned the unit off for a minute or two.** When the short-cycle
  guard refused a restart, the unit was commanded off in the middle of its fan
  coast (06:35 and 06:52). It now stays on its fan, or off where it has no fan
  mode. (DR-059)
- **The power ceiling flipped about twenty times in forty minutes.** It now
  drops at once and rises only after ten minutes, and only to a bin the
  allowance clears by 0.3 kW. (DR-062)
- **A sensor reporting the same value could be judged stale.** Age is now
  measured from the last report, not the last change. (DR-066)
- **Heading home with a deadline stopped every room being evaluated.** The
  date-time picker sends a time with no time zone, and comparing it with the
  clock failed on every evaluation. It is now taken as local time.
- **A removed room's last commands were never forgotten.** What was last sent
  has been kept per air conditioner since 0.8.8, but removing a room cleared it
  by room name, so nothing was cleared. Commands to units no remaining room
  uses are now dropped.
- **A learned rate far outside its range could stop the evaluation.** The
  humidity calculation overflowed below the formula's range. It now keeps the
  reading.
- **A sensor stuck on a value that is not a number was logged every thirty
  seconds.** It is now logged once, and once when it recovers, like any other
  unavailable reading.

### Changed

- **A humid room dries when the compressor stops; a dry room stays on its fan.**
  Judged by the same test that chooses dry over cool, at the humidity the room
  will reach once the coil has given its water back. (DR-059, amending DR-057)
- **A coast counts the coil's water.** The weather coast now projects the
  comfort index, with the room's humidity rising for fifteen minutes after the
  compressor stops at a rate each room learns. A cooling room is not coasted on
  the weather until that rate is known. Those minutes are no longer learned as
  the room's passive drift. (DR-060)
- **Enforced power management may turn the unit off; guidance never does.**
  Where even holding the room cannot be afforded, an enforced room is turned
  off until the budget allows it, and the trace says so. A coasting room the
  budget can afford is left alone. (DR-063)

### Added

- **The power budget counts the day's solar forecast.** A new field in the
  power settings lists every solar production forecast the Energy dashboard can
  use; choose one per array. Until the step is saved, the ones the Energy
  dashboard uses are read. The forecast sun between now and the end of a
  no-import window counts towards the budget. A forecast that cannot be read is
  named in a repair issue and the budget falls back to the solar being produced
  now. (DR-061)
- Decision records DR-059 to DR-066, and DR-058 in the index. Every record's
  code anchors re-checked against 0.9.3.
- **Every module is tested to 98% or better, the config flow to 100%.** 920
  tests. Every rule in the Home Assistant integration quality scale, bronze to
  platinum, is now done, or exempt for the reason the rule itself gives.
- A room that cannot be read now stops setup with a message saying which room
  and what to fix, in the integration's own translations.
- `docs/tariff.md` describes the power budget as it works now. It still
  described the 0.8.5 refusal, which 0.8.10 replaced.

## 0.9.2 - 2026-10-11

### Added

- **A Comfort mode for every room: Schedule, Sleep or Awake.**
  `select.<room>_comfort_mode`. Schedule is the default and is exactly the old
  behaviour: the room's sleep schedule decides. Sleep puts the room on its sleep
  band now, with or without a schedule. Awake keeps it on its occupied band
  while the schedule says sleep. Sleep and Awake go back to Schedule once the
  room is empty (presence sensor off and the stop grace run out), and Awake also
  when the sleep schedule turns off. Choosing Sleep or Awake in an empty room is
  refused with a reason. The choice survives a restart, is writable whether
  Automatic control is on or off, and never turns the automation off - only the
  band changes. The mode sensor carries a `comfort_mode` attribute, the reasons
  say when a choice is in force or has ended, and the diagnostics download
  includes it. (DR-058)

### Changed

- **Every place that asks whether a room is asleep now gets the same answer.**
  The unaided-return projection behind coasting and the cheaper-tariff wait
  read the sleep schedule directly. They now ask the same question the mode
  decision does, so a room put on Sleep by hand is projected against its sleep
  band, not its day band. (DR-058)

## 0.9.1 - 2026-10-04

### Fixed

- **The unit switched off and on every 37 minutes.** With no air-movement sensor
  configured, the comfort index counted the room's air as moving whenever the
  air conditioner was in any mode but off, and still air adds a full point. The
  index fell by 1.0 when the unit started and rose by 1.0 when it stopped, with
  the room sensor unchanged, so the controller decided from a number its own
  action had just moved: running dropped the index below the band's floor, the
  room asked for heat and coasted, the unit was stopped, the index rose, and the
  room warmed until the fan and compressor started again. The air
  conditioner's own state no longer counts. Only an air-movement entity you
  configured does, and with none, air is assumed still. (DR-055)
- **The unit sat at 19 °C for hours.** The room loop asked for 4 °C below the
  room, the most it could, for errors as small as 0.3 °C, because the Office's
  learned rate curve is nearly flat and any wanted rate above its best read as
  "ask for the most". It now asks for the smallest approach at which the unit
  reaches its best rate. And a unit that is running, with the room in band, is
  now sent the loop's setpoint when it differs from the unit's by a step, so a
  pull-down setpoint no longer stays on a unit that has arrived. (DR-056)
- **A coast turned the unit off.** Coasting now stops the compressor and leaves
  the unit in fan-only mode on its quietest fan. A unit with no fan-only mode
  still coasts off, and its trace says so. Off is kept for lockout, an empty
  room, an opening held past its grace, a direction the unit cannot deliver and
  a deferred precondition. (DR-057, superseding part of DR-032)
- A line in `behaviour.md` still said a coasting room can never drift out of
  band, which stopped being true in 0.9.0 when the weather coast was added. It
  now says so.

### Changed

- A coasting room projects no compressor energy and is not throttled by the power
  ceiling.
- The mode sensor's attributes carry `hold_setpoint`, true for a room inside its
  band whose unit is left running.
- Tests: 631 became 644 (491 pure, 153 against Home Assistant). Every new
  decision was checked by putting the defect back. The 0.9.0 tests of the room loop
  assumed a steep, even learned curve, which the Office does not have; one test
  now uses the Office's real figures.
- Decision records: DR-055, DR-056 and DR-057 added; DR-010, DR-017, DR-032,
  DR-053 and DR-054 amended; all re-checked against this build.

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
