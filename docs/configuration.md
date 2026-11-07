# Configuration

Everything is set up from the UI. The house's settings are changed with **Reconfigure**, which reloads the integration. The rooms are changed in **Configure → Rooms**, without a reload.

## House screens

| Screen | Field | What it does | Default |
| --- | --- | --- | --- |
| Tariff | Abode Power Tariffs entry | The tariff that prices every half-hour of the plan, and its rules (such as no grid import) | None: every hour is priced the same |
| Outdoor | Outdoor temperature sensor | Read by every room's model | None: the weather forecast's temperature for the hour now is used, and the rooms still learn from it. Without either, a room cannot learn |
| | Outdoor humidity sensor | The outdoor dew point, and free-cooling advice | None: the weather forecast's humidity for the hour now |
| | Wind speed sensor | The outdoor apparent temperature | None |
| Forecast | Weather entity | The hourly forecast the plan looks ahead with, read every 30 minutes | None: the readings now are used for every hour |
| Power | Battery charge sensor | The battery's charge, % | None |
| | Battery size | Usable capacity, kWh. Shown once a battery charge sensor is chosen (Home Assistant 2026.11 and later) | None |
| | Rated maximum discharge | The most the battery can supply, kW. Shown with the battery charge sensor, as above | None: no limit |
| | Solar power sensor | Solar output now | None |
| | House load sensor | The whole house's power use. The air conditioners' power draw is learned from its steps | None: each unit is taken as drawing 1.2 kW |
| | Reserve margin | Energy always left in the battery, kWh. Shown with the battery charge sensor, as above | None |
| | Grid power sensor | Power to or from the grid. Checks that no-grid-import windows are kept | None |
| | Solar forecast sources | Which solar forecasts to use (shown only if an integration offers one, and from 2026.11 only once a solar power sensor is chosen) | None: the Energy dashboard's |
| Grid direction | A positive reading means | Importing or exporting. Asked for a new grid sensor, and on Reconfigure while the grid-direction repair stands; pre-filled from the readings where they show it | — |
| Announcements | Speech engine | The engine spoken warnings use | None: the only one Home Assistant has |

From Home Assistant 2026.11, a field that has no effect without another is shown only once that other field is filled in, and is not saved while hidden. Before 2026.11 every field is shown.

The **power budget** runs only during a no-grid-import window, and only when battery charge, battery size, reserve margin, solar and house load are all set and reading. Otherwise nothing is limited.

## Room screen 1: the room

| Field | What it does | Default |
| --- | --- | --- |
| Name | The room's name, on its device and in spoken warnings | Required |
| Air conditioners | Every head in the room. Each one's live state is read when you save, and only what every head can do is sent | Required |
| Temperature sensor | The room's own temperature sensor; the model learns from it | Required |
| Humidity sensor | The room's own humidity sensor; the comfort index uses it | Required |
| Presence sensor | Whether someone is in. Needed for Precool | None: always occupied |
| Sleep schedule | A schedule, input boolean or binary sensor that is on while the room sleeps | None |
| Sun on window sensor | A binary sensor that is on while the sun is on the glass | None: worked out from the sun and the window direction |
| Doors and windows | With one open, nothing new is started, and the unit stops after the wait below | None |
| Blinds | The room's covers | None |
| Allow blind control | Lets the plan close the blinds when the sun is on the glass | On |
| Windows face | N, NE, E, SE, S, SW, W or NW | None: the sun is not counted unless a sun sensor says so |
| Overhang projection | How far an eave or awning sticks out, m (0–10). Shown once the window direction is chosen (2026.11 and later) | None |
| Overhang height | How far the overhang is above the bottom of the window, m (0–10). Shown with the window direction, as above | None |
| Wait before counting occupied | Minutes of presence before the room counts as occupied | 2 |
| Wait before counting empty | Minutes without presence before the room counts as empty | 10 |
| Warning before switching off | Minutes between a spoken warning and the unit stopping | 3 |
| Announce before switching off | Speak a warning before stopping for an empty room or an open door or window | Off |
| Announce through | The speakers for the warnings. Shown once Announce is on (2026.11 and later) | None |
| Stop for an open door or window after | Minutes a door or window may stay open before the unit stops | 5 |
| Lockout reason | A reason to keep the room off. Choose one or type your own | Not locked out |

The screen refuses an air conditioner that is already in another room, one that is not reporting, heads whose setpoint ranges do not overlap, and heads where one takes only a low and high setpoint and another cannot take them.

## Room screen 2: each air conditioner

One screen for each head.

| Field | What it does | Default |
| --- | --- | --- |
| Outdoor unit | A name for the outdoor unit this head shares. Heads with the same name share one compressor, in this room or another | None: its own outdoor unit |
| Compressor frequency sensor | A sensor of the compressor's frequency, Hz. Tells the component exactly when the compressor runs | None |

## Room screen 3: comfort

The comfort index is the temperature plus a part for humidity: index = temperature + 0.33 × vapour pressure (hPa) − 4. At 60 % humidity an index of 23 is about 22 °C, and 26 about 24 °C.

| Field | What it does | Default |
| --- | --- | --- |
| Occupied low, high | The band while someone is in and awake (0–50, steps of 0.5) | 23, 26 |
| Sleep low, high | The band while the room sleeps | 20, 23 |
| Precool low, high | The band during Precool | 23, 26 |
| Power management | Off: never limited. Guidance: the power limit is shown, never applied. Enforced: the room is held within the house's power budget during a no-grid-import window | Off |

A band whose low is not below its high is refused.

Under **Enforced**, a room whose gentlest cooling can't last to the end of the no-grid-import window keeps cooling at its lowest rate until the battery is down to its reserve or can't supply it, and only then stops.

## Options after setup

**Configure** opens a summary of the setup and two menus:

- **Rooms:** add, edit or remove a room.
- **Learning:** seed a room from the recorder's history, which replaces what it has learned with what its history teaches (a history that teaches nothing leaves it as it was), or reset what a room has learned and then seed it.
