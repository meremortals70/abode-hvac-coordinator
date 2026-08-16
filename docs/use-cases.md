# Use cases

## What this is for

**A house where comfort matters more than cost, but cost still matters.** The
comfort band is a hard constraint. The tariff decides *when* energy is banked
and *which* actuator delivers comfort — never whether you get it.

**A house with more than one thing that could deliver comfort.** If all you have
is a compressor, most of the value here is gone. The point is that blinds, a
fan, dry mode and the compressor are four different prices for the same outcome,
and the cheapest one that works should go first.

**A humid climate.** Dry bulb alone does not describe comfort where humidity
swings 30 points in a day. A room at 24 °C and 40% and a room at 24 °C and 80%
are not the same room.

**Anyone who has been burned by an automation they could not explain.** Every
decision publishes why it was made, including which cheaper options were
rejected and on what grounds.

## Concrete examples

**The room nobody is in.** The office is empty and it is 34 °C outside. The
controller does nothing at all — not a wider band, nothing. When you send a
heading-home request on the way back, it brings the room to its comfort band
before you arrive.

**The muggy evening that is not actually hot.** 26 °C and 85% humidity. Dry bulb
says you are fine. The comfort index says you are not. The load is latent, so
the controller reaches for dry mode rather than cooling — a fraction of the
draw for the thing that is actually wrong.

**The sunny winter morning.** The room is below its band and there is sun on the
window. The controller opens the blind rather than running the compressor.

**The free tariff window.** Between 11:00 and 14:00 energy costs nothing and the
forecast says the afternoon is going to be hot. The controller overshoots the
low bound to bank thermal mass in the building, so the expensive evening runs
on stored cold rather than stored electricity.

## What this is not for

**It is not a thermostat.** If you want to set 22 and have it hold 22, use the
Generic Thermostat integration. This deliberately does not let you set a
setpoint.

**It does not manage your battery.** It publishes what it expects to draw. What
you do with that is yours — see [Architecture](architecture.md) for why.

**It is not a scheduler.** It has no concept of "cool the house at 5pm". It has
modes driven by presence, tariff and a thermal model.

**It will not save you money on its own.** It will spend less than a naive
thermostat for the same comfort. It will not spend less than turning the air
conditioning off.
