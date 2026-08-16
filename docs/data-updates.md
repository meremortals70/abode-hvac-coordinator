# Data updates

## How it evaluates

The integration polls nothing and makes no network calls. Everything it reads is
already in Home Assistant's state machine.

Every room is re-evaluated when:

1. **Any entity a room reads changes state** — its temperature, humidity,
   presence, illuminance, sleep schedule, or any of its openings
2. **Every 30 seconds**, as a backstop
3. **An action is called** — heading home or clear override refresh immediately
4. **The configuration changes**, which reloads the integration

The 30 second interval exists because some decisions depend on the clock rather
than on any entity — a tariff interval boundary, for instance. It is not a poll of
anything.

## What an evaluation does

For each room, in order:

1. Assemble the inputs from current state
2. Work out the mode
3. Compute the comfort index
4. Work out the band in force and the derived setpoint
5. Select the actuator step
6. Write the decision trace

The decision is made by pure functions with no access to Home Assistant, which
is why the whole decision path can be unit tested without a running instance.

## What it does with the decision

Currently, nothing but log it at debug level. Carrying out a decision means
calling the regulation and cover layers, which is not yet wired. See
[Known limitations](known-limitations.md).

## Missing readings

A sensor that is missing, unavailable, unknown or non-numeric is treated as
**no reading**, never as zero. A non-numeric value on an entity that should be
numeric is logged as a warning naming the entity.

The distinction matters most for presence: unknown is not absent. See
[Modes](modes.md).

## Stored state

Learned thermal model state is written to `.storage`, at most once every five
minutes, with atomic writes. A pending write is flushed when Home Assistant
stops.

Losing it costs convergence time, not correctness — the model falls back to
hysteresis until it has learned again. Configuration is not stored here; it
lives in the config entry.

## The tariff series

Fetched from Abode Power Tariffs at startup and every fifteen minutes after,
covering twenty-four hours at half-hourly resolution.

The refresh interval does not set decision accuracy. Which interval is in force
is resolved from the series itself on every evaluation, so a boundary is caught
within the thirty-second cycle regardless of when the series was last fetched.
Fifteen minutes is only about how quickly a plan you have just edited in the
tariff integration is picked up.

A failed fetch keeps the series already held and raises a repair issue. See
[Tariff](tariff.md).

## Reading ages

A reading is only acted on if it is recent enough for its feed. Home Assistant
keeps the last state of a device that has silently left the network, so a value
being present is not the same as it being current. See
[Stale feeds](staleness.md).
