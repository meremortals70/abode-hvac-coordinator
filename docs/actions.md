# Actions

Two actions. Both take a room id, which is the slug of the room name — a room
called "Main Bedroom" has the id `main_bedroom`. It appears in the `room_id`
attribute of every one of that room's sensors.

## `abode_hvac_coordinator.heading_home`

Brings a room to its comfort band ahead of arrival. **This is the only thing
that overrides an unoccupied room being off.**

| Field | Required | Type | Meaning |
|---|---|---|---|
| `room_id` | Yes | string | The room to bring to comfort |
| `deadline` | No | datetime | When it should have got there |

```yaml
action: abode_hvac_coordinator.heading_home
data:
  room_id: office
```

With a deadline:

```yaml
action: abode_hvac_coordinator.heading_home
data:
  room_id: office
  deadline: "2026-08-09T17:30:00+10:00"
```

The action takes **no target**. There is one comfort definition per room and it
is the band. Preconditioning drives to the middle of the occupied band; a room
with no occupied band configured is left alone and the trace says so.

The deadline is recorded but not yet acted on. Working out when to start in
order to arrive on time is the thermal model's job, and the model is not built.
Without it, preconditioning starts immediately.

Raises an error naming the room if no such room is configured.

## `abode_hvac_coordinator.clear_override`

Drops a heading-home request. The room returns to whatever presence and the
tariff say it should be doing.

| Field | Required | Type |
|---|---|---|
| `room_id` | Yes | string |

```yaml
action: abode_hvac_coordinator.clear_override
data:
  room_id: office
```

Clearing a room that has no override is not an error.

## Triggers

The integration defines no custom triggers. Use standard state triggers on the
entities it creates — see [Entities](entities.md) and [Examples](examples.md).

## Conditions

The integration defines no custom conditions. Use standard state conditions on
its entities.
