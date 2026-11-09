# Installation and removal

## Before you start

- Home Assistant 2026.10.4 or later, with the recorder (it is on by default).
- Each air conditioner shown in Home Assistant as a climate entity, on and reporting.
- Each room's own temperature and humidity sensors.
- Optional:
  - an [Abode Power Tariffs](https://github.com/meremortals70/abode-power-tariffs) entry for prices;
  - a weather entity with an hourly forecast;
  - outdoor temperature, humidity and wind sensors;
  - battery charge, solar power, house load and grid power sensors;
  - a speech engine and speakers for spoken warnings.

## Install through the Marketplace (Home Assistant 2026.11 and later)

From 2026.11, HACS is built into Home Assistant as the **Marketplace**, under **Settings**. An existing HACS install, with its custom repositories, is moved into it on the first start after upgrading.

The quickest way is the first button in the [README](../README.md#install): it opens this repository in your own Home Assistant and offers to add it as a custom repository.

By hand:

1. Open **Settings → Marketplace** and accept its warning about community content.
2. Connect GitHub when the Marketplace asks: adding a custom repository needs a GitHub connection.
3. Select **Add from link**, enter `https://github.com/meremortals70/abode-hvac-coordinator`, and choose the type **Integration** if it asks.
4. Open **Abode HVAC Coordinator** and select **Install**. A first install of a config-flow integration opens its setup page straight away, and a repair offers the restart after an update.

## Install through HACS (before 2026.11)

The button in the [README](../README.md#install) opens the same repository in HACS on these versions.

1. In HACS, open the menu (three dots, top right) and choose **Custom repositories**.
2. Add `https://github.com/meremortals70/abode-hvac-coordinator` with the type **Integration**.
3. Find **Abode HVAC Coordinator** in HACS and download it.
4. Restart Home Assistant.

## Install by hand

1. Copy `custom_components/abode_hvac_coordinator` from this repository into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.

## Set it up

**Settings → Devices & services → Add integration → Abode HVAC Coordinator.** Only one can be set up.

The house screens come first. Any field can be left empty and filled in later with **Reconfigure**.

1. **Tariff:** the Abode Power Tariffs entry. Left empty, every hour is priced the same.
2. **Outdoor:** outdoor temperature, humidity and wind sensors.
3. **Forecast:** the weather entity.
4. **Power:** battery charge and size, rated discharge, solar, house load, reserve margin, grid power, and solar forecast sources (shown only when an integration offers a solar forecast).
5. **Grid direction:** shown only when you chose a new grid sensor. Say whether a positive reading means importing or exporting.
6. **Announcements:** the speech engine. Left empty, the only one Home Assistant has is used.

Then the first room:

1. **The room:** its name, air conditioners, temperature and humidity sensors, and any of the optional settings. Each air conditioner's live state is read now, so it must be reporting. An air conditioner can belong to only one room.
2. **Each air conditioner** gets its own screen: the outdoor unit it shares (if any) and its compressor frequency sensor (if any). Heads given the same outdoor-unit name share one compressor.
3. **Comfort:** the occupied, sleep and precool bands, and power management.

Every field is explained in [configuration](configuration.md).

When you save, the room is created with its device and entities. Its model is seeded from the recorder's history in the background, so it does not start from nothing.

## Add, edit and remove rooms

**Settings → Devices & services → Abode HVAC Coordinator → Configure → Rooms.**

- **Add a room** opens the same three screens. The room appears at once, without a restart.
- **Edit a room** opens them pre-filled. What the room has learned is kept.
- **Remove a room** removes its device and entities, and what it has learned.

**Learning** in the same menu seeds a room from history again, or resets what a room has learned and then seeds it.

## Check it works

1. Open the room's device. The mode sensor (named after the room) should show `Occupied`, `Sleep`, `Unoccupied` or similar, not `Unavailable`.
2. Open the mode sensor's attributes. `action` is what the unit is being told (`cool`, `rest` and so on) and `control_reason` says why.
3. After the first 5 minutes, `plan` appears in the attributes. The **Learning** sensor has a value once the first week has closed (it is the last completed week's average one-hour error); before then it shows unknown.
4. **Settings → System → Repairs** lists anything the component needs you to fix.

## Remove the integration

1. **Settings → Devices & services → Abode HVAC Coordinator → the three dots → Delete.** This removes every room's device and entities, and the component's stored learning (`.storage/abode_hvac_coordinator`).
2. In the Marketplace (or HACS before 2026.11), uninstall **Abode HVAC Coordinator**, or delete `config/custom_components/abode_hvac_coordinator` if you installed by hand.
3. Restart Home Assistant.

The air conditioners are left as they were last set. Turn them off or back to your own settings if you want.
