# Examples

Replace the room id with the one shown as `room_id` on your room's mode sensor.

## Precool when someone heads home, ready on arrival

```yaml
automation:
  - alias: "Office ready when Jason heads home"
    triggers:
      - trigger: zone
        entity_id: person.jason
        zone: zone.work
        event: leave
    actions:
      - action: abode_hvac_coordinator.precool
        data:
          room_id: 3f2b9c0e8d5a4b7c9e1f2a3b4c5d6e7f
          ready_by: "{{ (now() + timedelta(minutes=40)).isoformat() }}"
```

The room starts when its model says it must, so it is in its precool band 40 minutes from now.

## A Precool button on a dashboard

```yaml
type: button
entity: button.office_precool
name: Cool the office
tap_action:
  action: toggle
```

## Tell me when a repair is raised

```yaml
automation:
  - alias: "Abode HVAC needs attention"
    triggers:
      - trigger: event
        event_type: repairs_issue_registry_updated
        event_data:
          action: create
          domain: abode_hvac_coordinator
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "Abode HVAC Coordinator"
          message: "Something needs fixing: Settings → System → Repairs."
```

## Hand a room back for the evening

```yaml
automation:
  - alias: "Office manual after 9 pm"
    triggers:
      - trigger: time
        at: "21:00:00"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.office_automatic_control
```

The unit is sent off once; after that its controls are yours until Automatic control is turned back on.
