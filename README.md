# Narwal Freo 20 for Home Assistant

My complete Home Assistant setup for the Narwal Freo 20, packaged so others can
install it and see everything: a full control window (cleaning, dock, settings,
history, obstacle photos, pet, live camera with audio, no-go zones and walls),
the integrations behind it, and the camera add-on.

> **Read first: cloud dependency and account risk.** The camera, obstacle
> photos, parts, history and pet photos use the Narwal cloud with your own
> account, through unofficial and undocumented app endpoints. Narwal can change
> or block them at any time, and using them could put the account at risk. The
> local integration and the map work without the cloud.

> **Status: tested only on one Freo 20** (Home Assistant OS, amd64, EU region),
> and **not yet installed from this repository on a clean system.** Expect rough
> edges. Other Narwal models are untested.

## What is in it

| Path | What it is | Needs the cloud |
|---|---|---|
| `custom_components/narwal_local` | Local connection to the robot (state, settings, dock, map, rooms). A modified copy of [cvette/narwal-ha](https://github.com/cvette/narwal-ha), MIT. See [changes](docs/NARWAL_LOCAL_CHANGES.md). | No |
| `custom_components/narwal_mapa` | App-style map as an image entity, plus a service to draw no-go zones and virtual walls. | No |
| `custom_components/narwal_chmura` | "Narwal Cloud Companion": obstacle photos (decrypted locally), parts and wear with reset, cleaning history and totals, pet photos and pet sounds, room cleaning, camera session. | Yes |
| `addons/narwal_kamera` | Local Supervisor add-on that joins the robot's Agora video session and serves the camera, robot audio and push-to-talk to HA. | Yes |
| `www/narwal` | The control window: a Lovelace card `custom:narwal-dashboard` (English UI). | Partly |
| `examples` | Card configuration and an optional schedule automation. | |
| `docs/HOW_IT_WORKS.md` | How the photo decryption and the camera session work. | |

## Requirements

**Required**

- Home Assistant OS or Supervised (the camera add-on needs the Supervisor), amd64.
- `narwal_local` (included) with the robot's local IP address.
- `narwal_mapa` (included), for the map in the window.
- `narwal_chmura` (included) with your Narwal account, for camera, photos, parts and history.
- `narwal_kamera` add-on (included), for the live camera. It installs
  `agora-python-server-sdk` 2.4.10, numpy, pillow and av when it is built.
- The robot added to Home Assistant through **Matter** (built-in integration).
  The window's Auto mode and the schedule example use the Matter vacuum and its
  clean mode select.

**Optional**

- A schedule helper and the automation in `examples/schedule_automation.yaml`.
- A TTS engine (for example Piper) for "say through the robot".
- `browser_mod`, only if you want to open the window as a popup.

**Not needed:** sjmotew/NarwalIntegration, button-card or other HACS cards.

## Install

**Full step-by-step guide with troubleshooting: [docs/INSTALL.md](docs/INSTALL.md).**

Quick start:

1. Copy `custom_components/narwal_local`, `narwal_mapa` and `narwal_chmura` to
   `/config/custom_components/`, and `www/narwal` to `/config/www/narwal`.
2. Add `narwal_mapa:` to `configuration.yaml` and restart Home Assistant.
3. Add integration **Narwal Local** (robot IP), then **Narwal Cloud Companion**
   (Narwal email, password, region). Pair the robot with **Matter** too.
4. Camera (HA OS only): copy `addons/narwal_kamera` to the local add-ons folder,
   install and start **Narwal kamera**.
5. Add `/local/narwal/dashboard.js` as a JavaScript module resource and add the
   card from [examples/card.yaml](examples/card.yaml).

Only administrators can start the camera, reset parts, edit zones or drive the robot.

## Safety notes

- Starting the camera turns on the robot's camera.
- "Find pet", "Clean here", "Go to point" and arrow driving **move the robot**.
- Pet calls and "say through the robot" **play sound on the robot**.
- Every cleaning start, dock task, part reset and zone or wall change asks for
  confirmation in the window first.

## Known limits

- Push-to-talk and typed speech are experimental: the robot may not play them.
- The camera frame rate is whatever the robot sends; there is no app setting for it.
- Entity ids are fixed in the window except the ones in the card configuration.
  If your entities are named differently, rename them in HA.
- Service names, attribute names and some service options are still in Polish
  (for example `narwal_chmura.zeruj_czesc`, `narwal_mapa.strefa`). The UI text
  is English.

## Maintenance

The code is generated from my private setup with
`python3 tools/build_from_source.py <private repo>`, which also applies the
English text from `tools/i18n_en.py` and `tools/i18n_dashboard_en.py`.
`python3 tools/scan_private.py` checks for personal data before every commit.

## Licence

`narwal_local` keeps its MIT licence (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
The licence for the rest is still to be decided.
