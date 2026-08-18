# Home Assistant developer rules and the platinum standard

**Compiled:** 14 August 2026
**Sources:** the rule set in `script/hassfest/quality_scale.py` in Home Assistant
core, and the rule pages under `docs/core/integration-quality-scale/rules/` in
the developer documentation. Read from source, not from memory.

This is the standard `abode_power_tariffs` is written to. Section 4 records
where it stands against every rule.

---

## 1. The tiers

Four scaled tiers. **A tier requires every rule of that tier and of all tiers
below it.**

| Tier | What it means |
|---|---|
| 🥉 **Bronze** | The baseline required of every new integration. Sets up through the UI, follows basic coding standards, has automated tests that it can be configured correctly, and has end-user documentation good enough to get started |
| 🥈 **Silver** | A solid runtime. Handles errors, offline devices and failed authentication. Recovers automatically without filling the log. Has an active code owner. Documents what it provides and how to troubleshoot it |
| 🥇 **Gold** | The best end-user experience. Discovery where possible, reconfigurable, fully translated, entities categorised and named logically, extensive end-user documentation, diagnostics, and full automated test coverage. Required for anything in the Works with Home Assistant programme |
| 🏆 **Platinum** | Technical excellence on top of gold. Fully typed with clear comments, fully asynchronous, and efficient in its data handling |

There are also special tiers for things that do not fit the scale. **A custom
integration is one of them: it cannot be graded, and Custom is its own tier.**
The rules are still the standard.

Progress is recorded in `quality_scale.yaml` inside the integration, with a
status of `done`, `todo`, or `exempt` plus a reason.

---

## 2. The 54 rules

Counts are exact: 20 bronze, 10 silver, 21 gold, 3 platinum.

### 🥉 Bronze — 20 rules

| Rule | What it demands |
|---|---|
| `action-setup` | Service actions are registered in `async_setup`, not `async_setup_entry`, so automations using them validate even when no entry is loaded. Validation happens inside the action and raises `ServiceValidationError` |
| `appropriate-polling` | If it polls, the interval suits the data. Exempt if it does not poll |
| `brands` | The integration has an icon and a logo in the brands repository |
| `common-modules` | The coordinator lives in `coordinator.py`, the base entity in `entity.py` |
| `config-flow` | Set up through the UI. Connection details in `data`, everything else in `options` |
| `config-flow-test-coverage` | The config flow is fully covered by tests |
| `dependency-transparency` | Dependencies are open source, published, versioned and built from source in CI |
| `docs-actions` | Every action is documented |
| `docs-conditions` | Every condition is documented |
| `docs-high-level-description` | The documentation says what the integration is for |
| `docs-installation-instructions` | Step-by-step setup |
| `docs-removal-instructions` | How to remove it cleanly |
| `docs-triggers` | Every trigger is documented |
| `entity-event-setup` | Subscribe in `async_added_to_hass`, unsubscribe in `async_will_remove_from_hass`. Earlier is too early: `self.hass` does not exist yet |
| `entity-unique-id` | Every entity has one |
| `has-entity-name` | `_attr_has_entity_name = True`, so entity names read as "Device Name Entity Name" |
| `runtime-data` | Runtime state goes in `ConfigEntry.runtime_data`, with the entry type parameterised so the type is checked |
| `test-before-configure` | The config flow tests the connection before creating the entry |
| `test-before-setup` | Setup raises `ConfigEntryNotReady`, `ConfigEntryAuthFailed` or `ConfigEntryError` as appropriate |
| `unique-config-entry` | The same thing cannot be added twice |

### 🥈 Silver — 10 rules

| Rule | What it demands |
|---|---|
| `action-exceptions` | Actions raise `ServiceValidationError` for bad input and `HomeAssistantError` for anything else |
| `config-entry-unloading` | The entry unloads cleanly, releasing every subscription |
| `docs-configuration-parameters` | Every option is documented |
| `docs-installation-parameters` | Every setup field is documented |
| `entity-unavailable` | Entities mark themselves unavailable when the data cannot be trusted |
| `integration-owner` | At least one code owner in the manifest |
| `log-when-unavailable` | Log once when it goes away and once when it comes back. Not every cycle |
| `parallel-updates` | `PARALLEL_UPDATES` is set explicitly on every platform, even to `0` |
| `reauthentication-flow` | Failed credentials trigger a reauth flow |
| `test-coverage` | Above 95% coverage on every module |

### 🥇 Gold — 21 rules

| Rule | What it demands |
|---|---|
| `devices` | Entities are grouped into devices |
| `diagnostics` | A diagnostics download, with secrets redacted |
| `discovery` | Devices are discovered where the protocol allows |
| `discovery-update-info` | Discovery updates network details for known devices |
| `docs-data-update` | The documentation says how data is fetched |
| `docs-examples` | Example automations and dashboards |
| `docs-known-limitations` | What it cannot do |
| `docs-supported-devices` | Which devices work |
| `docs-supported-functions` | What the entities are |
| `docs-troubleshooting` | What to do when it goes wrong |
| `docs-use-cases` | What people use it for |
| `dynamic-devices` | Devices appearing later are added automatically |
| `entity-category` | Entities are categorised where they are not primary |
| `entity-device-class` | Device classes are used where they apply |
| `entity-disabled-by-default` | Noisy or rarely used entities are off by default |
| `entity-translations` | Entity names come from translations |
| `exception-translations` | Exception messages come from translations. The exception inherits `HomeAssistantError` and carries `translation_domain` and `translation_key` |
| `icon-translations` | Icons come from `icons.json`, not from state. Do not override an icon the device class already provides |
| `reconfiguration-flow` | Settings can be changed after setup without removing the entry |
| `repair-issues` | Problems needing user action raise repair issues |
| `stale-devices` | Devices that go away are removed |

### 🏆 Platinum — 3 rules

| Rule | What it demands |
|---|---|
| `async-dependency` | The library the integration depends on is itself asynchronous, so no work is pushed to an executor |
| `inject-websession` | The integration passes Home Assistant's shared web session into its library rather than creating its own |
| `strict-typing` | Fully typed, passing mypy's strict profile. If a dependency ships type hints, it is marked in `.strict-typing` |

**Platinum is only three rules, and two of them are about a network library.**
An integration with no library and no network requests reaches platinum by
being strictly typed and by being honest about the exemptions — which is
exactly what the `quality_scale.yaml` comments are for.

---

## 3. The rules that shape code, not documentation

These are the ones that change how the integration is written.

**`runtime-data`.** Do not stash state in `hass.data[DOMAIN][entry_id]`. Use
`entry.runtime_data`, and parameterise the entry type so the checker knows what
is in it:

```python
type TariffConfigEntry = ConfigEntry[TariffCoordinator]
```

**`action-setup`.** A service registered in `async_setup_entry` disappears with
the entry, and an automation calling it cannot be validated. Register in
`async_setup` and look the entry up inside the action, raising
`ServiceValidationError` when it is missing or not loaded.

**`entity-event-setup`.** Subscribing in `__init__` fails, because `self.hass`
does not exist until the entity platform has registered the entity. Subscribe
in `async_added_to_hass` and release through `self.async_on_remove`.

**`parallel-updates`.** Explicit on every platform module. `0` means no limit
and is the right answer when nothing is polled — but it has to be written down,
because silence means the default rather than a decision.

**`log-when-unavailable`.** Keep a flag. Log a warning on the transition to
unavailable and an info on the transition back. A log line every thirty seconds
for a week is how a log becomes unreadable.

**`exception-translations` and `icon-translations`.** Message text lives in
`strings.json`; icons live in `icons.json`. Neither belongs in Python. An icon
that the device class already provides should not be overridden, or the same
concept looks different in two integrations.

**`common-modules`.** `coordinator.py` and `entity.py` are not suggestions.
Someone reading an unfamiliar integration should find both without looking.

---

## 4. Where `abode_power_tariffs` stands

Tracked in full in `custom_components/abode_power_tariffs/quality_scale.yaml`.

**38 done, 13 exempt with reasons, 3 todo.**

The three outstanding are all the same thing: `config-flow-test-coverage`,
`test-coverage` and `brands`. The first two need
`pytest-homeassistant-custom-component`, which needs Home Assistant installed.
The third is written but the HACS store listing will show no icon regardless,
because HACS fetches from its own CDN and does not fall back to the local
brands API — HACS issue #5171, open.

The exemptions worth reading, because they are decisions rather than gaps:

| Rule | Exempt because |
|---|---|
| `appropriate-polling` | Nothing is polled. The next window boundary is scheduled and recomputed on firing |
| `test-before-configure` | There is nothing to connect to. The plan is validated instead, and an invalid one cannot be saved |
| `reauthentication-flow` | There is no authentication |
| `discovery`, `discovery-update-info` | A tariff plan cannot be discovered |
| `entity-category` | Every entity is primary. Prices, the rate and the constraints are the point of the integration, not diagnostics about it |
| `entity-disabled-by-default` | Nothing is noisy. The supply-charge pair is created only when asked for, rather than created and disabled |
| `inject-websession` | No network requests at all |

And one rule answered in a way that looks like a violation and is not:

**`entity-device-class` on the price sensors.** They deliberately carry no
device class. There is none for a unit price, and `monetary` means an amount of
money rather than a rate. The Energy dashboard matches a price entity on its
unit — the configured currency over kWh — which is what the sensors publish.

---

## 5. Beyond the scale

Rules that are not on the quality scale but are enforced in core review, and
are followed here.

- **Everything is async.** No blocking I/O in the event loop, ever. No `open()`,
  no `requests`, no `time.sleep`. If something must block, it goes to an
  executor.
- **No I/O in properties.** A property is read during state writes and must be
  cheap.
- **Time comes from `homeassistant.util.dt`.** `dt_util.now()` for local,
  `dt_util.utcnow()` for UTC. Never `datetime.now()`, which has no timezone and
  no test control.
- **The config flow validates; setup assumes.** An entry that saved should set
  up. Anything that can be checked at configuration time is checked there,
  where the user is looking at the form.
- **`strings.json` and `translations/en.json` are kept identical.** English is
  the source, and a test compares them so they cannot drift.
- **Version is what was published.** Internal iterations are not releases.

---

## 6. What this project adds on top

Not Home Assistant rules. Project rules, each written because it was broken.

- **Pure modules import nothing from Home Assistant.** Decisions live there and
  are tested without a Home Assistant install. A test walks the source and
  fails if one of them grows a `homeassistant` import, or if a module quietly
  becomes pure or impure without the list being updated.
- **A static attribute check.** It parses the source and fails when an attribute
  is read and never assigned, following base classes within the package. It
  exists because exactly that bug shipped, and both ruff and mypy passed it
  because the file cannot be imported without Home Assistant.
- **No site data in source.** A test greps for the address, the meter entity
  names and the retailer, and fails on any of them.
- **Read the code, not the README.** Every claim about a Home Assistant
  behaviour in this document was read from core or from the developer
  documentation repository in this session, not recalled.
