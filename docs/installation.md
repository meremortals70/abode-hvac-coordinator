# Installation

## Requirements

- Home Assistant **2026.6.0** or later
- One `climate` entity per room you want controlled
- A temperature sensor and a humidity sensor per room

No external dependencies. The integration makes no network calls and installs no
Python packages.

## Install

### With HACS (recommended)

HACS does not know about this repository until you tell it. That is what a
"custom repository" is — a repository HACS watches that is not on its default
list.

1. Open **HACS** in the Home Assistant sidebar
2. Click the **three-dot menu** at the top right of the HACS page
3. Choose **Custom repositories**
4. In **Repository**, paste:
   ```
   https://github.com/meremortals70/hvac-coordinator
   ```
5. In **Type**, choose **Integration**
6. Click **Add**, then close the dialog

HACS now watches this repository. To install what it found:

7. Back on the HACS page, search for **HVAC Coordinator**
8. Click it, then **Download**
9. Accept the version it offers
10. **Restart Home Assistant** — Settings → System → top right → Restart

Nothing appears until you restart. That catches people out every time.

### Updating later

HACS shows an update when a new **release** is published. If you know there is a
newer version and HACS is not offering it, open the repository in HACS and use
**Redownload** — that fetches the current state regardless.

### Without HACS

1. Download the repository as a ZIP and unpack it
2. Copy the folder `custom_components/abode_hvac_coordinator/` into your Home
   Assistant configuration directory, so you end up with
   `config/custom_components/abode_hvac_coordinator/manifest.json`
3. Restart Home Assistant

## Set up

1. **Settings → Devices & Services → Add Integration**
2. Search for **HVAC Coordinator** and select it
3. Describe your first room and its entities
4. Set its comfort bands

Setup finishes with one working room. Add further rooms from **Configure**.

**Only one instance can be created.** A second attempt aborts with "already
configured". Rooms live inside the single entry.

## Add, edit or remove a room

**Settings → Devices & Services → HVAC Coordinator → Configure**

A menu offers add, edit and remove. Editing prefills the room's current
settings. Removing a room takes its device and entities with it.

Adding and editing are the same two steps. First the room and its entities:

| Field | Required | What it does |
|---|---|---|
| Room name | Yes | Becomes the device name. The room id is derived from it |
| Air conditioner | Yes | The `climate` entity for this room |
| Temperature sensor | No | Without it, no comfort index and no actuation |
| Humidity sensor | No | Without it, no comfort index and no actuation |
| Presence sensor | No | Without it, presence reads unknown and the room holds occupied |
| Sleep schedule | No | Without it, the sleep band is never used |
| Sun on this room's windows | No | Without it, falls back to sun above horizon |
| Illuminance sensor | No | Recorded, not acted on |
| Windows and doors | No | Any one open suspends the room |
| Blinds | No | Without any, covers are never used |
| Lockout | No | Leave on "Not locked out". Choosing any reason means the room is never actuated |

Then the comfort bands, in HCI. These arrive prefilled with sensible defaults —
occupied 24–27, sleep 21–24 — and are meant to be adjusted. Clear both boxes of
a mode you do not want: an office with no sleeping hours should clear the sleep
pair. See [Comfort index](comfort-index.md) for what the numbers mean.

Repeat Configure for each room. Adding a room with a name that slugs to an
existing room id replaces that room.

## Verify it is working

Each room becomes a **device** with three entities under it. Check
**Settings → Devices & Services → HVAC Coordinator → devices**:

- `sensor.<room>_mode` — should show a mode, not `unknown`
- `sensor.<room>_comfort_index` — should show a number
- `sensor.<room>_target_dry_bulb` — should show a temperature

Open the mode sensor's attributes. The `reasons` and `rejected` lists explain
the current decision. If they do not make sense, that is a bug worth reporting.

## Remove

**Settings → Devices & Services → HVAC Coordinator → three-dot menu → Delete**

This removes the config entry, every room device, every entity, and the stored
thermal model state. Nothing is left behind in `.storage`.

To remove a single room rather than everything, delete its device from the
device page. The delete button is enabled only once the room is no longer in
the configuration.

To uninstall completely, delete the entry first, then remove
`custom_components/abode_hvac_coordinator/` and restart.
