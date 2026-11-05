# Troubleshooting

## Where to look first

1. **The room's mode sensor attributes** (for a room named Office, `sensor.office`, then **Attributes**). `action` is what the unit is told; `control_reason`, `band_reason` and `limits.reasons` say why. `model.last_reason` says why the model did not learn on its last step, if it did not.
2. **Settings → System → Repairs** for anything the component needs you to fix.
3. **Diagnostics:** Settings → Devices & services → Abode HVAC Coordinator → the three dots → **Download diagnostics**. It holds every room's settings, choices, learning, plan and trace, the last 200 commands sent, the learned power draw, the power budget, the tariff and the forecasts. Your home's position is removed.
4. **The log:** each command sent is logged at INFO, a failed one at WARNING.

## Repairs

| Repair | What it means | How to fix it |
| --- | --- | --- |
| A tariff rule is not known | The tariff declares a rule this version does not know. It is shown in each room's trace and never dropped | Update the Abode HVAC Coordinator, or remove the rule from the tariff |
| The tariff cannot be read | Abode Power Tariffs did not answer. Every hour is priced the same until it does | Check the Abode Power Tariffs entry is loaded, or clear the tariff in Reconfigure |
| No speech engine | A room announces warnings, but Home Assistant has no speech engine | Add one, or turn announcements off in the room's options |
| Choose a speech engine | Home Assistant has several speech engines and none is chosen | Reconfigure → Announcements |
| The solar forecast cannot be read | The solar forecast integration failed | Check that integration, or remove it from the Power screen in Reconfigure |
| The grid sensor's direction looks wrong | Ten readings in a row contradicted the direction set up | Reconfigure → Grid direction |
| Grid import during a no-grid-import window | The house imported more than 0.05 kWh in the last window | Check the battery's settings and the rooms' power management |
| An air conditioner is in two rooms | One climate entity is used by two rooms; both are locked out | Remove it from one room in Configure → Rooms |
| A room's sensor is missing | Home Assistant has not provided a room's temperature or humidity sensor for 15 minutes: its integration is gone, failed, or no longer offers it | Check the integration it comes from, or choose another sensor in Configure → Rooms → Edit |
| A room's model has stopped learning | No correction in 24 hours; the repair names the reading that stopped it | Check the room's sensors and its air conditioner |

Each repair clears itself once its cause is gone.

## Stale feeds

The house's **Stale feeds** sensor lists readings too old to use:

| Feed | Too old after | What happens meanwhile |
| --- | --- | --- |
| Outdoor temperature, humidity, wind | 3 hours | The forecast's values, or the model's last outdoor temperature, are used |
| Battery charge, solar, house load, grid | 15 minutes | The power budget sets no limit |

Fix the sensor or its integration; nothing in this component needs changing.

## Common questions

**The mode sensor says Unavailable.** The room's cycle failed, or a required reading is missing. The log has one ERROR line naming the room, with the cause; it says again at INFO when the room runs again.

**The unit is not doing what I set.** While Automatic control is on, the component re-sends its decision every cycle. Turn Automatic control off to take over.

**A unit control is refused.** The unit's mode, fan speed and setpoint are writable only while Automatic control is off; the vanes only while Automatic vane control is off.

**The room turns off when nobody is there.** An empty room is off. Without a presence sensor a room always counts as occupied.

**An enforced room stops during a no-grid-import window.** Its trace's `power.reason` says why: the battery is down to its reserve, or it cannot supply the lowest rate's power.
