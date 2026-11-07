# Changelog

Every build that ships gets an entry here, newest first. Entries say what
changed for someone running the integration, and name the decision record
(`docs/decisions/`) where one applies.

Headings used: **Fixed**, **Changed**, **Added**, **Removed**.

## 0.10.2 - 2026-11-07

### Fixed

- **Shared outdoor unit message.** Where a room on a shared outdoor unit is
  left as it is and runs the other way, the room that rests is now told
  another room is "left as it is, running the other way". It said "run by
  hand" even when the room was left as it is for another reason (Automatic
  control off, an empty-room warning counting down). Wording only; what the
  guard does is unchanged [D020].
- **Device version test.** The test of a room's device compared its software
  version with a fixed "0.10.0", so it failed on 0.10.1. It now reads the
  version from the manifest.

## 0.10.1 - 2026-11-07

A rewrite of the whole component to the design specification of 23 October
2026 (revised 27-29 October 2026), built one module at a time. It needs
Home Assistant 2026.10.4 or later (2026.9.4 until 6 November 2026).

### Added

- **Step 0, foundation and helpers.** `const.py` (every design value of the
  specification's Section 10, with its basis), `models.py` (shared
  enumerations, house and room settings, a unit's capability profile),
  `linalg.py` (matrix arithmetic, no external package), `hci.py` (the comfort
  index), `psychro.py` (dew point, free cooling, condensation), `sun.py` (sun
  position and sun on the glass) and `capabilities.py` (what a unit can do).
  Pure tests for each, plus a structure test that fails if any decision module
  or helper imports Home Assistant.
- **Step 1, Observe.** `observe.py` builds one snapshot of a room each cycle
  from the states the coordinator hands over, with every value tagged as
  measured, stood in, estimated, missing or unknown; ages readings and lists
  stale feeds; stands the unit's own reading in for a missing room reading;
  decides whether each compressor ran, from frequency, `hvac_action` or a
  house-load step, and says which; works out the sun on the glass. Readings
  are converted to C, kW and m/s; a reading in a unit it does not know is
  treated as missing.
- **Step 2, Model.** `model/` learns each room: `physics.py` (the two-store
  heat balance, the unit's rising rate curve, exact 5-minute matrices),
  `filter.py` (one Kalman filter carrying the room, the mass and all 23
  figures, with the sensor's noise learned from the readings), `unit.py` (the
  unit's effort, delay and lag, lowest steady rate, and each outdoor unit's
  draw from house-load steps), `humidity.py` (the moisture filter),
  `trust.py` (one- and six-hour forecast errors, weekly errors) and
  `forecast.py` (drift, forecasts with their uncertainty, the setpoint for a
  rate). The unit's delay and lag are learned as two more states of the
  heat filter (27 in all) instead of by a separate fit after each start,
  stop or setpoint change, which could not recover them at 0.2 C sensor
  steps (decision of 27 October 2026). On the specification's simulated
  room over 14 days at 0.2 C steps the filter learned a total delay and lag
  of 19-20 minutes against a true 21, the unit's rate at effort 2 within
  11 %, and forecast resting hours to 0.06-0.10 C.
- **Step 3, Comfort.** `comfort.py` decides whether the room is occupied,
  with a grace each way; runs Precool and ends it; follows Comfort mode and
  the sleep schedule; chooses the band in force by the first rule that
  applies; moves a changed band steadily over an hour and asks for the room
  to be decided again at once; marks a room waking on its way out of Sleep;
  and gives the band at every 5-minute point of the next six hours, with
  its centre as a dry-bulb temperature.
- **Step 4, Economics.** `economics.py` reads Abode Power Tariffs'
  `get_intervals` response; prices each half-hour ahead; finds the
  no-grid-import window in force; works out the house's one power allowance
  from the battery, the solar forecast (or, without one, the solar now) and
  the rest of the house's load; turns it into each room's ceiling, which
  drops at once and rises only after 10 minutes with 0.3 kW to spare; keeps
  an enforced room whose lowest rate cannot last the window cooling at that
  rate, and asks it to turn off, while running, only once the battery is
  down to its reserve or cannot supply that rate's draw; a unit whose draw
  is not yet learned has no ceiling until the battery reaches its reserve; checks the grid sensor's sign and adds up import in a
  no-import window; and adds the rooms' planned energy into a demand
  forecast by tariff interval.
- **Step 5, Control.** `control/plan.py` finds, every 5 minutes, the
  cheapest six hours that keep the room near the centre of its band: a
  beam search over rest, dry, cool and heat at each rate of the learned
  curve, and the blinds, ranked first by time outside the band (with a
  one-sigma margin) and then by comfort, energy and start costs.
  `control/pid.py` holds the room on the plan's path every cycle, with
  gains worked out from the room's model. `control/rl.py` and
  `control/arbiter.py` hold the learned controller (inactive until a policy
  exists) and the rules for when it may take over. In a whole-loop
  simulation of the specification's room from day one, the room was in its
  band 99.7-100 % of the time with 1-2 compressor starts a day after the
  first day.

- **Step 6, Delivery.** `delivery.py` turns the action and rate Control chose
  into what each head and blind is asked for. Cooling and heating: the
  setpoint is the unit's own reading less, or plus, the effort the learned
  curve gives for the rate, worked out for each head from its own reading,
  and kept within the unit's range. Dry: the setpoint cooling would have at
  the lowest steady rate. Rest: fan-only at the quietest fan speed, or off
  for a unit with no fan-only mode. A unit that holds only a range gets the
  setpoint -1.0 to +1.0 C. A unit with a coarse setpoint step is moved
  between the two nearest values so the 30-minute average is the setpoint
  wanted, each value held at least 10 minutes. Blinds go to the plan's
  position when more than 5 points away; one whose position is unknown is
  left alone. Fan speed and, under Automatic vane control, the vertical
  vane are chosen from what the unit offers. Free cooling is advised in an
  occupied room, and condensation risk reported. Where a setpoint cannot
  be worked out the unit is left alone and the reason named.
- **Step 7, Limits.** `limits.py` decides what may reach the unit, the same
  way whichever controller is in charge. In order, the first that applies:
  lockout, off every cycle; Automatic control switched off, off once, then
  nothing; Leave, off at once; an empty room, off, after a spoken warning
  where one can be heard; an open door or window, nothing new started and,
  after the room's grace and two warnings, off, or off at once if it was
  opened on free-cooling advice; a missing reading, nothing sent; enforced
  power management, off. Then always: only what the unit offers, within
  its range; the short-cycle guard, 10 minutes on and 5 off per outdoor
  unit across every room on it, which never holds lockout, Automatic
  control off or Leave; and the tariff's constraints, recorded, with any
  this component does not know reported. Whenever a rule changes the step
  the PID is told, so its integral holds.
- **Step 8, Act.** `act.py` compares the allowed step with each unit's live
  state and sends only what differs: the mode, the setpoint (or range) when
  it is a step or more away, the fan speed, and the vertical vane under
  Automatic vane control; the horizontal vane is never moved. A command
  sent in the last cycle that the unit has not yet reported is not sent
  again. Blinds are moved when more than 5 points away. Every command is
  logged at INFO with the rate it was for, the room temperature and the
  centre, and the last 200 are kept; a command that fails is logged and the
  room's other commands still go. A user's change from a room control is
  refused, with a translated reason, while Automatic control (or, for a
  vane, Automatic vane control) is on, or if the unit does not offer it;
  otherwise it goes to every head. Temperatures are sent in the system's
  unit. First tests through Home Assistant (`tests/ha`).
- **Step 9, Evidence.** `evidence.py` builds each room's decision trace for
  its Mode sensor: the band and why, the comfort index and where it sits,
  who is in charge, the plan with its forecast, its costs and every first
  action not taken with the reason, the PID's terms, the model's figures
  and how far they can be trusted, the power ceiling or "no limit", each
  limit that changed the step, and each compressor's state and source. The
  Mode sensor's state is the band rule in force, with lockout and
  Automatic control off first. It counts each room's day: share of
  occupied time inside the band and near its centre, starts per running
  hour, and cost. It decides which warnings are said, never while
  Automatic control is off, the room is asleep or its unit is not on, and
  one held by the minimum run is said with the off it warned of. It works
  out every repair that should stand, and logs each lost and regained
  reading once.
- **Step 10, Memory.** `memory.py` keeps, in one versioned file in Home
  Assistant's storage, what each room has learned (both filters with their
  uncertainty and the sensor's learned noise, the unit's lowest steady
  rate, trust, who is in charge and the arbiter's scores), each outdoor
  unit's draw, every choice the user has made, occupancy, the unit's
  capability profile and yesterday's scorecard. The PID's integral is not
  kept. A number that is not finite is discarded and relearned. Learning is
  saved at most 5 minutes after it happens, and a waiting save is never
  pushed later; a user's choice within 5 seconds; everything when the
  component unloads. A new room can be seeded by running its filters over
  the recorder's history, minute by minute, exactly as the live path does,
  with the weather entity's recorded cloud cover standing in for the
  forecast.
- **Step 11, the integration.** The component can now be installed and set
  up from the UI. First setup asks for the house (tariff, outdoor sensors,
  forecast, power, grid direction, speech engine) and then the first room;
  Reconfigure reopens the house; the options add, edit and remove rooms
  without a reload, and seed or reset a room's learning. The coordinator runs
  every room each 30 seconds through the ten modules, with the filters and the
  plan in the executor; one failing room never stops the others. Each room is
  a device with its mode sensor (carrying the full decision trace), comfort
  index, target and commanded setpoint, dew point, settings, controller,
  learning and yesterday's scores, the Automatic control and Automatic vane
  control switches, Comfort mode and the unit's own controls, the setpoint,
  and Leave and Precool buttons. The house shows the demand forecast, the
  tariff now, constraints in force, projected cost, outdoor readings, the
  forecast peak, stale feeds and the number of rooms. The `precool` and
  `cancel_precool` actions, repairs, spoken warnings, diagnostics (the home's
  position redacted), icons, brand images and the quality scale file are
  included. Required Home Assistant 2026.9 or later at the time; 2026.10.4
  since 6 November 2026.
- **Step 12, documentation and CI.** A README and user guides in `docs/`
  (use cases, installation and removal, configuration, entities, actions,
  data updates, examples, known limitations, troubleshooting), the design
  (`docs/architecture.md`) and one file per design rule
  (`docs/decisions/`). `hacs.json`. CI on every push and pull request to
  main: ruff 0.17.3 and mypy --strict; the pure tests and the whole-loop
  simulation with nothing installed; and the whole suite through Home
  Assistant 2026.10.4 and 2026.11.0 (2026.9.4 until 6 November 2026),
  failing if any module is at or below 95 % coverage.

### Fixed (since step 12, from live testing on Home Assistant 2026.11.0b0)

- **Blinds were closed with no sun on the glass.** The plan now moves blinds
  only in a half-hour with the sun on the glass, and leaves them alone in one
  without; sun that cannot be worked out (no window facing) counts as none.
  (DR-023)
- **Turning Automatic control back on could stop a compressor you had just
  started.** The short-cycle guard did not count a start made by hand while
  Automatic control was off, so it held the "start" for five minutes and put
  the unit on its fan. It now follows the compressor as it is seen to run
  whenever the unit is left alone. (DR-016)
- **A sensor whose integration had gone did not raise the missing-sensor
  repair.** Home Assistant keeps such a sensor as an "unavailable" stand-in;
  that now counts as missing, as a deleted sensor does. (DR-046)
- **A countdown could read "off in 0 s" while it still waited.** Countdowns
  round up.
- **A first long run at full rate became the unit's lowest steady rate,** so
  after a hot-afternoon pull-down the plan could only rest or cool at full
  rate. The lowest rate is now learned only from a run held at or below the
  lowest rate offered at the time. (DR-100)
- **From the independent review of 31 October:**
  - A run with the room at its setpoint could store a lowest rate of 0, which
    then never changed. Minutes with no effort asked now break the run, and
    a stored 0 is relearned. (DR-100)
  - A room run by hand kept its shared outdoor unit running, yet another room
    on that unit was held on after its own stop. The guard now counts the
    hand-run room as wanting the unit. (DR-016, DR-020)
  - The missing-sensor repair was raised while the sensor's own integration
    was only retrying or reloading, and for a moment at every start. It is
    now raised only once Home Assistant has started, and only when nothing is
    about to provide the sensor. (DR-046)
- **From the second independent review, 1 November:**
  - The plan could find no allowed first step, and the room failed: a waking
    room that had been heating under ten minutes, or a power ceiling below
    the lowest rate. Where it did find one, a cold waking room could be put on
    dry. The minimum run now binds the plan only where the running mode can
    carry on; otherwise it may rest, and the short-cycle guard holds the stop.
    (DR-016, DR-085)
  - Setup failed for good when a solar forecast source was still starting.
    A source not yet loaded is read on the next 15-minute read, and anything
    another integration's forecast raises becomes the forecast repair.
    (DR-061)
  - The lowest steady rate was never learned from heating, and a low figure
    learned early could never rise. Heating holds count, a hold at the lowest
    rate offered replaces it, and nothing below the curve's first point is
    offered. (DR-100)
  - A unit working in °F on a °C system (or the reverse) had its setpoint
    step converted from the wrong unit. The step is read in the unit's own
    unit, which Home Assistant does not convert. (DR-051)
  - The missing-sensor repair could appear while a sensor's platform was only
    retrying. It now waits until the sensor has been gone 15 minutes, and says
    to check the integration it comes from.
  - Seeding a room that had already learned replayed its old readings on top
    of what it knew, out of time order. A seed now starts from the starting
    figures; a history that teaches nothing leaves the room as it was.
- **From the third independent review, 1 November:**
  - A compressor-frequency sensor shared by the heads on one outdoor unit
    made a head that was off read as running, so a hand-run room could let
    another room stop 3 minutes into its 10-minute minimum run. A head that
    is off or on its fan now counts as not running, whatever the sensor
    says. (DR-025, DR-016)
  - Heating and cooling shared one lowest steady rate, and the plan offered
    heating from the cooling curve's floor. Each now has its own. (DR-100)
  - The missing-sensor repair never appeared for a sensor whose integration
    had failed for good or been disabled. Only one still setting up or
    retrying is waited for. (DR-046)
  - A disabled solar forecast source was waited for forever, with no repair;
    and one whose integration had not finished starting raised the repair
    for 15 minutes at boot. A disabled source now counts as unreadable, and
    one still starting is waited for. (DR-061)
  - Seed or Reset chosen while a room was already seeding was dropped, though
    the screen said it had started. The screen now says it is already
    seeding.
- **From the fourth independent review, 2 November:**
  - The power budget found a heating room's limit on the cooling curve, so a
    heating room in Enforced mode could be held below what its allowance
    covers, or, resting, never be offered heat for the rest of the window.
    The budget now works on the curve of the way the room runs, or must go.
    (DR-028, I10)
  - The plan and the demand forecast priced heating at the cooling curve's
    draw. Each rate is now priced on its own curve. (DR-028)
  - A head turned on beside one already running its outdoor unit was learned
    as that unit starting, so a small load step became the unit's whole draw.
    A head joining or leaving a running unit is no longer learned from.
    (DR-028)
  - A setpoint for a unit working in °F on a °C system (or the reverse) was
    rounded so that Home Assistant's conversion landed a hair under the
    degree; an integration that truncates set a degree lower. What is sent
    now converts onto the unit's own degree, never under it. (DR-051)
- **From the fifth independent review, 2 November:**
  - In a °F house, a °C unit sent its maximum setpoint was refused by Home
    Assistant every cycle: the value converted to a hair over the maximum.
    At the top of the range the value now converts to a hair under it.
    (DR-051)
  - In a °F house, Home Assistant shows a °C unit's setpoint in whole °F, so
    a half-degree drop looked already done and was never sent. A shown value
    now counts as done only within half a shown degree, and a value other
    than the one last sent is always sent. (DR-007)
  - The enforced power ceiling limited the plan but not the PID's
    corrections, so the unit could be asked for more than the allowance; and
    a lower ceiling waited up to 5 minutes for the next plan. The PID is now
    held to the ceiling, and a new ceiling is planned under at once.
    (DR-041, DR-062)
- **From the sixth independent review, 2 November:**
  - In a °F house, a °C unit's limits were read from Home Assistant's shown,
    rounded figures (whole °F), so a setpoint at full cooling or heating
    could fall just outside the unit's real limits and be refused every
    cycle. The unit's own limits are read from the entity; failing that, the
    shown ones are moved inside. (DR-051)
  - A setpoint shown too coarsely to tell from the wanted one (22.0 and
    22.5 °C both show as 72 °F) was never sent after a restart, or after the
    unit was set by hand with Automatic control off. It is now sent once, and
    what was sent is forgotten while the room is not run. (DR-007)
  - The enforced ceiling, found for the way a resting room must go, also
    capped the other direction's steps by the same figure. It is now turned
    into the other direction's rate for the same power. (DR-028, DR-041)
  - One head stopping as another started on the same outdoor unit in the
    same minute could be learned as the unit stopping and starting. Neither
    is learned now. (DR-028)

- **From the sweep after the fourteenth review, 7 November (on r21):**
  Each defect pattern from the last reviews checked across the whole
  component, not just where it was found (Jason: "yes do the sweep").
  - One rule now decides whether a unit is running wherever the component
    acts: seen running, or in cool, dry or heat with its compressor
    unknown. Two more places took an unknown compressor as stopped:
    - the short-cycle guard, so a unit run by hand with no frequency
      sensor or hvac_action (the living room) did not keep its way on a
      shared outdoor unit, and its run was not held;
    - an open door or window, so such a unit was stopped at once instead
      of being given its grace. (D016, D020, D050)
  - The recorder's own error (a database error, a timeout) while reading a
    room's history is logged as a history that could not be read; before,
    only Home Assistant's own error was.
  - Another integration's energy platform failing leaves the solar
    forecast choice empty on the Power screen instead of breaking it.
  - Checked and left as they are: the model and the draw learner use only
    a known compressor state, by design (D074, D028).

- **From the fourteenth independent review, 7 November (on r20):**
  - A solar forecast integration timing out (or raising anything else) at
    start-up raises the forecast repair; before, setup stopped with an
    error until the integration was reloaded by hand. (D061)
  - On a shared outdoor unit, a reversal after a restart still gets its
    5-minute rest; before, a unit running since start-up could go from
    cooling to heating in one cycle. (D016)
  - Yesterday's cost reads unknown for a day the unit ran unseen with its
    draw not yet learned; it read 0.00. (D097)
  - The lost-reading log line says a stand-in is used, not that the
    room's sensors go unavailable (they do not).
  - The PID example in the design adds the actuation interval as the code
    does: θ 8.75 min, K_p 4.16 per hour, T_i 85 min, T_d 12.4 min.

- **From the thirteenth independent review, 7 November (on r19):**
  - An Enforced room whose draw is not yet learned is turned off at the
    battery's reserve even after its unit has run for 15 minutes. Before,
    the budget stopped at "the rest of the house is not yet measured" and
    the room ran on below the reserve. (D063, D067)
  - A start or stop found by the house-load step no longer teaches the
    draw learner: it is dated a minute or more after the change, so the
    load either side of it was not either side of the change. Before, the
    learned draw fell (0.10 kW where 1.5 kW was true). (D028)
  - The house-load step waits for a load reading from the end of its
    minute. Before, it read one from 30 seconds after the command, so a
    compressor starting 31 to 60 seconds after it was never seen.
  - A blind moved in the same cycle as a start no longer counts as another
    unit changing: only climate commands are kept for the load step and
    the draw learner. (D028)
  - A head in a running mode with its compressor unknown counts as running
    for the power budget and the plan, so its draw is not counted as the
    rest of the house; an Enforced room in that state is turned off at the
    reserve. (D028)
  - A door or window opened on free-cooling advice stops the unit at once,
    as D073 says; the short-cycle guard held it for up to 10 minutes.
  - The humidity filter steps each minute by the exact solution and holds
    its figures in the heat filter's range; a stored figure outside is
    relearned. The straight-line minute diverged above 120 per hour.
  - The grid repairs and their counts are kept across a restart or reload.
  - A failed Energy-dashboard solar forecast now raises the repair, as a
    chosen one's does. (D061)
  - Documents: the PID keeps only its feedback part on a gain change;
    battery power is not published; the grid direction is asked on
    Reconfigure while its repair stands; Leave without a presence sensor
    ends on Automatic control or Comfort mode (Precool needs a sensor).

- **From the twelfth independent review, 7 November (on r18):**
  - On a shared outdoor unit, a stop that was not the user's (the room
    empty, an Enforced turn-off) is held to the 10-minute run while
    another room wants the other way, as it is without one. Before, the
    cooling run was stopped 3 minutes in. (D016)
  - The house-load step (4.8, source 3) can now answer whether a
    compressor runs: Observe is given each outdoor unit's learned draw
    whatever its compressor is seen to do. Before, it was given only the
    draw of units already seen running, so a unit no other source answers
    (the living room) stayed unknown for good. (D028)
  - An hour is scored as resting for the trust check and the Learning
    sensor only when every step knew the compressor and found it stopped.
    Before, hours the unit cooled unseen were scored as rest forecasts:
    60 of 287 such hours in the living room's history, 34 in the office's.
  - The grid-direction repair: Reconfigure shows the Grid direction screen
    while the repair stands, and ten readings in a row agreeing clear it.
    Before, the screen was shown only for a new grid sensor, so the repair
    could not be cleared as it said. (D038)
  - The Commanded setpoint sensor shows the middle of the range sent to a
    unit that takes only a range; it showed nothing. (D024)
  - An unknown input widens the room's uncertainty whether or not there
    is a reading that step, as the design says.
  - Documents: Q_air is learned at once on a state-only step; an unlearned
    draw sets no ceiling (the 0.10.0 entry below); a missing reading does
    not make the mode sensor unavailable.

- **From the eleventh independent review, 6 November (on r17):**
  - The humidity estimate drifted from the room. Its filter took every
    miss as sensor noise (6.5 hPa^2 learned in the house's history) and
    left the estimate 2 hPa or more from the room's vapour pressure on a
    quarter of its steps. It now splits the misses between the reading
    and the room as the heat filter does; replaying the house's history,
    the mean gap is 0.06 hPa. A step whose humidity inputs were stood in
    or unknown now corrects the moisture and teaches no figure. (R21,
    D074)
  - A change of PID gains held the whole output where it was, cancelling
    the new plan's own rate: a room asked for 4 C/h on its path was sent
    1 C/h until the next stop. Only the feedback part is now held; the
    plan's rate passes straight through.
  - A plan made between filter steps started where the room was up to 5
    minutes before; it now starts from the same estimate the PID acts on.
  - The heat figures are held to at most 1,000 per hour (was 10,000), so
    the 5-minute steps of the plan and forecast cannot overflow.
  - A step with the compressor unknown in heat_cool or auto now widens
    the room's uncertainty by either way's largest rate; before, it added
    nothing.
  - On a shared outdoor unit a room run by hand now has its way, and a
    reversal holds the rooms wanting the other way at rest for the rest of
    the minimum run plus the 5-minute rest. Before, a hand-run room was
    not counted, and the unit could be asked to reverse with no rest.
    (D016, D020)
  - A timeout or other error from the tariff, the weather forecast or a
    speech engine is now a failed read or failed warning like Home
    Assistant's own; before, it could stop the cycle or the setup.
  - The spoken countdown rounds its minutes up, so a warning never says 0
    minutes while time remains.
  - An Enforced room whose outdoor unit's draw is not yet learned is no
    longer turned off: it has no ceiling and keeps going until the battery
    is down to its reserve or the window ends at the tariff change, since
    running is the only way its draw is learned. Before, it was turned off
    and its draw could never be learned. (D044, D063)
  - Setup texts: the power screen says which inputs are needed and which
    optional; the grid direction is pre-filled on Reconfigure; a seed from
    a history that teaches nothing leaves the model as it was; the
    filter-silent repair says the room is still followed.
  - Documents: the free-cooling advice is in the trace and attributes,
    not spoken; the demand forecast runs every cycle; the rest of the
    house's load is measured while the room's own and every other
    enforced unit are stopped; assumed sun corrects the air and mass
    only; the PID's delay adds half the filter's step.

- **From the tenth independent review, 6 November (on r16):**
  - The room estimate stopped following the sensor. The r16 surprise limit
    shrank each correction as the miss grew, and the learned sensor noise
    took the miss instead: 2.17 C^2 on a sensor in 0.1 C steps, and the
    living room's estimate stayed 1 C or more from the room for 12 hours.
    The sensor's noise is now learned from the reading's own second
    differences, and what the innovations show beyond it is the room's own
    unforecast change, added to the air's noise, so a room that moves is
    followed. Replaying the house's history: steps more than 1 C out fell
    from 160 to 6 (office) and 652 to 15 (living room); the one-hour
    forecast error with the unit resting fell from 0.33 to 0.21 C and 0.63
    to 0.48 C. (R21)
  - A reading is now always used to say where the room is. A step whose
    compressor state is unknown, whose sun had to be assumed, or that
    comes too soon after start-up corrects the air and mass, with the
    unknown input's possible effect added to their uncertainty, and teaches
    no figure. Before, the air ran open-loop through every such step: a
    whole night of cooling in the living room. (DR-074)
  - A minute whose compressor state was unknown in the unit's last 40
    minutes no longer lets the figures learn as if it ran. (DR-074)
  - After a restart the figures wait until the unit's rate has come through
    its delay and three lags (95 %), not a fixed 40 minutes; with a 30-minute
    lag the rate was still 29-32 % short and the curve learned high.
  - The PID compared a path that moves every 30 seconds with an estimate
    that moved every 5 minutes, so a room exactly on its path showed an
    error and the output swung across its range. The estimate is carried
    forward on the model's rate between filter steps, and the gains count
    half the filter's step as delay. (DR-054)
  - The plan and the forecast now start from the unit's rate now and reach
    each rate through its delay and lag (taken as one lag of their sum);
    they had the rate jump at once, up to 1.1 C off an hour after a stop.
  - The plan's margin is the room's uncertainty, not the sensor's noise; it
    moves one for one with temperature (the plan holds vapour pressure), not
    1.5 times as fast; and it is held to half the band's half-width, so a
    band always has an inside.
  - A lag or delay at the top of its range could stick there: the filter's
    slope is now taken downward at the top.
  - Every figure is held within the range the arithmetic can carry, and a
    step whose arithmetic fails is undone, not raised every 5 minutes.
  - Rooms on one outdoor unit could be sent heat and cool together, which a
    multi-split cannot do. The room furthest outside its band has the
    outdoor unit; a room wanting the other way rests (decision of 6
    November 2026). A reversal is a stop and a start: the run keeps its 10
    minutes and the rest its 5. (DR-016, DR-020)
  - A climate entity's own error (a timeout, a connection error, Home
    Assistant's call validation) is recorded as a failed command and the
    room's other commands still go; only Home Assistant's errors were.
  - A tariff priced at or below zero throughout reversed the plan's comfort
    weight; the price of no tariff, 1, now stands in.
  - One missing battery, load or solar reading wiped the power ceiling's
    memory, so it rose without its 10-minute hold when the reading came
    back. The hold now stands. (DR-062)
  - Leave in a room without a presence sensor stays until you change
    Automatic control or Comfort mode; the docs said
    "until someone comes back". The behaviour is right (Jason) and the docs
    now say so. (DR-088)
  - Home Assistant 2026.10.4 is now the oldest release supported, in
    `hacs.json`, the README and the install guide; CI no longer tests
    2026.9.4 (Jason: "drop anything before .10.x").

- **From replaying the house's own 12 days of history, 6 November:**
  - One reading could wreck a room's model. A sensor reporting to 0.01 C
    was trusted to about 0.003 C, so a single 0.4 C jump was forced into
    the room's figures (the mass exchange went from 0.3 to 180 per hour in
    one correction). The minute step's straight-line arithmetic then
    diverged: the forecast room reached billions of degrees and the model
    never recovered. A reading far outside what the filter expected is now
    weighted as a 3.3-sigma surprise at most, in both filters; and the
    minute step uses the heat balance's exact solution, which stays bounded
    for any figures. (R20)
  - The unit's lag could grow without limit (16.5 days and 4 years were
    stored), and the PID's derivative time is the lag, so its gain reached
    4,306 and the setpoint went to the unit's minimum. The lag is now held
    between 1 and 30 minutes, the delay's own range, in the state itself.
    (R19)
  - A figure whose input never acted walked on the filter's correlations
    alone: in a room whose sun is never measured, the mass's sun gain went
    from 0.1 to 87 per hour and the forecast room passed 100 C. Each figure
    is now corrected only on a step where its input acted, as the humidity
    filter already did: the sun's figures with sun on the glass, each
    direction's curve when the unit worked that way in the last 40 minutes,
    the delay and lag when it worked either way. (DR-026, DR-060)
  - A stored model whose lag is outside 1 to 30 minutes was learned by a
    filter that could diverge: it is refused at start-up with the trust it
    earned, and the room is seeded again from history. Choices and
    everything else kept stand. (R19)

- **From the ninth independent review, 5 November:**
  - Seeding a room in a house with no outdoor temperature sensor replaced
    what it had learned with the starting figures: the replay had no
    outdoor temperature, so it corrected nothing. The weather entity's own
    temperature and humidity now stand in, in its own units, as they do
    live; and a replay that corrects nothing leaves what the room has
    learned as it was. (DR-099)
  - A compressor started by hand while Automatic control was off was
    stopped as soon as control came back to a plan that rests. Its stop is
    now held until it has run 10 minutes, keeping it as it runs. (DR-016,
    DR-089)
  - Another enforced outdoor unit running was taken off a room's power
    allowance twice: once in the rest of the house and again as its own
    draw. The rest of the house is now measured with the enforced units
    stopped. (DR-067, I10)
  - Yesterday's cost read 0.00 while the room's draw was not yet learned.
    It now reads unknown. (DR-097)
  - After a restart while the unit ran, the room's model took the unit as
    having rested and learned its delay, lag and curve from that. Nothing
    is learned now until the unit's last 40 minutes have been seen.

- **From the eighth independent review, 2 November:**
  - The power budget walked the whole solar forecast, today's past hours
    and tomorrow's included, instead of the no-grid-import window. It now
    walks from now to the window's end, counting a forecast hour partly
    inside for its part, and no solar where the forecast has none. (DR-061)
  - A room waking from Sleep below its daytime band was never heated: it
    stayed waking until it warmed up, and a waking room is not heated.
    Waking now ends when the hour's ramp ends. (DR-085, DR-092)
  - A unit running in heat_cool or auto was learned as a resting room.
    Nothing is learned from those minutes now. (DR-074)
  - The 10-minute hold on a start when outdoor air would cool the room
    never held anything: the advice was judged after the start had gone
    out. It is now judged first, and the start waits 10 minutes. (DR-073)
  - An empty room waited for a spoken warning when no speech engine could
    say it. It goes off at once now. (DR-069, DR-082)
  - The Precool action's description said it runs whether or not anyone
    is in; it ends once the room counts as occupied. The description now
    says so. (DR-080)

- **From the seventh independent review, 2 November:**
  - A room with one head that takes only a low and high setpoint and another
    that cannot take them was accepted, and the second head was sent a range
    Home Assistant refused every cycle. Such a room is now refused at setup.
    A room set up before this keeps running as it was; open its options and
    save to check it. (DR-024)
  - The hourly trust check scored the air conditioner's own reading when the
    room sensor was unavailable. Only the room sensor's own reading is
    scored now. (DR-074)
  - A lost room sensor was not logged while the air conditioner's own
    reading stood in for it. It is now. (DR-074)
  - The Controller sensor's plan was kept in the recorder's history. It is
    kept out now, as documented.

### Changed (since step 12, for Home Assistant 2026.11)

- Ready for Home Assistant 2026.11, and still runs on 2026.10.4 and later
  (2026.9.4 until 6 November 2026).
- The manifest declares its integration categories,
  `climate.heat_pumps_air_conditioning` and `energy.solar_batteries`, which
  the Marketplace browses by and the HACS action checks.
- `hacs.json` drops `render_readme`, which neither HACS nor the
  Marketplace reads.
- From 2026.11 a form field with no effect without another is shown only
  with it, and not saved while hidden: battery size, rated discharge and
  reserve margin with the battery charge sensor; solar forecast sources
  with the solar power sensor; the overhang with the window direction;
  Announce through with Announce. Before 2026.11 every field is shown, as
  it was.
- A room's device names the house's device it is reached through in the
  form both the old and the new device information take.
- The documentation covers installing through the Marketplace.
- CI also tests against 2026.11.0 (released 4 November), with Home Assistant's own
  `pytest-homeassistant`, which replaces
  `pytest-homeassistant-custom-component` from 2026.11. The tests import
  the helpers through `tests/ha/harness.py`, which uses whichever is
  installed.

### Fixed (since step 12, from the review of 29 October 2026)

- Seeding replays every recorded row, including those where only an
  attribute changed (a unit's `hvac_action`, the forecast's cloud cover),
  each at the time it was written. It had read only rows where the state
  changed [D002].
- Rooms on one outdoor unit are budgeted as one draw on the battery: a
  room's allowance is less what the other rooms on its unit draw now,
  whatever their power management, and a room's scorecard is charged its
  own share, not the whole unit's [D097].
- An edited room keeps what its modules remember between cycles: the
  running command the short-cycle guard holds a stop on, today's
  scorecard, the plan, the band's ramp, warnings already given and
  commands still to be reported. An edit had started each of them afresh
  [D098].
- An edited room's entities are made again from its new settings, so a
  Precool button comes or goes with the presence sensor and the controls
  follow a changed unit. Registry entries are kept; that of an entity the
  room no longer has is removed [D098].
- Without an outdoor temperature sensor, or with one gone quiet, the
  forecast's value for the hour now stands in, so the room still learns
  [D099].
- A choice's 5-second save is no longer pushed out to 5 minutes by the
  model learning just after it [D064].
- The model's 5-minute and hourly steps run on a copy in the executor, so
  a save or a diagnostics download never reads a model being changed.
- A repair whose wording or figures change is updated; it had kept the
  first wording until cleared.
- Room screen 3: a comfort field emptied on Edit takes its default again,
  as on screen 1.

### Fixed (since step 7)

- A unit's setpoint limits and step are now read in the system's
  temperature unit and stored in C; on a Fahrenheit system they were
  stored in F while everything else works in C.

### Fixed

- The unit's lowest steady rate was learned from its lagged rate after
  each start, and so came out too low; it is now learned from the steady
  rate the unit is asked for, as "a rate held" requires.
- Below the curve's first effort the model now gives the first effort's
  rate, as an inverter that runs at all runs at least at its lowest rate.
- Observe: a unit that is off or in fan-only counts as not running, so the
  model learns on units that report no `hvac_action`; an outdoor unit's draw
  is also learned when a head goes from cool, heat or dry to off or fan-only
  (decisions of 27 October 2026).

### Changed

- A band whose low is not below its high is refused; a room whose heads'
  setpoint ranges do not overlap is refused at setup; a room with any
  range-only head is sent a range (decisions of 27 October 2026).
- The comfort index is T + 0.33 e - 4.00 from the room's measured
  temperature and humidity, with nothing added. The added terms for sun on
  the occupant (3.0), still air (1.0) and a heat source (1.0) are gone. The
  sun still counts, through the warmth the model learns and forecasts it
  brings (decision of 27 October 2026).
- Default bands are now 23-26 occupied, 20-23 sleep and 23-26 precool.
  They are the old defaults less the 1.0 still-air term that was always in
  them, so each band still means 22-24 C (sleep 19.5-22 C) at 60 % humidity.

- A compressor-frequency sensor is never taken as the unit's effort. The
  effort is always the unit's reading against its setpoint, which is what
  the component sends; the frequency sensor still answers first whether
  the compressor is running (decision of 28 October 2026).
- **An enforced room keeps cooling until the battery can't.** When even
  its lowest rate cannot last to the end of a no-grid-import window, the
  room cools at its lowest rate instead of being turned off after 10
  minutes. It is turned off only when the battery is down to its reserve,
  or cannot supply the lowest rate's draw, and runs again when the battery
  can supply it (decision of 29 October 2026). A unit whose draw is not yet
  learned has no ceiling until the battery reaches its reserve or the
  window ends (Jason, 6 November 2026).
- **The demand forecast runs to the next tariff change**, not 8 hours: the
  energy the rooms' plans will use until the rate, price or rules next
  change, what it is priced at, and when that is. Where the change is
  beyond the plans' six hours it runs to their end and says so. The tariff
  is read 24 hours ahead (decision of 29 October 2026).

### Removed

- The room's heat source and air mover settings. They existed only for the
  index terms removed above.

## 0.9.7 - 2026-10-15

From the living room on the night of 14–15 October, the owner's instructions of
15 October, and a root-and-branch review of the component: six simulations of
the real integration against a model room, checked against the live trace.

### Fixed

- **The compressor started and stopped at the edge of the band.** A room
  coasted as soon as it was anywhere inside its band, so the unit stopped at the
  band's edge and restarted, at its coldest setpoint, within a quarter of an
  hour. A room the unit is working on now coasts only once it reaches its
  target. In simulation, starts over three nights fell from 29 to 7, and in an
  Office working day from 11 to 3. (DR-098)
- **The unit changed between dry and cool every few minutes.** The cooling rate
  compared with drying was read at the unit's own setpoint, the one the
  component had just sent, so each command moved the next comparison; the
  living room changed mode thirteen times between 05:15 and 05:38 on 15
  October. The rate is now read at the room's target, and a route, once chosen,
  is kept for 20 minutes. (DR-090)
- **The room was heated the moment Sleep ended.** The first evaluation after a
  change of band used the new band in full, with no ramp. It is now decided
  again knowing the change. (DR-092)
- **A unit stayed in a mode whose reason had passed.** Inside its band, a unit
  heating a room past the middle of its band now goes to its fan, and a unit
  drying a room that no longer calls for it goes back to cooling. (DR-091)
- **A coast after heating put the unit in dry.** It now goes to the fan.
  (DR-093)
- **Before a room had learned, its setpoint swung each time it crossed its
  target.** The fallback loop now turns round only half a degree past target.
  (DR-094)
- **A unit whose sensor reads differently from the room took hours to
  correct.** The difference is now measured from the unit's own reading.
  (DR-095)
- **Inside the band the unit could be asked for far colder than needed, and
  the trim never corrected it.** Inside the band the unit's own thermostat now
  holds the room's target, and the trim is learned there; a pull-down far from
  target no longer winds it. (DR-097)
- **The band ramp lasted one evaluation.** It now lasts the hour. (DR-084)
- **The power trace printed "ceiling 99.0 C".** It says "no limit on the
  setpoint". (DR-096)

### Changed

- **A Comfort mode chosen by hand stays until you change it.** Not the
  schedule, not sunrise, not an empty room. Supersedes the 0.9.6 sunrise end.
  (DR-083)
- **A room coming out of Sleep is not heated up to its daytime band,** nor left
  cooling: its unit goes to its fan until the room is back in band. (DR-085)
- **The room loop's rate is the trend over 15 minutes,** and a setpoint sent
  stands until the loop moves three quarters of a step from it. (DR-086)
- **Presence and door or window sensors have no age limit.** (DR-087)
- **Switching Automatic control off turns the unit off,** once; after that the
  unit is yours. (DR-089)

### Added

- **A Leave button per room.** Turns the room's air conditioning off at once
  and keeps it off until someone comes back. An example script is in
  `docs/examples.md`. (DR-088)

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
