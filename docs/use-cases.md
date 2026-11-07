# Use cases

## Keep rooms comfortable for less

Each room has a band: a low and a high on the comfort index. The component aims for the centre and counts anywhere inside as comfortable. Inside that leeway it picks the cheapest way to stay there. It might let the weather do the work, close the blinds when the sun is on the glass, dry the air, or run the compressor gently for longer instead of hard and often.

Each room's model learns how fast the room warms and cools, what the sun and the people in it add, and how the unit responds. The plans get better as the model learns. The **Learning** sensor shows the week's average forecast error, so you can watch it fall.

## Use cheap or solar hours

With [Abode Power Tariffs](https://github.com/meremortals70/abode-power-tariffs) set up, every half-hour of the plan is priced. A cheaper period coming up is part of every plan, so a room can cool a little ahead of an expensive period and coast through it, but only while it stays inside its band.

During a no-grid-import window, a room set to **Enforced** power management is held within what the battery and solar can supply until the window ends. If even its gentlest cooling can't last that long, it keeps cooling gently until the battery is down to its reserve, then stops. **Guidance** shows the same limit without applying it.

The house's **Demand forecast** sensor gives the energy the rooms' plans will use until the next tariff change, for whatever runs your battery.

## A room ready when someone gets home

In a room with a presence sensor, the **Precool** button starts cooling an empty room now. The `precool` action can also give a time to be ready by. The component then works out from the room's model when it must start, so the room is in its precool band by that time. Precool ends when someone comes in, when Leave is pressed, when it is cancelled, or an hour after the ready time.

## Leave, sleep, and hand back control

- **Leave** turns a room off at once until someone comes back; in a room without a presence sensor, until you change Automatic control or Comfort mode.
- **Comfort mode** follows the sleep schedule, or stays on Sleep or Awake until you change it.
- **Automatic control off** sends the unit off once, then leaves it entirely to you.
