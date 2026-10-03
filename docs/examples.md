# Examples

## Heading home when you leave work

```yaml
alias: Precondition office on the way home
triggers:
  - trigger: zone
    entity_id: person.jason
    zone: zone.work
    event: leave
conditions:
  - condition: time
    after: "15:00:00"
actions:
  - action: abode_hvac_coordinator.heading_home
    data:
      room_id: office
```

## Heading home from a phone shortcut

```yaml
alias: Heading home button
triggers:
  - trigger: event
    event_type: mobile_app_notification_action
    event_data:
      action: HEADING_HOME
actions:
  - action: abode_hvac_coordinator.heading_home
    data:
      room_id: living_room
```

## Clear the override once you have arrived and settled

```yaml
alias: Clear office override after arrival
triggers:
  - trigger: state
    entity_id: binary_sensor.office_presence
    to: "on"
    for: "00:30:00"
actions:
  - action: abode_hvac_coordinator.clear_override
    data:
      room_id: office
```

Presence takes over from there, so the override has done its job.

## Sleep schedule

Create a **Schedule** helper covering your sleeping hours and select it as the
room's sleep schedule. Nothing else is needed — the room moves to its sleep band
when the schedule is on.

For a schedule that varies, an `input_boolean` driven by your own automation
works identically.

## Notify when a room reaches for the compressor

Useful while you are learning whether the cheaper steps are being used properly.

```yaml
alias: Office went to compressor
triggers:
  - trigger: state
    entity_id: sensor.office_mode
    attribute: actuator
    to: compressor
actions:
  - action: notify.mobile_app_phone
    data:
      title: Office compressor
      message: >
        {{ state_attr('sensor.office_mode','reasons') | join('; ') }}
        (rejected: {{ state_attr('sensor.office_mode','rejected') | join('; ') }})
```

## Dashboard card showing the decision

```yaml
type: entities
title: Office
entities:
  - entity: sensor.office_comfort_index
  - entity: sensor.office_mode
  - entity: sensor.office_target_dry_bulb
  - type: attribute
    entity: sensor.office_mode
    attribute: band_position
    name: Position in band
  - type: attribute
    entity: sensor.office_mode
    attribute: demand
    name: Demand
```

## Greying out the controls while the automation owns them

The room's mode, fan speed and setpoint controls refuse changes while
**Automatic control** is on, and the vane controls refuse them while **Automatic
vane control** is on. A native entity cannot be greyed out and still show its
value, so the controls always show the unit's live state and the grey look comes
from the card: a conditional card that shows the controls only while the room
can be changed by hand.

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Office
    entities:
      - entity: switch.office_automatic_control
      - entity: switch.office_automatic_vane_control
  - type: conditional
    conditions:
      - entity: switch.office_automatic_control
        state: "off"
    card:
      type: entities
      title: Office, by hand
      entities:
        - entity: select.office_mode
        - entity: select.office_fan_speed
        - entity: number.office_setpoint
  - type: conditional
    conditions:
      - entity: switch.office_automatic_vane_control
        state: "off"
    card:
      type: entities
      title: Office vanes
      entities:
        - entity: select.office_vertical_vane
```

To show the live state while the automation owns it, put the same entities in a
second card that is shown when the switch is `on`, and add `secondary_info` or a
muted theme to it.

## Gauge with the comfort scale

```yaml
type: gauge
entity: sensor.office_comfort_index
min: 15
max: 35
needle: true
severity:
  green: 23.5
  yellow: 27.5
  red: 31
```

The thresholds come from the band table in [Comfort index](comfort-index.md).
