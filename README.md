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
| `binary_sensor.<name>_demand_period_active` | On while the rate in force carries a demand charge. Only created when a rate has one |

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
| **The plan** | One meter or one circuit. Its name, and the charges that apply no matter what you use — daily supply charge, monthly fee, tax |
| **A timetable** | A set of days that share the same prices. "Every day", or "Weekday" and "Weekend" and "Public holidays" |
| **A rate** | A named price, belonging to a timetable. It is identified by the pair, so a Peak on the Weekday timetable and a Peak on the Weekend one are two rates at two prices, published as `weekday.peak` and `weekend.peak` |
| **A time period** | A span of the day, priced at one of that timetable's rates. Together they must cover the day exactly once |

Feed-in works the same way, per timetable: either one price all day, or its own
rates and its own time periods.

**A plan can have as many timetables as it needs.** Weekends priced differently
is not a second plan — it is a second timetable inside the same one.

---

## Setting it up

Settings → Devices & services → Add integration → Abode Power Tariffs.

**1. Plan name**, an optional description, and two questions that decide the
shape of everything that follows:

- **Is this power plan based on a single rate?** — for a plan with no clock
  in it at all: an amount for the first part of your usage, then a different
  price for the rest of the billing period. Tick this and setup skips
  straight to step 3 below; there are no timetables, rates or time periods
  to enter.
- **You export power to the grid** — turns the feed-in screens on. Leave it
  off and nothing about export is asked, on this plan or any timetable in it.

Leave both off for an ordinary time-of-use or flat-rate plan and the rest of
setup runs as before.

**2. Fixed charges** — daily supply charge, monthly fee, whether prices
include tax and at what rate, and the day of the month your billing cycle
starts.

**If "Is this power plan based on a single rate?" was ticked**, this is
followed by one more screen instead of the timetable loop below:

**3. Prices** — the price for the first part of your usage, the allowance in
kWh, and the price for the rest. If export was also switched on, the same
three questions for export. This is declared exactly like an allowance on any
other rate: nothing is counted against it yet. Submitting this screen finishes
setup.

**Otherwise, the timetable loop runs**, the same screens in the same order for
every timetable:

**3. Timetable** — its name, and which days it covers. Leave "Every day is the
same" on unless weekends or public holidays are priced differently.

**4. Rates** — the prices on this timetable, and any rules that apply during
them. Call it `Peak` and it stays `Peak`: it belongs to the timetable you are
entering, so a weekday Peak and a weekend Peak can both be called Peak at
different prices. It is published as `weekday.peak`.

The screen opens on the two things every rate has — its name and its price.
Everything else is in a section you open only if it applies:

- **Demand charging** — whether a demand charge applies while this rate is in
  force, and what it is
- **Allowance** — whether the rate is capped, and what energy costs past the
  cap
- **Constraints** — the rules you are declaring about this rate

All three start closed, so a rate with none of these things is a name and a
price. Nothing inside them is required.

Enter a rate and the screen returns for the next. Choose **Continue to the
time periods** when they are all in — anything filled in on the screen is
checked and kept on the way out, so a rate typed in and then continued from
is not lost. Continue from a blank screen and nothing is added.

**5. Time periods** — start, end, and which rate. Only the rates belonging to
this timetable are offered, shown by their published identifier, so
`weekday.peak` cannot be allocated on the weekend. If the timetable has no
rates yet the rate screen opens first.

The start is included and the end is not, so one period can end at 16:00 and
the next begin at 16:00. A period running to midnight ends at 00:00.

**Continue to feed-in** is a gate, not a preference: it moves on only once
every minute of the day is accounted for. Until then the screen says how much
is still uncovered and stays where it is. A plan with a gap in it is not a
valid plan.

**6. Feed-in** — shown only if "You export power to the grid" was ticked on
step 1. One price all day, or not. Leave the switch on and enter the price,
the export allowance if there is one, and what you are paid past it; this
timetable is then done. Turn it off and you get:

- **6a. Feed-in rates** — a name and a price, and the same two questions: the
  allowance on that feed-in price and what is paid once it is spent
- **6b. Feed-in time periods** — same as step 5, independent of the import
  ones, and gated the same way

**7. Timetable complete** — two buttons. **Add a timetable for other days**
takes you back to step 3 for the next one. **Finish and create the plan** ends
setup.

### A worked example

A plan where weekends are cheaper and the weekend feed-in is better:

| Screen | Weekday pass | Weekend pass |
|---|---|---|
| Timetable | `Weekday`, Mon–Fri | `Weekend`, Sat, Sun, public holidays |
| Rates | Off Peak, Shoulder, Peak → `weekday.off_peak`, `weekday.shoulder`, `weekday.peak` | Off Peak, Peak → `weekend.off_peak`, `weekend.peak` |
| Time periods | 00:00–06:00 Off Peak, 06:00–16:00 Shoulder, 16:00–21:00 Peak, 21:00–00:00 Shoulder | 00:00–16:00 Off Peak, 16:00–00:00 Peak |
| Feed-in | One price all day, 2.7 c | Switch off → `Weekend Daytime` 2.7 c, `Weekend Evening` 12 c → 00:00–16:00 Daytime, 16:00–00:00 Evening. Feed-in rates are not scoped to a timetable; they carry its name in front |
| Timetable complete | Add a timetable for other days | Finish |

Five rates, two timetables, one plan.

---

## Changing it afterwards

Settings → Devices & services → Abode Power Tariffs → **Configure**. Everything
entered at setup can be changed there; nothing requires deleting the plan.

The plan is shown before you choose anything and again on every screen that
touches time periods, so a gap or an overlap is visible the moment you make
one. It is one table per timetable — the time span, the name as you typed it,
the identifier it is published under, and the price:

```
Weekday
  00:00-06:00  Off Peak  weekday.off_peak    19.80 c/kWh
  06:00-16:00  Shoulder  weekday.shoulder    32.10 c/kWh
  16:00-21:00  Peak      weekday.peak        56.88 c/kWh
  21:00-24:00  Shoulder  weekday.shoulder    32.10 c/kWh
  Coverage: complete. 4 periods, no gaps, no overlaps.
  Feed-in: 2.70 c/kWh all day
```

There is no chart and no colour bar. A configuration dialog renders markdown,
and a bar drawn from characters cannot be made to line up with a clock across
every font and platform. The picture is a **history-graph** card on the rate
sensor — see below.

The Configure menu:

| | |
|---|---|
| **Rates** | Add, edit, remove. Renaming follows through into every time period that uses it |
| **Day patterns** | The timetables. Add, edit, duplicate or remove one, including its feed-in mode and its season dates. Renaming a timetable takes its rates with it — a rename is only a rename |
| **Time periods** | Per timetable, and offering only that timetable's rates |
| **Feed-in** | Feed-in rates with their allowances, and their time periods per timetable |
| **Supply charge, tax, plan dates, sensors** | The plan-wide settings |
| **Track usage by rate** | Utility meters — see below |
| **Rate plan card** | The plan laid out for transcribing into an inverter |

### The rate fields in full

Beyond the name and the price, a rate carries the following. Everything here
sits in one of the three sections on the rate screen, all of which start
closed:

| Field | Section | Notes |
|---|---|---|
| Timetable | — | Which timetable the rate belongs to. Rates are identified by the pair, so two timetables can each have a Peak |
| Demand charge period | Demand charging | Marks this rate as carrying a demand charge, and turns on `binary_sensor.<name>_demand_period_active` while it is in force |
| Demand charge ($/kW/month) | Demand charging | Declared alongside the flag. Published on every interval; the monthly amount is not calculated |
| Energy allowance for this rate | Allowance | Some plans give a period free only up to a cap. Zero for none. The allowance belongs to the time slot, not the day |
| Rate beyond the allowance | Allowance | Required when there is an allowance |
| Count usage against this allowance | Allowance | Marked *not yet implemented* — see Known limitations |
| Energy sensor to count | Allowance | Marked *not yet implemented* |
| Information only rules | Constraints | Your own words. Each becomes a binary sensor. See below |
| Enforceable rules | Constraints | The same, but declared as part of what the rate means. See below |

**Coasting permitted is a rule, not a field of its own.** It says the same
kind of thing the other rules say — something another system may act on while
this rate is in force — so it is declared in the two rule lists like any
other, and `coasting_permitted` is published as true only where you declared
it.

**The export allowance is not on an import rate.** Import and export are
separate flows with separate rates and separate periods, so the cap on a
feed-in price and what is paid past it are declared beside that feed-in
price: on the feed-in rate where export is priced by period, and on the
timetable where it is one price all day.

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
charge — it is one of its longest-running open requests. This integration
declares the charge as `sensor.<name>_daily_supply_charge` and stops there: it
states what the charge is, and does not keep a running total against it.

If you want that total, build it yourself with a `utility_meter` helper against
the declared figure. That keeps the billing cycle where it belongs — plans
rarely start on the first of the month, and owning the cycle would make this an
accounting system rather than a statement of what your plan says.

**Usage split by rate.** Create a `utility_meter` helper with your rate names as
its tariffs. It makes one meter per rate plus a select naming which is in force.
Nominate that select under Track usage by rate and the integration sets it at each
time period boundary — the automation people normally write for this disappears.

That select is the only thing this integration ever writes, it is off by
default, and it writes only to selects you nominated.

---

## evcc

Both price sensors carry a `forecast` attribute holding the next 24 hours as
`{start, end, value}`, which is the shape evcc reads. The timestamps are local
with the offset, like everything else this integration publishes — the offset
is what makes an instant unambiguous on the morning the clocks go back:

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

### Seeing the plan as a picture

Configure shows the plan as text — every period, what it costs, and whether the
day is covered. There is deliberately no chart in there: a config dialog renders
markdown, and a bar drawn from characters cannot be made to line up with a clock
across every font and platform.

Home Assistant already draws it properly. `sensor.<name>_rate` is an enum, so a
built-in **history-graph** card renders it as a coloured timeline with a real
time axis, one band per rate:

```yaml
type: vertical-stack
cards:
  - type: history-graph
    title: Tariff
    hours_to_show: 24
    entities:
      - entity: sensor.<name>_rate
        name: Rate

  - type: history-graph
    hours_to_show: 24
    entities:
      - entity: sensor.<name>_import_price
        name: Import
      - entity: sensor.<name>_export_price
        name: Feed-in

  - type: entities
    entities:
      - sensor.<name>_import_price
      - sensor.<name>_export_price
      - sensor.<name>_rate
      - sensor.<name>_next_rate_change
      - sensor.<name>_daily_supply_charge
```

Replace `<name>` with your plan's slug. The first card is the coloured bar; the
second plots the two prices over the same period. History is the last 24 hours,
so a plan set up today fills in as the day goes.

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
    constraints: [coasting_permitted, no_grid_import, precool_opportunity]
    enforceable_constraints: [no_grid_import]
    coasting_permitted: true
    allowance_kwh: null
    fallback_rate: null
    fallback_per_kwh: null
    day_pattern: Weekday
    demand_period: false
    demand_rate_per_kw_month: 0.0
    export_allowance_kwh: null
    export_fallback_price: null
    forecast: false
```

`per_kwh` is in dollars and the field names match the core Amber Electric
integration, so anything already written against that shape works here.

Times are local, with the offset. On the day the clocks go back a wall-clock
time appears twice and the offsets tell the two apart; `duration` is always the
real length of the interval.

`rate` is the identifier — the timetable and the name. `constraints` is every
rule as it always was; `enforceable_constraints` is the subset declared as part
of what the rate means. `coasting_permitted` is one of those rules, published
as its own field for the consumers that already read it, and true only where
you declared it.

`demand_period` and `demand_rate_per_kw_month` are declared, not applied —
`per_kwh` is always just the energy rate. A consumer wanting the real cost of
drawing power in this interval applies its own assumption about kW draw to
the demand rate itself. `export_allowance_kwh` and `export_fallback_price` are
the same idea for feed-in: declared facts about an allowance and what follows
it, never blended into `export_per_kwh`. They follow the feed-in price in
force at that moment, not the import rate running alongside it, so on a
timetable with feed-in periods they change when the feed-in rate changes.

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
whichever comes first is what wakes the integration.

Everything is also recomputed on the zero second of every minute. Time periods
are whole minutes, so that tick lands on each boundary exactly, and no single
scheduled instant is load-bearing — a mistake costs a minute rather than hours.
A recompute that changes nothing writes nothing. There is also an update
whenever the nominated holiday or import energy sensor changes.

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

The same applies to the sensors, not just the forecast. On the morning the
clocks go back, a plan with a boundary inside the repeated hour changes rate,
changes back, and changes again — because that hour genuinely happens twice.
A boundary is stored as minutes past midnight, which on that one morning names
two real instants an hour apart, so both are built and the nearer one ahead is
taken.

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

**Nothing is counting my allowance.** Counting is marked *not yet
implemented* on the rate screen while how an allowance should accumulate is
still being decided. The cap and the fallback are declared and published
either way, on the rate and on every interval, so a consuming system can
apply the rule itself.

**The count does not match my bill.** It will not. It counts the meter you
nominated, and the count belongs to the time slot — each occurrence of a
capped period has its own, and nothing carries between slots, days or billing
cycles. Your retailer meters and resets on their own terms.

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

- **Tiered and block rates are supported only without a timetable.** A plan
  with "Is this power plan based on a single rate?" ticked gets a price for
  the first part of usage and a different price for the rest, on both import
  and export. A timetabled rate does not yet support a second tier.
- **Nothing is counted against an allowance or a demand charge.** Every one of
  them is declared and published — the cap, the price past it and the demand
  rate — but nothing switches automatically once one is spent, and the two
  counting fields on the rate screen are labelled *not yet implemented* while
  how an allowance should accumulate is decided. `perosb/power_max_tracker`
  already tracks a monthly maximum for demand.
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
- Free or discounted time periods, with or without an energy cap on the slot
- Separate import and feed-in prices, each with their own timetable and
  periods, and each with their own cap and price past it
- A no-timetable plan: one price for the first part of usage, another for the
  rest of the billing period, for import and export alike
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
