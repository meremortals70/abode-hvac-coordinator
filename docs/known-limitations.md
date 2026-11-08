# Known limitations

- **One sensor per room, in the room.** The room's air is taken as well mixed and read by one sensor where people are. A sensor in the air conditioner's airflow reads the unit, not the room.
- **The model needs weeks to settle.** It forecasts well within days, but its separate figures (how much the walls hold, what the sun adds) need weeks of varied weather to pull apart. Until then the forecast is right but the figures may trade against each other.
- **The forecast matters.** The plan looks six hours ahead on the weather forecast. A poor forecast means a poorer plan; the PID still holds the room.
- **Cloud at the windows** is taken from the forecast's cloud cover or UV index for the hour.
- **Irregular use** of a room (people and appliances at odd hours) shows up as forecast error, because the model learns a daily pattern.
- **Without a compressor frequency sensor or `hvac_action`,** off or fan-only is taken as not running, and in any other mode the compressor's starts and stops are read from rises and falls in the house load. Another appliance switching at the same moment can be taken for the compressor, and two units started within a few minutes of each other cannot be told apart.
- **Another automation writing to the same air conditioners** is overridden every cycle. The command log in diagnostics shows it.
- **A sensor that stops changing** while Home Assistant still shows it as available is trusted.
- **Ducted systems** with one indoor unit and zone dampers cannot be driven: the component controls one unit per room.
- **One window direction per room.** A corner room with two glazed walls is treated as one direction.
- **Overhangs** are taken as wide enough that the sun cannot get round their ends.
- **Seeding needs history.** A new room is seeded from what the recorder keeps, 10 days unless you changed it.
- **The demand forecast** reaches the next tariff change only when it is within six hours, because that is how far the plans look. Beyond that, it says so.
- **The learned controller** runs beside the plan and PID but has no policy yet, so the plan and PID are always in charge.
