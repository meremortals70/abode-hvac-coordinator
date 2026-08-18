# Attribution

Abode Power Tariffs is original work. The architecture, the domain model and the
design decisions behind it are the author's own, written to solve a problem
Home Assistant had no clean answer for: a single, canonical source of truth for
a household's electricity tariff plan.

No code, wording, plan data or tariff structure has been taken from another
project.

Licensed under the Apache License 2.0. See `LICENSE`.

## Dependencies

None. The integration has no third-party runtime requirements. Development
needs only ruff, mypy, voluptuous and coverage, pinned in
`requirements_test.txt`.

## Interoperability

Two published shapes are matched deliberately, so that consumers already written
against them work here without being rewritten. Neither project is affiliated
with this one and no code from either is used.

- The forward series on the price sensors uses the `{start, end, value}` shape
  that [evcc](https://evcc.io/) reads from a Home Assistant sensor attribute.
- The `get_intervals` response uses the field names of Home Assistant's Amber
  Electric integration.

## Home Assistant

Built as a custom integration for [Home Assistant](https://www.home-assistant.io/),
against its public integration APIs, and tracked against its integration quality
scale in `custom_components/abode_power_tariffs/quality_scale.yaml`. Not
affiliated with or endorsed by the Home Assistant project or Nabu Casa.
