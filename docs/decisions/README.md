# Design rules

One file per rule of Appendix A of the design specification. A number no longer in Appendix A is marked superseded.

| Rule | What it says |
| --- | --- |
| [D000](D000.md) | The component decides what each room's air conditioning should be doing, from how the room should feel, and says why |
| [D001](D001.md) | The component works through any device that presents a Home Assistant climate entity, and reads the unit through it. A better driver improves what is learned but is never required |
| [D002](D002.md) | Decisions live in modules that import nothing from Home Assistant. The coordinator gathers inputs, calls the modules and carries out the result, and decides nothing |
| [D003](D003.md) | Superseded |
| [D004](D004.md) | The tariff lives in Abode Power Tariffs. The component reads it and holds none of it |
| [D005](D005.md) | One writer per actuator: the component writes to climate entities and covers only, never to a battery, and publishes a vendor-neutral demand forecast instead |
| [D006](D006.md) | Every room publishes, every cycle, what it is doing, the reasons, and every option not taken with the reason |
| [D007](D007.md) | There is no manual override: each cycle the component's decision is re-asserted against the unit's live state |
| [D008](D008.md) | Comfort is the constraint, not the variable: cost never narrows a room's band |
| [D009](D009.md) | One comfort definition per room: a band, low and high, in comfort index. The target temperature is solved from it at the room's humidity |
| [D010](D010.md) | The comfort index is Steadman's shade apparent temperature with wind at zero, from the room's measured temperature and humidity, with nothing added: the sun, people, appliances and air movement reach it only through the temperature and humidity they cause |
| [D011](D011.md) | Superseded |
| [D012](D012.md) | An unoccupied room is off |
| [D013](D013.md) | Precool drives an empty room to its precool band, from now or by a time the user gives, with no separate target, starting when the model says it must |
| [D014](D014.md) | A change of band moves the band steadily over one hour |
| [D015](D015.md) | Superseded |
| [D016](D016.md) | The compressor runs at least 10 minutes and rests at least 5, counted per outdoor unit |
| [D017](D017.md) | Stopping the unit (OFF) and leaving it alone (NONE) are separate decisions |
| [D018](D018.md) | A missing reading leaves the unit alone and names what is missing |
| [D019](D019.md) | Superseded |
| [D020](D020.md) | A room has one or more heads. Heads given the same outdoor-unit name share one compressor, in one room or across rooms |
| [D021](D021.md) | Lockout takes a room out of control, set by the user or forced when two rooms share a climate entity |
| [D022](D022.md) | The cheapest way to comfort is used, blinds included, and every option not taken is traced with the reason |
| [D023](D023.md) | Blinds act only when the sun is on the glass, judged from the sun's position and the window, not from light level |
| [D024](D024.md) | The unit is sent only what it advertised it can do |
| [D025](D025.md) | Whether the compressor ran is measured, never read from the unit's mode alone |
| [D026](D026.md) | Sensible and latent load are learned separately, each only from steps where it acts |
| [D027](D027.md) | Superseded |
| [D028](D028.md) | Electrical draw is learned per outdoor unit, at each effort, from steps in house load |
| [D029](D029.md) | Dry or cool is decided by forecasting both in comfort index |
| [D030](D030.md) | Every reading carries an age; one too old is treated as absent |
| [D031](D031.md) | What a room has learned is kept across restarts; the PID's integral is not |
| [D032](D032.md) | Superseded |
| [D033](D033.md) | Precool is not rationed by the power budget |
| [D034](D034.md) | Tariff constraints are declared in the tariff, are absolute, and are reported if unknown |
| [D035](D035.md) | Every decision looks for the cheapest way to deliver the band |
| [D036](D036.md) | Superseded |
| [D037](D037.md) | A no-grid-import window is met by a ceiling on the rate the unit may run at |
| [D038](D038.md) | The grid sensor's sign is settled at setup and never inferred at run time |
| [D039](D039.md) | Solar pays down the rest of the house first; the battery binds only on the shortfall |
| [D040](D040.md) | Superseded |
| [D041](D041.md) | Power management per room is off, guidance or enforced. Guidance shows the ceiling; enforced applies it |
| [D042](D042.md) | The battery's rated maximum discharge is a setup figure, not learned |
| [D043](D043.md) | A no-grid-import window's actual grid import is measured and reported afterwards |
| [D044](D044.md) | Power management fails open: any missing figure means no ceiling |
| [D045](D045.md) | A setting exists only if a correct result is impossible without it |
| [D046](D046.md) | A room needs a temperature and a humidity sensor |
| [D047](D047.md) | Superseded |
| [D048](D048.md) | What a room's units can do is read from them when the room is configured, and stored |
| [D049](D049.md) | Automatic control off stops the automation, and the room's controls are then the user's. Automatic vane control decides whether the component moves the vertical vane |
| [D050](D050.md) | An open door or window stops new starts and, after the room's grace and with spoken warnings, stops the unit |
| [D051](D051.md) | Setpoints sent stay within the unit's advertised range and step |
| [D052](D052.md) | Every mode or setpoint command the component sends is recorded |
| [D053](D053.md) | Superseded |
| [D054](D054.md) | The room loop is a PID, cascaded on the unit's own thermostat as the fast inner loop, with gains from what the room has learned, and no integral wind-up while its output is not delivered |
| [D055](D055.md) | The comfort index is never changed by what the component itself is doing |
| [D056](D056.md) | A rate above what the unit can deliver asks for the unit's top rate |
| [D057](D057.md) | Resting is the compressor off and the unit on its fan |
| [D058](D058.md) | Each room has a Comfort mode: Schedule, Sleep or Awake |
| [D059](D059.md) | Superseded |
| [D060](D060.md) | The moisture the coil gives back after a stop is learned in its own right |
| [D061](D061.md) | The power budget counts the day's forecast solar, from house-level forecast sources, and never draws the battery below the configured reserve |
| [D062](D062.md) | The power ceiling drops at once, and rises only after it has stood 10 minutes and the allowance clears the higher draw by 0.3 kW |
| [D063](D063.md) | Enforced power management may turn the unit off; guidance never does |
| [D064](D064.md) | Learned state is saved on one pending timer, the user's choices are saved at once, and everything is written when the component unloads |
| [D065](D065.md) | Superseded |
| [D066](D066.md) | A reading's age runs from the later of its last change and its last report |
| [D067](D067.md) | The power budget measures the rest of the house over 15 minutes. An enforced room that cannot last to the end of the window cools at its lowest rate until the battery cannot supply it; only then is it turned off |
| [D068](D068.md) | A room's occupancy is kept across a restart |
| [D069](D069.md) | Spoken warnings go through one text-to-speech engine, and a warning that fails never stops a cycle |
| [D070](D070.md) | A room's temperature and humidity are trusted until they are unavailable |
| [D071](D071.md) | A room speaks only while the component runs it and the room is awake |
| [D072](D072.md) | Superseded |
| [D073](D073.md) | Superseded by [D105](D105.md) |
| [D074](D074.md) | Where a room's sensor is unavailable, the unit's own reading stands in, and is never learned from |
| [D075](D075.md) | Superseded |
| [D076](D076.md) | Superseded |
| [D077](D077.md) | A warning is said only when it is true |
| [D078](D078.md) | Dry mode is sent with a setpoint |
| [D079](D079.md) | Superseded |
| [D080](D080.md) | Precool ends once the room is occupied, on Leave or cancel, or one hour after its ready time |
| [D081](D081.md) | The model learns from what the unit was actually sent, and from the setpoint the unit reports |
| [D082](D082.md) | An empty room waits for its warning only where a warning can be heard |
| [D083](D083.md) | A Comfort mode the user chooses stays until the user changes it |
| [D084](D084.md) | A change of band ramps across the whole hour |
| [D085](D085.md) | A room coming out of Sleep is not heated while it is waking |
| [D086](D086.md) | The room's rate of change used for control comes from the filter's estimate, not from the slope of the raw readings |
| [D087](D087.md) | Presence and door or window sensors have no age limit |
| [D088](D088.md) | Each room has a Leave button. It turns the room off at once, not held by the minimum run, until someone comes back; without a presence sensor, until the user changes Automatic control or Comfort mode |
| [D089](D089.md) | Switching Automatic control off turns the unit off, once |
| [D090](D090.md) | Superseded |
| [D091](D091.md) | Superseded |
| [D092](D092.md) | The cycle that sees a band change decides again at once, knowing it |
| [D093](D093.md) | Superseded |
| [D094](D094.md) | Superseded |
| [D095](D095.md) | The unit's own reading is what its thermostat acts on, and the unit's effort is measured against it |
| [D096](D096.md) | The power trace says "no limit" where none binds |
| [D097](D097.md) | Rooms on one outdoor unit are one draw on the battery: each room's allowance is less what the other rooms on its unit draw now, whatever their setting |
| [D098](D098.md) | An edit keeps what each of the room's modules remembers between cycles, and makes the room's entities again from the new settings |
| [D099](D099.md) | The forecast's value for the hour now stands in for a missing or too-old outdoor temperature or humidity, and the model learns from it |
| [D100](D100.md) | The unit's lowest steady rate is learned only from a 30-minute run held at or below the lowest rate offered then |
| [D101](D101.md) | Off or fan-only is not running. In any other mode the component watches the house power: a rise is the compressor starting, a fall back is it stopping |
| [D102](D102.md) | Never heat after a cooling cycle; never cool after a heating cycle. The cycle is the run: a running unit is not reversed, a room inside its band is never reversed, and after rest the plan reverses only when neither the rest forecast nor the observed half-hour trend is bringing the room back inside its band |
| [D103](D103.md) | A change of running kind is a start, and dry is offered only where the moisture model can price it |
| [D104](D104.md) | Constraints are facts about the equipment; comfort and economic judgements are priced by the model, never branched on |
| [D105](D105.md) | An opening is an input to the heat balance with a learned coupling; free cooling is the model's own counterfactual, with no hold and no at-once stop |
