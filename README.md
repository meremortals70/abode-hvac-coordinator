# Abode Power Tariffs

Enter your electricity plan once, in the Home Assistant interface, and stop
building it out of helpers.

If you are on a time-of-use plan, the usual way to get a working price into the
Energy dashboard is an `input_number` for every rate, two time helpers for every
time period, a workday sensor, a Jinja template sensor with a nested `if` chain, a
`utility_meter` with tariffs, and an automation for every time period boundary — then
all of it again for export. Change retailers and you rebuild the lot.

This replaces that. You describe the plan; it publishes the prices, the rate in
force, the rules in force, and the next 24 hours.

**It decides nothing.** No optimiser, no load scheduling, no "cheapest time period"
action. It answers questions and other things decide.

---

## What you get

| Entity | What it is |
|---|---|
| `sensor.<name>_import_price` | Price now, in currency per kWh. Goes straight into the Energy dashboard |
| `sensor.<name>_export_price` | Feed-in price now |
| `sensor.<name>_rate` | The rate in force, as `weekday.peak` — its timetable and its name |
| `sensor.<name>_next_rate_change` | When the import rate next changes |
| `sensor.<name>_next_export_change` | When the feed-in price next changes, if it moves during the day |
| `sensor.<name>_daily_supply_charge` | Your daily supply charge |
| `sensor.<name>_allowance_remaining` | kWh left of a capped period, when you have asked for it to be counted |
| `binary_sensor.<name>_<constraint>` | One per rule you declared — see below |
| `sensor.<name>_supply_charge_today` and `..._supply_charge_energy` | Optional pair that gets the daily supply charge into the Energy dashboard |

Plus an action, `abode_power_tariffs.get_intervals`, returning the forward
series.

---

## Installing

### HACS

1. HACS → three-dot menu → **Custom repositories**
2. Repository `meremortals70/abode-power-tariffs`, type **Integration**
3. Find **Abode Power Tariffs** in HACS and download it
4. Restart Home Assistant
5. **Settings → Devices & services → Add integration → Abode Power Tariffs**

### By hand

Copy `custom_components/abode_power_tariffs` into your `config/custom_components`
directory and restart, then add the integration as above.

### Setup

One field: a channel name. Use one entry per metering channel — general
consumption, controlled load, a second circuit. They are separate plans with
separate prices.

A working plan is created for you: a single rate at 30 c/kWh covering the whole
day. Nothing is broken while you edit it.

---

## Removing

Settings → Devices & services → Abode Power Tariffs → the three-dot menu on the
entry → **Delete**. Entities and the device go with it.

If you installed through HACS, remove it there as well, then restart.

Anything you pointed at it — Energy dashboard sources, automations triggering on
the constraint sensors, `utility_meter` selects it was driving — needs its own
tidying up. Nothing outside the integration is changed on removal.

---

## How a plan is put together

Four things, and the order matters:

| | |
|---|---|
| **The plan** | One meter or one circuit. Its name, and the charges that apply no matter what you use — daily supply charge, monthly fee, tax, demand charge |
| **A timetable** | A set of days that share the same prices. "Every day", or "Weekday" and "Weekend" and "Public holidays" |
| **A rate** | A named price, belonging to a timetable. The timetable's name is put in front of it automatically, so **Weekday Peak** and **Weekend Peak** are two rates at two prices |
| **A time period** | A span of the day, priced at one of that timetable's rates. Together they must cover the day exactly once |

Feed-in works the same way, per timetable: either one price all day, or its own
rates and its own time periods.

**A plan can have as many timetables as it needs.** Weekends priced differently
is not a second plan — it is a second timetable inside the same one.

---

## Setting it up

Settings → Devices & services → Add integration → Abode Power Tariffs.

**1. Plan name** and an optional description.

**2. Fixed charges** — daily supply charge, monthly fee, whether prices include
tax and at what rate, and the demand charge if you have one.

Then the timetable loop. Every timetable runs the same screens in the same
order:

**3. Timetable** — its name, and which days it covers. Leave "Every day is the
same" on unless weekends or public holidays are priced differently.

**4. Rates** — the prices on this timetable, and any rules that apply during
them. Call it `Peak` and it stays `Peak`: it belongs to the timetable you are
entering, so a weekday Peak and a weekend Peak can both be called Peak at
different prices. It is published as `weekday.peak`. Enter one and the screen
returns for the next; choose **Continue to the time periods** when they are all
in.

Allowances, fallback rates and demand periods are not asked for here. They have
sensible defaults and are set afterwards in Configure.

**5. Time periods** — start, end, and which rate. The start is included and the
end is not, so one period can end at 16:00 and the next begin at 16:00. A period
running to midnight ends at 00:00. Choose **Continue to feed-in** when the day
is covered.

**6. Feed-in** — one price all day, or not. Leave the switch on and enter the
price, and this timetable is done. Turn it off and you get:

- **6a. Feed-in rates** — same as step 4
- **6b. Feed-in time periods** — same as step 5, independent of the import ones

**7. Timetable complete** — two buttons. **Add a timetable for other days**
takes you back to step 3 for the next one. **Finish and create the plan** ends
setup.

### A worked example

A plan where weekends are cheaper and the weekend feed-in is better:

| Screen | Weekday pass | Weekend pass |
|---|---|---|
| Timetable | `Weekday`, Mon–Fri | `Weekend`, Sat, Sun, public holidays |
| Rates | Off Peak, Shoulder, Peak → `Weekday Off Peak`, `Weekday Shoulder`, `Weekday Peak` | Off Peak, Peak → `Weekend Off Peak`, `Weekend Peak` |
| Time periods | 00:00–06:00 Off Peak, 06:00–16:00 Shoulder, 16:00–21:00 Peak, 21:00–00:00 Shoulder | 00:00–16:00 Off Peak, 16:00–00:00 Peak |
| Feed-in | One price all day, 2.7 c | Switch off → `Weekend Daytime` 2.7 c, `Weekend Evening` 12 c → 00:00–16:00 Daytime, 16:00–00:00 Evening |
| Timetable complete | Add a timetable for other days | Finish |

Five rates, two timetables, one plan.

---

## Changing it afterwards

Settings → Devices & services → Abode Power Tariffs → **Configure**. Everything
entered at setup can be changed there; nothing requires deleting the plan.

The 24-hour strip is shown before you choose anything and again on every screen
that touches time periods, so a gap or an overlap is visible the moment you make
one. It is coloured by price, cheapest green through dearest red, with a legend
naming each colour:

```
Weekday
0           3           6           9           12          15          18          21  24
🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟥🟥🟥🟥🟥🟥🟥🟥🟥🟥🟨🟨🟨🟨🟨🟨

  Coverage: complete. 4 periods, no gaps, no overlaps.

Export: 2.70 c/kWh all day

🟩 Weekday Off Peak — 19.80 c/kWh
🟨 Weekday Shoulder — 32.10 c/kWh
🟥 Weekday Peak — 56.88 c/kWh
```

The Configure menu:

| | |
|---|---|
| **Rates** | Add, edit, remove. Renaming follows through into every time period that uses it |
| **Day patterns** | The timetables. Add, edit, duplicate or remove one, including its feed-in mode and its season dates |
| **Time periods** | Per timetable |
| **Feed-in** | Feed-in rates, and their time periods per timetable |
| **Supply charge, tax, plan dates, sensors** | The plan-wide settings |
| **Track usage by rate** | Utility meters — see below |
| **Rate plan card** | The plan laid out for transcribing into an inverter |

### The rate fields in full

Beyond the name and the price, a rate carries:

| Field | Notes |
|---|---|
| Timetable | Which timetable the rate belongs to. Rates are identified by the pair, so two timetables can each have a Peak |
| Information only rules | Your own words. Each becomes a binary sensor. See below |
| Enforceable rules | The same, but declared as part of what the rate means. See below |
| Coasting permitted | Tells other systems a room may drift during this rate |
| Demand charge period | Marks the period the demand charge is measured in. The monthly amount is not calculated |
| Daily energy allowance | Some plans give a period free only up to a cap. Zero for none |
| Rate beyond the allowance | Required when there is an allowance |

### Rate names

A rate is identified by its timetable and its name together. Type `Peak` on the
Weekday timetable and on the Weekend one and you have two rates, both called
Peak, at whatever prices you gave them. They are published as `weekday.peak` and
`weekend.peak`, and that is what the rate sensor reports and what a
`utility_meter` tariff should be called.

Plans created before this carry rates named `Weekday Peak` and the like, with no
timetable of their own. They keep those names and keep working. Nothing is
renamed underneath you.

### Seasons

A timetable can carry a date range, entered as `MM-DD`. It may cross the new
year — `11-01` to `03-31` is summer in the southern hemisphere. A seasonal
timetable wins over a year-round one on the dates it covers.

### Validity dates

When your retailer reprices, set **Plan valid to** on the old plan and add a new
plan for the new prices. Historical cost figures then stay correct against the
price that was actually in force.

---

## Constraints

A constraint is a rule you declare on a rate. Name it whatever you like —
`no_grid_import`, `grid_charge_battery`, `precool_opportunity`. Each becomes a
binary sensor that is on while a rate carrying it is in force.

This is what battery, hot water and EV automations should trigger on, instead of
a clock comparison. When the plan moves, they move with it and you edit nothing.

Note what they are: **rules you declared**, not decisions the integration made.

### Information only, or enforceable

Rules go in one of two lists on the rate.

**Information only** is a useful fact about the rate. A consuming system can
look at it and decide it does not care.

**Enforceable** says you are declaring the rule to be part of what the rate
means, so another system should treat it as a rule rather than a hint.

The difference is a declaration about your tariff, not an instruction. This
integration enforces nothing either way — it says which rules you meant as
rules, and the consuming system decides what to do about them.

Every rule appears in the `constraints` list as it always has. The enforceable
ones also appear in `enforceable_constraints`, and each binary sensor carries an
`enforceable` attribute. Rules on a plan created before this are all information
only until you say otherwise.

---

## Energy dashboard

**Grid consumption cost.** Settings → Dashboards → Energy → your grid
consumption source → **Use an entity with current price** → pick
`sensor.<name>_import_price`. No template sensor.

**Return to grid.** The same, with `sensor.<name>_export_price`.

**Daily supply charge.** The Energy dashboard has no field for a fixed daily
charge — it is one of its longest-running open requests. Turn on **Create the
supply charge pair** under Track usage by rate and you get two entities: an
accumulating cost and a matching token energy sensor. Add
`sensor.<name>_supply_charge_energy` as a second grid consumption source and
choose **use an entity tracking the total costs**, pointing at
`sensor.<name>_supply_charge_today`. The supply charge then appears in the
dashboard's figures.

**Usage split by rate.** Create a `utility_meter` helper with your rate names as
its tariffs. It makes one meter per rate plus a select naming which is in force.
Nominate that select under Track usage by rate and the integration sets it at each
time period boundary — the automation people normally write for this disappears.

That select is the only thing this integration ever writes, it is off by
default, and it writes only to selects you nominated.

---

## evcc

Both price sensors carry a `forecast` attribute holding the next 24 hours as
`{start, end, value}` with UTC timestamps, which is the shape evcc reads:

```yaml
tariffs:
  grid:
    type: custom
    forecast:
      source: homeassistant
      uri: http://homeassistant.local:8123
      token: your-long-lived-token
      entity: sensor.electricity_import_price
      attribute: forecast
```

---

## Rate plan card

Under Configure there is a **Rate plan card**: the plan laid out with buy and
sell prices per period, weekday and weekend separately, in the order an
inverter's own tariff screen asks for them.

Tesla, Sungrow, Fronius and GoodWe all have that screen, and the plan usually
gets typed into it a second time from a bill. This renders it from the same
source of truth. Transcription is manual — the integration makes no vendor calls
and holds no vendor credentials.

---

## The action

```yaml
action: abode_power_tariffs.get_intervals
data:
  config_entry_id: 01JABCDEF...
  hours: 12
  resolution_minutes: 30
response_variable: tariff
```

Returns:

```yaml
intervals:
  - start_time: "2026-08-14T16:00:00+10:00"
    end_time: "2026-08-14T16:30:00+10:00"
    duration: 30
    per_kwh: 0.584
    export_per_kwh: 0.043
    rate: weekday.peak
    constraints: [no_grid_import, precool_opportunity]
    enforceable_constraints: [no_grid_import]
    coasting_permitted: true
    allowance_kwh: null
    fallback_rate: null
    fallback_per_kwh: null
    day_pattern: Weekday
    forecast: false
```

`per_kwh` is in dollars and the field names match the core Amber Electric
integration, so anything already written against that shape works here.

Times are local, with the offset. On the day the clocks go back a wall-clock
time appears twice and the offsets tell the two apart; `duration` is always the
real length of the interval.

`rate` is the identifier — the timetable and the name. `constraints` is every
rule as it always was; `enforceable_constraints` is the subset declared as part
of what the rate means.

`allowance_kwh`, `fallback_rate` and `fallback_per_kwh` are the cap and what is
paid past it, published whether or not this integration is counting, so a
consumer can apply the rule itself.

`forecast` marks a value that was predicted rather than contracted. A static
plan is always `false`.

---

## Examples

**Charge the battery while energy is free**

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.electricity_precool_opportunity
    to: "on"
actions:
  - action: script.battery_grid_charge
```

**Run the house off the battery through the peak**

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.electricity_no_grid_import
    to: "on"
actions:
  - action: script.battery_self_powered
```

**Warn when the free allowance is nearly spent**

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.electricity_allowance_remaining
    below: 3
actions:
  - action: notify.persistent_notification
    data:
      message: "Under 3 kWh of the free allowance left."
```

**Delay the dishwasher until the cheapest rate**

```yaml
triggers:
  - trigger: state
    entity_id: sensor.electricity_rate
    to: cheap
conditions:
  - condition: state
    entity_id: input_boolean.dishwasher_waiting
    state: "on"
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.dishwasher
```

---

## How the data updates

Nothing is polled and nothing is fetched. The plan is stored in the config entry
and resolved locally.

The next boundary is computed and scheduled; when it fires, every entity updates
and the following one is scheduled. Both sides count: the import rate changing
and the feed-in price changing are separate boundaries and separate sensors, and
whichever comes first is what wakes the integration. There is also a midnight
trigger that resets the daily allowance and the supply-charge accumulator, and
an update whenever the nominated holiday or import energy sensor changes.

### Daylight saving

Time periods are wall-clock times, which is what you want: a peak period is
16:00 local on both sides of a transition, and your plan does not change because
the clocks did.

The horizon is wall clock too. Twenty-four hours from 18:00 means 18:00
tomorrow, which is 23 real hours on the day the clocks go forward and 25 on the
day they go back. Inside that, the series is walked in real instants, so every
interval is genuinely the length it says it is. The hour that does not exist is
not emitted; the hour that happens twice is emitted twice, with its two
different offsets. A rate covering 02:00 to 03:00 is in force for no time at all
on the short day and for two hours on the long one, because that is what
happened.

Everything is reported in local time, with the offset. Nothing is published in
UTC.

---

## Troubleshooting

**The price sensor is unavailable.** No time period resolves at this moment. Open
Configure and read the strip — there is a gap, or a day type that no day pattern
covers.

**A repair issue says the plan is not valid.** The plan was saved by an older
version and no longer passes validation. The issue lists every problem; open
Configure and fix them.

**Prices are held after the plan expired.** Deliberate. Removing the price
entity would stop Energy dashboard cost tracking without saying so, and you
would find it weeks later as a gap in a graph. Set up the new plan and the
repair issue clears.

**The utility meter select is not changing.** Three things to check: the select
is nominated under Track usage by rate; the tariff names on the meter exactly match
your rate names; and the log — a mismatch is logged along with the options the
select actually offers.

**Public holidays are being priced as ordinary days.** Nominate a holiday sensor
under the general settings. Without one the day option is not offered, and
holidays follow the calendar weekday.

**Nothing is counting my allowance.** Counting is off by default and separate
from the plan. Open Configure, then **Allowance counting**, switch it on and
nominate a meter. Your plan declares the cap and the fallback either way, and
publishes both, so a consuming system can apply the rule itself.

**The count does not match my bill.** It will not. It counts the meter you
nominated and resets at local midnight; your retailer meters and resets on their
own terms. It exists so the Energy dashboard stays roughly honest on capped
plans, not to reconcile a bill.

**A capped rate runs through midnight.** Configure will say so. A period cannot
cross midnight here, so a capped stretch from 22:00 to 02:00 is two periods
naming the same rate, and the count resets between them — that one stretch gets
its full allowance twice. Whether that is right depends on how your retailer
counts it, so it warns rather than refusing to save.

**The allowance reads high after a restart.** It should not — it is restored. If
it does, the import energy sensor was unavailable at the moment of restore. It
corrects itself at midnight.

**You want to see the whole state.** The entry's three-dot menu → **Download
diagnostics**. It contains the plan, the strip, every validation problem, what
is resolved right now with a trace of why, and the next 24 hours.

---

## Known limitations

- **Tiered and block rates are not supported.** First N kWh at one rate and the
  remainder at another. The rate model has room for it; it is not built.
- **Demand charges are a flag only.** A time period can be marked as one so a
  controller can avoid a coincident peak, but the $/kW/month is not computed.
  `perosb/power_max_tracker` already tracks the monthly maximum.
- **Dynamic tariffs are not connected yet.** The interface is shaped for it —
  the action's response matches Amber's — but no adapter is written. A static
  plan is the only price source today.
- **Public holidays are resolved for today only**, because a workday binary
  sensor reports one day at a time. Holidays inside the forward series are
  treated as ordinary days.
- **There is no dashboard card.** The strip lives in the configuration screens,
  where the question it answers is asked. On a dashboard, the rate and
  next-rate-change sensors already say what is in force.
- **No text or CSV import.** With rates defined once, a time period is three fields.
  Diagnostics renders the plan as CSV for backup and for support.
- **No bill reconciliation.** It reports the rate in force; it does not
  reproduce an invoice.

---

## Supported plans

Anything expressible as named rates over non-overlapping time periods:

- Flat rate
- Time of use, any number of rates
- Weekday, weekend and public holiday variation, as separate timetables in one plan
- Seasonal variation, including ranges wrapping the new year
- Free or discounted time periods, with or without a daily energy cap
- Separate import and feed-in prices, each with their own timetable and periods
- Controlled load and second circuits, as additional channels
- Plans with a validity period, so past costs stay right after a reprice

---

## Development

```bash
python3 -m unittest discover -s tests -p "test_*.py"
ruff check custom_components tests
mypy --config-file mypy.ini custom_components/abode_power_tariffs/plan.py
```

Seven modules import nothing from Home Assistant — `const`, `plan`, `validate`,
`intervals`, `allowance`, `strip`, `serialise` — and they hold every decision.
They are tested without Home Assistant installed, and a test fails the build if
one of them grows a `homeassistant` import.

`tests/test_attributes.py` parses the source and fails when an attribute is read
and never assigned, following base classes within the package. It exists because
exactly that bug shipped once, past both ruff and mypy, because the file cannot
be imported without Home Assistant.

Quality scale progress is tracked in
`custom_components/abode_power_tariffs/quality_scale.yaml` against all 54 rules.

---

## Licence

Apache 2.0. See `LICENSE` and `ATTRIBUTION.md`.
