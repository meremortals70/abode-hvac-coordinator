# Abode HVAC Coordinator

A Home Assistant integration that decides what your air conditioning should be
doing, room by room, and tells you exactly why.

**It never writes to your battery.**

> ### Status: v0.5.3 — not yet proven
> Everything described here is built and unit tested. None of it has run for
> long against real hardware. Treat your first week as a test and read
> [Known limitations](docs/known-limitations.md) before you rely on it.

---

## What problem this solves

A normal thermostat holds a temperature. That is the wrong target in a humid
climate, and it is the wrong *method* when you have blinds, a fan and a dry mode
sitting there costing nothing.

This does two things differently.

**You say how a room should feel, not what temperature it should be.** You set
a comfort band once. The controller works out what to actually ask for,
adjusting for humidity, sun through the glass, still air and equipment heat —
all the things that make a room hot without moving the number on a wall sensor.

**It spends the cheap things first.** Before the compressor runs it will close a
blind against the sun, or run the fan, or use dry mode if the problem is
humidity rather than heat. Every step it skips is recorded with the reason, so
you can see the decision rather than guess at it.

---

## Quick start

**1. Install it** — see [Installation](docs/installation.md). If you have never
added a HACS custom repository, that page walks through it click by click.

**2. Add the integration.** Settings → Devices & Services → Add Integration →
Abode HVAC Coordinator. It asks about your first room straight away.

**3. Fill in the room.** Only two fields are required: a name, and the air
conditioner. Everything else is optional and marked as such — add what you have.

**4. Set the comfort band.** It arrives pre-filled with sensible numbers. Change
them if you disagree.

That is a working room. Add more, and point it at your tariff, from **Configure** on the
integration afterwards.

---

## What you get, per room

| Entity | Shows |
|---|---|
| Mode | What the room is doing, and the full reasoning in its attributes |
| Comfort index | How the room actually feels, in HCI |
| Target dry bulb | What the air conditioner is being asked for |

Plus one for the whole house: **Demand forecast** — how much energy the air
conditioning expects to want over the next eight hours, for your battery
automations to read.

---

## The ideas behind it

**Comfort is a constraint, not a variable.** Cost never narrows your comfort
band. The tariff decides *when* to bank energy and *which* actuator to use —
never whether you get to be comfortable.

**An unoccupied room is off** — but not the instant someone steps out. Leaving
to sign for a delivery does not stop the compressor; the room waits, and only a
sustained absence shuts it down. Coming back cancels it.

**Cheapest actuator first.** Blinds, then fan, then dry, then compressor.

**Every decision is explained.** Both what it did and what it decided against.

---

## Documentation

**Start here**

| | |
|---|---|
| [Use cases](docs/use-cases.md) | Whether this suits your house at all |
| [Installation](docs/installation.md) | Installing and setting up, step by step |
| [Configuration](docs/configuration.md) | Every setting, and why there are so few |

**How it decides**

| | |
|---|---|
| [Comfort index](docs/comfort-index.md) | The scale, the maths, the band table |
| [Modes](docs/modes.md) | The seven modes and their precedence |
| [Actuator ordering](docs/actuator-ordering.md) | Cheapest first, in detail |
| [Behaviour](docs/behaviour.md) | What happens when a window opens, when coasting ends, and other flows |
| [Tariff](docs/tariff.md) | Reading the plan from Abode Power Tariffs, and what the constraints mean |
| [Regulation](docs/regulation.md) | Layer 2: why the commanded setpoint is not the solved target |
| [Free cooling](docs/free-cooling.md) | Dew point, when opening up helps, and condensation risk |
| [Stale feeds](docs/staleness.md) | Why a sensor that is reporting is not necessarily current |
| [Thermal model](docs/thermal-model.md) | What it learns about your house |
| [Demand forecast](docs/demand-forecast.md) | The battery contract, and why it stops there |

**Living with it**

| | |
|---|---|
| [Entities](docs/entities.md) | What it creates and what each reports |
| [Actions](docs/actions.md) | The two services |
| [Examples](docs/examples.md) | Working automations |
| [Troubleshooting](docs/troubleshooting.md) | When it does something unexpected |
| [Known limitations](docs/known-limitations.md) | What it does not do, stated plainly |

**Under the bonnet**

| | |
|---|---|
| [Architecture](docs/architecture.md) | The layer model and the settled decisions |
| [Data updates](docs/data-updates.md) | When it evaluates, and what triggers it |
| [Contributing](docs/contributing.md) | Tests, structure, quality scale |
| [As-built](AS-BUILT.md) | What was built, and where it differs from the design |
| [Handover](HANDOVER.md) | Context for picking this up cold |
| [Architecture gaps](ARCHITECTURE-GAPS.md) | What the architecture specifies and the code does not do |

---

## Requirements

- Home Assistant **2026.6.0** or later
- A `climate` entity per room
- Ideally a temperature and humidity sensor per room — without both, that room
  reports no comfort index and is never actuated

Presence, blinds, window sensors, a sleep schedule and an outdoor temperature
feed each unlock behaviour if you have them, and are optional if you do not.

No external dependencies. No network calls. No cloud account.

## Licence

Apache 2.0. See [ATTRIBUTION.md](ATTRIBUTION.md).
