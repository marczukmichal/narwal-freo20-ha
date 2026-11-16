# Installation guide

This guide was tested on a clean Home Assistant 2026.11.1: the integrations
install, the robot and the Narwal account can be added, and the card works in
English.

## Before you start

- Home Assistant OS or Supervised if you want the live camera (the camera add-on
  needs the Supervisor). Everything else also works on HA Container.
- The robot's local IP address. Give it a fixed address in your router.
- Your Narwal app email and password, for the cloud features.
- A way to copy files into `/config`: the Samba share, the SSH add-on or the
  File editor add-on.

## 1. Copy the files

From this repository, copy:

| From | To |
|---|---|
| `custom_components/narwal_local` | `/config/custom_components/narwal_local` |
| `custom_components/narwal_map` | `/config/custom_components/narwal_map` |
| `custom_components/narwal_cloud` | `/config/custom_components/narwal_cloud` |
| `www/narwal` | `/config/www/narwal` |

Create the `custom_components` and `www` folders if they do not exist.

## 2. Enable the map

Add this line to `/config/configuration.yaml`:

```yaml
narwal_map:
```

## 3. Restart

Settings → System → ⋮ (top right) → Restart Home Assistant.

After the restart, **Narwal map (app style)** shows up in Settings → Devices &
services on its own. Its map stays "unavailable" until step 4 is done; that is
expected.

## 4. Add the robot (local)

1. Settings → Devices & services → **Add integration**.
2. Search for **Narwal Local**.
3. Enter the robot's IP address. Keep the default port.
4. Keep the device name **Narwal**. The card expects entity ids such as
   `sensor.narwal_battery` and `vacuum.narwal`.

If the robot is asleep, some entities stay unavailable until it wakes up.
Open the Narwal app or start a clean to wake it.

## 5. Add your Narwal account (cloud)

1. Settings → Devices & services → **Add integration**.
2. Search for **Narwal Cloud Companion**.
3. Enter the email and password you use in the Narwal app, and your region.

The password is not stored; only the session token is. If the session expires,
Home Assistant asks you to sign in again. Read the cloud and account-risk note
in the README before this step.

This adds obstacle photos, parts and wear, cleaning history, pet photos and
sounds, and the camera session.

## 6. Add the robot through Matter

The card's **Auto** mode and the schedule use the robot's Matter entities.

1. Get a Matter pairing code for the robot as Narwal describes for your model.
2. In Home Assistant: Settings → Devices & services → Add integration →
   **Matter**, then add the device with that code.
3. Check that `vacuum.freo_20` and `select.freo_20_clean_mode` exist. If yours
   have other ids, put them in the card configuration (`matter:`) or rename them.

## 7. Camera add-on (Home Assistant OS only)

1. Copy `addons/narwal_kamera` into the local add-ons folder: `/addons`
   (called `local_apps` in newer SSH and Samba add-ons).
2. Settings → Add-ons → Add-on store → ⋮ → **Check for updates**.
3. Open **Narwal kamera** under *Local add-ons*, install it and start it.
   Building takes a few minutes the first time.
4. Leave the `device_id` option empty; the robot on your account is used.

It must be a local add-on: the integration reaches it at
`http://local-narwal-kamera:8099`.

## 8. Add the card

1. Settings → Dashboards → ⋮ → **Resources** → Add resource:
   - URL: `/local/narwal/dashboard.js`
   - Type: **JavaScript module**
2. Open a dashboard → Edit → Add card → **Manual**, and paste:

```yaml
type: custom:narwal-dashboard
vacuum: vacuum.narwal
matter: vacuum.freo_20
image: image.vacuum_map
camera: camera.narwal_camera
schedule: schedule.vacuum_schedule
tts: tts.piper
tts_language: en
```

The card is wide. It works best in a **Panel** view or as a popup.

**Or add a ready dashboard:** Settings → Dashboards → **Add dashboard** → *New
dashboard from scratch*, name it *Vacuum*, open it → Edit → ⋮ → **Raw
configuration editor**, paste [examples/dashboard.yaml](../examples/dashboard.yaml)
and save. The resource from step 1 is still needed.

## 9. Schedule (optional)

1. Settings → Devices & services → Helpers → Create helper → **Schedule**.
   Name it **Vacuum schedule** (`schedule.vacuum_schedule`) and set the times.
2. Add the automation from `examples/schedule_automation.yaml`
   (Settings → Automations → Create → ⋮ → Edit in YAML).

The robot then cleans the whole home in Auto mode when a schedule window
starts, only if it is in the dock.

## Quick check

- Cleaning tab: rooms load under "Choose rooms".
- Dock tab: water tanks and dust box show values.
- History tab: totals from the cloud.
- Camera tab: **Start preview** shows the live picture (add-on needed).

## Troubleshooting

| What you see | What to do |
|---|---|
| "Custom element doesn't exist: narwal-dashboard" | The resource is missing or not a JavaScript module. Check step 8, then reload the page with the cache cleared. |
| Card is mostly "Unavailable" | Narwal Local is not added, or the robot is asleep. Check step 4. |
| "Map temporarily unavailable" | Narwal Local has no map yet. Wake the robot; the map appears after it reports one. |
| Auto mode is not available | The Matter entities are missing or have other ids (step 6). |
| History, parts or obstacles say "unavailable" | Narwal Cloud Companion is not added, or its session expired. Check Settings → Devices & services for a re-auth prompt. |
| Camera does not start | The add-on is not running, or it is not a *local* add-on. Check its log in Settings → Add-ons. |
| The integration does not show up in "Add integration" | The folders are in the wrong place or HA was not restarted. The path must be `/config/custom_components/narwal_local/manifest.json`. |
| Card is hard to read | It is made for a dark theme. On a light theme it uses its own dark background; reload if it still looks pale. |

Logs: Settings → System → Logs, filter by "narwal".
