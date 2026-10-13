# Changelog

Every build that ships gets an entry here, newest first. Entries say what
changed for someone running the integration, and name the decision record
(`docs/decisions/`) where one applies.

Headings used: **Fixed**, **Changed**, **Added**, **Removed**.

## 0.9.6 - 2026-10-14

From the living room on the night of 13–14 October, the Office on the
morning of 14 October, and two reviews of the whole component against its
intent.

### Fixed

- **A room whose sensor reports only on change was left with no reading, and
  its air conditioner did nothing.** A room's temperature and humidity were
  treated as missing two hours after their last report. The living room's Eve
  Room reported at 17:42 and next at 21:05 because the room did not change, so
  the room, set to Sleep with Automatic control on, sent nothing to its unit.
  A room's own temperature and humidity are now trusted for as long as Home
  Assistant shows them as available; only `unavailable` or `unknown` makes them
  missing. (DR-070)
- **With its sensor down, a room whose unit was off stayed off.** The unit's own
  temperature (and humidity, where it reports one) now stands in, the room's
  trace says so, and nothing is learned from it. (DR-074)
- **The setpoint swung between 18 and 23 C while the room sat in its band.**
  The room loop decided whether it was cooling or heating from the room against
  its target, so a room 0.1 C either side of it lost and regained about 1 C of
  hold every minute or two; the living room was cooled to 19.4 C, below its
  band. The loop now turns round only when the rate it needs has clearly
  changed sign, and the setpoint is rounded the way the loop is working.
  (DR-075)
- **A drying or cooling unit was put on its fan short of the band.** The
  minimum off time then held it while the room climbed back; the living room
  went round about ten times overnight without reaching its band. A unit
  already working the room down now carries on until the room is in band.
  (DR-072)
- **The unit changed between cool and dry every minute or two** with the room's
  humidity at 64–65%. The route the unit is on is now kept unless the other
  wins by a margin, and an unconverged room more than 2 HCI above its band is
  cooled whatever the humidity. (DR-076)
- **"Turning the air conditioning off" was said when the stop was decided, not
  when it was sent.** On 14 October the minimum run held the Office's unit on
  for four and a half minutes after it was said. It is now said with the off
  command. Warnings that the air conditioning is running, or will be turned
  off, are said only where the unit was on. (DR-077)
- **The Office was started and told to open up in the same second, then warned
  for opening up.** While outdoor air is better, an occupied room's compressor
  is not started for ten minutes; an opening made while that advice stands
  stops the unit at once, with no warnings. (DR-073)
- **Dry mode was sent with no setpoint**, so the unit dried toward whatever it
  had last been given. It is now sent the room's setpoint and held to it in
  band, as cooling is. (DR-078)
- **Warnings were spoken for rooms the coordinator was not running.** Nothing is
  now spoken for a room with Automatic control off or one still waiting for its
  unit to report. (DR-071)
- **Sleep chosen by hand never ended in a room with no presence sensor.** It
  now ends when the room's sleep schedule finishes or, with no schedule, at
  sunrise. (DR-079)
- **A heading-home request never ended**, so the room stayed in Precondition
  and was never turned off when it emptied again. It now ends once the room is
  occupied, or an hour after its deadline if nobody arrived. (DR-080)
- **The thermal model learned against a setpoint the unit was never sent.**
  Every cooling interval was filed against the solved target plus the trim, a
  form the room loop has not sent since 0.9.0, and against the coordinator's
  figure when the unit had been set by hand. It now learns against the setpoint
  the unit reports. (DR-081)
- **An empty room waited three more minutes for a warning nobody would hear.**
  Where no warning can be said, the room is turned off when the vacancy period
  ends. (DR-082)
- **Removing a room used device-registry calls Home Assistant stops supporting
  in 2027.8.** It now uses the ones Home Assistant names as their replacements,
  and a removed room's device is removed.

### Changed

- **No warnings are spoken in a sleeping room**, asleep by its Comfort mode or
  its sleep schedule. (DR-071)
- Two outdoor-temperature reads, for the cheaper-window coast and the demand
  forecast, used the room-reading limit (two hours) instead of the outdoor one
  (three hours). They now use the outdoor limit, and a static test checks every
  house feed names its own limit.

## 0.9.5 - 2026-10-13

### Fixed

- **No spoken warning was ever given, and every attempt stopped that
  evaluation.** Warnings called `tts.speak` with the room's speakers as the
  target. Home Assistant needs a text-to-speech engine as the target and the
  speakers as `media_player_entity_id`, and rejected every call. The rejection
  was not caught, so each time a room emptied or a window was left open, the
  rest of that evaluation was skipped after its commands were sent: device
  clean-up, draw learning, the demand forecast and the save of what was
  learned. Seen eight times on 12–13 October as "Unexpected error fetching
  abode_hvac_coordinator data". Warnings now go through the engine to the
  room's speakers, and a warning that cannot be given is logged once and never
  stops anything else. (DR-069)

### Added

- **Spoken warnings**, under House configuration: the text-to-speech engine
  every room's warnings are spoken through. Left empty, the only engine Home
  Assistant has is used; with several and none chosen, a repair issue says so.
  (DR-069)

### Changed

- The spoken-warning tests now check every `tts.speak` call against Home
  Assistant's own schema. The tests before passed a call Home Assistant rejects.
  Running them needs `mutagen` and `ha-ffmpeg`, added to CI.
- `docs/contributing.md` no longer says the Home Assistant tests have never
  been run.

## 0.9.4 - 2026-10-12

From the Office on the evening of 12 October, set to Enforced, with the battery
at 93%.

### Fixed

- **An enforced room turned itself off the minute after every start.** The
  budget took off only the room's estimated draw from the house load, so the
  rest of what the unit drew was counted as the rest of the house for the hours
  left in the window, and the allowance collapsed. The rest of the house is now
  the house load measured while the room's own unit was not running, so the
  room's draw never counts against it. (DR-067)
- **One reading moved the budget.** House load and solar are now averaged over
  the last fifteen minutes. (DR-067)
- **One short reading turned an enforced room off.** A running room is now held
  at the gentlest setting until the budget has fallen short for ten minutes,
  and its trace says when it will be turned off. (DR-067)
- **Without a solar forecast, a room was allowed the house's share of the
  battery.** The energy left was spread over the hours left without taking off
  what the rest of the house would use in them. It is now taken off, as the
  forecast budget always did. (DR-067)
- **A reload turned an occupied room off.** After the 0.9.3 update the Office's
  presence sensor read unavailable for a minute, and every room started as
  unoccupied. Each room's occupancy is now kept across a restart or reload.
  (DR-068)

### Changed

- The reserve margin's help text on the power form now says it is in kWh, not
  a percentage.

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
