# Actions

The room id is shown as `room_id` in the attributes of the room's mode sensor (for a room named Office, `sensor.office`).

## `abode_hvac_coordinator.precool`

Starts Precool in an empty room with a presence sensor: it runs to the centre of its precool band. Precool ends once the room counts as occupied, when Leave is pressed, when `cancel_precool` is called, or an hour after `ready_by`. A room still counts as occupied for its empty grace after the last person leaves, so Precool started in that time ends at once.

| Field | Required | What it does |
| --- | --- | --- |
| `room_id` | Yes | The room |
| `ready_by` | No | The time the room must be in its precool band by. The room starts when the model says it must. Left out, Precool starts now |

```yaml
action: abode_hvac_coordinator.precool
data:
  room_id: 3f2b9c0e8d5a4b7c9e1f2a3b4c5d6e7f
  ready_by: "2026-11-02 17:30:00"
```

## `abode_hvac_coordinator.cancel_precool`

Ends Precool in a room.

| Field | Required | What it does |
| --- | --- | --- |
| `room_id` | Yes | The room |

```yaml
action: abode_hvac_coordinator.cancel_precool
data:
  room_id: 3f2b9c0e8d5a4b7c9e1f2a3b4c5d6e7f
```

## Errors

Each is shown with a message saying what is wrong:

| Error | When |
| --- | --- |
| The integration is not loaded | No Abode HVAC Coordinator entry is running |
| No room has the id … | The `room_id` is not one of your rooms |
| … has no presence sensor | `precool` in a room without a presence sensor. Precool needs one to know when someone comes in |
