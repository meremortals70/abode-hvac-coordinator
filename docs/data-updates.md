# How often it updates

| What | How often |
| --- | --- |
| The cycle: every room's readings, the band, the controller, what is sent to the unit, the entities | Every 30 seconds |
| The model's minute averages and the unit's rate | Every minute, in the cycle that passes the minute |
| The model learns (both filters) and a new plan is made | Every 5 minutes, and at once when the band changes or you change a choice |
| The model's trust check | Every hour |
| Yesterday's scores (comfort in band, near the centre, starts, cost) | At local midnight |
| The tariff, from Abode Power Tariffs, 24 hours ahead | Every 15 minutes |
| The solar forecast | Every 15 minutes |
| The hourly weather forecast | Every 30 minutes |
| The demand forecast and projected cost | Every cycle, from the plans in force |
| What it has learned, saved to `.storage` | Within 5 minutes of learning; within 5 seconds of a choice you make; on unload |

The unit controls (mode, fan speed, vanes, setpoint) show the unit's value as soon as the unit reports it, without waiting for a cycle.

Nothing is polled from a device. Everything comes from Home Assistant's own entities and actions.
