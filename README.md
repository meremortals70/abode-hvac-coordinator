# Abode HVAC Coordinator

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]](LICENSE)
[![Project Maintenance][maintenance-shield]][repository]
[![Quality Scale][quality-shield]][quality-scale]
[![CI][ci-shield]][ci]
[![Home Assistant Marketplace][marketplace-shield]][marketplace]

A Home Assistant integration that runs each room's air conditioning from how the room should feel.

It learns a model of each room from the room's own readings. Every 5 minutes it plans the next six hours on that model, choosing the cheapest way to keep the room near the centre of its comfort band. Every 30 seconds it holds the room on that plan. Every decision says why, on the room's mode sensor.

## Install

Home Assistant 2026.11 ships HACS as the built-in **Marketplace**, under **Settings**. Before 2026.11, install through HACS.

| Your Home Assistant | Install from |
| --- | --- |
| 2026.11 or later | **Settings → Marketplace** |
| 2026.10 | **HACS → Integrations** |
| any | copy `custom_components/abode_hvac_coordinator` by hand |

[![Open your Home Assistant instance and open the repository in the Marketplace.][marketplace-badge]][marketplace]
[![Open your Home Assistant instance and start setting up the integration.][setup-badge]][setup]

The first button opens this repository in your own Home Assistant, in the Marketplace (HACS before 2026.11), and offers to add it as a custom repository. The second starts the integration's setup once it is installed. On Home Assistant 2026.11 a first install with a config flow opens its setup page straight away, so no restart is needed; after an update, a repair offers the restart.

Every step, with the optional sensors: [installation](docs/installation.md).

## What it connects to

| It reads | It writes |
| --- | --- |
| Each room's air conditioners (any Home Assistant climate entity) | The air conditioners: mode, setpoint, fan speed, vertical vane |
| Each room's temperature and humidity sensors | Each room's blinds (covers), if you allow it |
| Presence, sleep schedule, door and window sensors, if you have them | Spoken warnings, through a speech engine you choose |
| The tariff, from [Abode Power Tariffs](https://github.com/meremortals70/abode-power-tariffs) | |
| The hourly weather forecast, and outdoor sensors | |
| The house's battery, solar, house load and grid sensors | |

It never writes to a battery. It publishes a demand forecast instead, for whatever runs the battery.

## What it decides

- **The band in force:** occupied, sleep or precool, or off for an empty room or after Leave.
- **The plan:** rest, dry, cool or heat, and how hard, for each half-hour of the next six hours. Comfort comes first: no saving is allowed to take the room out of its band.
- **What the unit is sent:** the setpoint the unit needs, from what it has learned about the unit.
- **What may reach the unit:** your controls, open doors and windows, the compressor's minimum run and rest, tariff rules and the house's power budget.

## Quick start

1. Install it (see [above](#install) or [installation](docs/installation.md)), then restart Home Assistant only if it asks.
2. **Settings → Devices & services → Add integration → Abode HVAC Coordinator.**
3. Answer the house screens. Every one can be left empty and filled in later with Reconfigure.
4. Add the first room: its air conditioners, its temperature and humidity sensors, then its comfort band.
5. Open the room's device. Its mode sensor shows what the room is doing and why.

## What each room gets

A device with:

- a **mode sensor** (the band rule in force, the occupancy grace it is in, with the full decision trace as attributes)
- the **comfort index**, the **target temperature** and the **dew point**
- yesterday's **comfort in band**, **near the centre**, **starts per running hour** and **cost of comfort**
- **Automatic control** and **Automatic vane control** switches
- **Comfort mode** (Schedule, Sleep, Awake), and the unit's own mode, fan speed and vane controls
- the unit's **setpoint**
- **Leave** and, in a room with a presence sensor, **Precool** buttons

The full list is in [entities](docs/entities.md).

## Requirements

- Home Assistant 2026.10.4 or later.
- The recorder, for seeding a new room from its history.
- For each room: at least one air conditioner, and its own temperature and humidity sensors. The air conditioner's own reading is not good enough on its own, because it sits in the unit's airflow.
- Optional: [Abode Power Tariffs](https://github.com/meremortals70/abode-power-tariffs) for prices, a weather entity with an hourly forecast, and the house's power sensors for the power budget.

### Which air conditioners work

Any air conditioner that Home Assistant shows as a climate entity, through any integration, as long as it:

- reports its state and accepts `climate.set_hvac_mode` and `climate.set_temperature`;
- advertises its lowest and highest setpoint.

The component reads what each unit can do when the room is saved (modes, fan speeds, vane positions, setpoint range and step) and sends only that. A unit that reports `hvac_action`, or has a compressor frequency sensor, lets the component see when its compressor runs, which helps it learn. Neither is required.

A ducted system with one indoor unit and zone dampers cannot be driven, because the component controls one unit per room.

## More

- [Use cases](docs/use-cases.md)
- [Installation and removal](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Entities](docs/entities.md)
- [Actions](docs/actions.md)
- [How often it updates](docs/data-updates.md)
- [Examples](docs/examples.md)
- [Known limitations](docs/known-limitations.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/architecture.md) and [design rules](docs/decisions/)

## Licence

See [LICENSE](LICENSE) and [ATTRIBUTION.md](ATTRIBUTION.md).

[releases-shield]: https://img.shields.io/github/v/release/meremortals70/abode-hvac-coordinator?sort=semver
[releases]: https://github.com/meremortals70/abode-hvac-coordinator/releases
[license-shield]: https://img.shields.io/github/license/meremortals70/abode-hvac-coordinator.svg
[maintenance-shield]: https://img.shields.io/maintenance/yes/2026.svg
[repository]: https://github.com/meremortals70/abode-hvac-coordinator
[quality-shield]: https://img.shields.io/badge/quality%20scale-platinum-4c1
[quality-scale]: https://developers.home-assistant.io/docs/core/integration-quality-scale/
[ci-shield]: https://github.com/meremortals70/abode-hvac-coordinator/actions/workflows/ci.yml/badge.svg
[ci]: https://github.com/meremortals70/abode-hvac-coordinator/actions/workflows/ci.yml
[marketplace-shield]: https://img.shields.io/badge/Marketplace-custom%20repository-18bcf2
[marketplace-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[marketplace]: https://my.home-assistant.io/redirect/hacs_repository/?owner=meremortals70&repository=abode-hvac-coordinator&category=integration
[setup-badge]: https://my.home-assistant.io/badges/config_flow_start.svg
[setup]: https://my.home-assistant.io/redirect/config_flow_start/?domain=abode_hvac_coordinator
