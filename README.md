# BTicino GO Integration for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/abeilprincipino/BTicino-GO-Integration)](https://github.com/abeilprincipino/BTicino-GO-Integration/releases/latest)
[![Release](https://github.com/abeilprincipino/BTicino-GO-Integration/actions/workflows/release.yml/badge.svg)](https://github.com/abeilprincipino/BTicino-GO-Integration/actions/workflows/release.yml)
[![License](https://img.shields.io/github/license/abeilprincipino/BTicino-GO-Integration)](LICENSE)
[![IoT Class](https://img.shields.io/badge/IoT-local_push-blue)](custom_components/bticino_companion/manifest.json)

Home Assistant custom integration and Lovelace intercom card for
[BTicino GO Companion](https://github.com/abeilprincipino/BTicino-GO-Companion).

The integration talks to the companion service running on the intercom, keeps a persistent local
connection to its `/api/v3` API, and turns it into native Home Assistant entities. It also ships the
**BTicino Go Intercom** card, which shows the intercom camera, rings on incoming calls, and lets you
answer, talk, hang up, and unlock from a dashboard.

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Setup](#setup)
- [Intercom Card](#intercom-card)
- [Doorbell notifications with the picture](#doorbell-notifications-with-the-picture)
- [Entities](#entities)
- [Updating](#updating)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Requirements

- Home Assistant `2025.2.0` or later.
- [HACS](https://hacs.xyz/), or manual installation of a custom component.
- A rooted BTicino Classe 300X or Classe 100X running BTicino GO Companion. Home Assistant can
  install it over SSH (see [Setup](#setup)), or install it by hand: see
  [BTicino GO Companion — Installation](https://github.com/abeilprincipino/BTicino-GO-Companion#installation).
- The companion reachable from Home Assistant, usually at `http://<intercom-ip>:8080`.
- The companion WebUI setup completed (admin account created at `http://<intercom-ip>/`).
- For camera, two-way audio and answering calls, the intercom must have valid Flexisip users and
  routing, usually provisioned by pairing the official BTicino Door Entry mobile app once, and the
  companion SIP user provisioned by its installer.

## Installation

### HACS

Prerequisite: [HACS](https://hacs.xyz/) must already be installed in Home Assistant.

Add this repository to HACS as a custom repository:

[![Add Repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=abeilprincipino&repository=BTicino-GO-Integration&category=integration)

Or manually, in `HACS > ⋮ > Custom repositories`:

- Repository: `https://github.com/abeilprincipino/BTicino-GO-Integration`
- Type: `Integration`

Then search for `BTicino Companion` in HACS, download it, and restart Home Assistant.

### Manual

1. Download `bticino_companion.zip` from the
   [latest release](https://github.com/abeilprincipino/BTicino-GO-Integration/releases/latest), or
   clone this repository.
2. Copy the integration directory into your Home Assistant configuration directory:

   ```text
   <config>/custom_components/bticino_companion
   ```

3. Restart Home Assistant.

The intercom card is part of the integration: there is no separate card to install.

## Setup

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=bticino_companion)

### Installing the Companion from Home Assistant

On a rooted intercom reachable over SSH you do not need a terminal:

1. **Settings → Devices & services → Add integration → BTicino Companion → Install the Companion on the intercom.**
2. Enter the intercom's IP address and its SSH username (`root`) and password. The password is used
   only during this setup and is never stored.
3. Home Assistant installs the latest Companion release and shows its progress. If the installer
   fails, its `FAIL:` lines are shown and you can run it again.
4. On a new Companion, choose the username and password of its web interface (8 to 72 bytes).
5. Reboot the intercom when asked: incoming calls reach the Companion only after a reboot.

To reinstall later — for example to repair an installation — open the integration entry and choose
**Reconfigure**. The SSH key of the intercom is checked against the one seen at installation; tick
*The intercom was reflashed: accept its new SSH key* only if you really reflashed it.

### Connecting to an installed Companion

1. Go to `Settings > Devices & services`.
2. The companion is discovered automatically over mDNS (`_bticomp._tcp.local.`). Click
   `Configure` on the discovered device. If it is not discovered, click `Add integration`, choose
   `BTicino Companion`, then `Connect to an installed Companion`, and enter the companion URL, for
   example `http://192.168.1.50:8080`.
3. Enter the code the flow asks for:
   - **Claim code** (`xxxx-xxxx`) — for a companion that has never been paired. It is shown on the
     Home Assistant page of the companion WebUI.
   - **Recovery code** (`xxxx-xxxx`) — for a companion that is already claimed, for example after
     reinstalling Home Assistant or removing the integration. Issue a temporary recovery code from
     the companion WebUI and enter it.
4. Submit. The device and its entities are created.

If the flow aborts with *"Finish the Companion WebUI setup before connecting Home Assistant"*, open
`http://<intercom-ip>/` and create the admin account first.

If the stored credentials stop working, Home Assistant starts a reauthentication flow that asks for
a new code in the same way.

## Intercom Card

When the integration is set up it registers the card as a Lovelace resource automatically
(`/bticino_companion_static/bticino-go-intercom-card.js`). No manual resource entry is needed.

Add it to a dashboard from `Edit dashboard > Add card > BTicino Go Intercom`, or in YAML:

```yaml
type: custom:bticino-go-intercom-card
camera: camera.main_gate
name: Front Door
```

| Option | Required | Description |
| --- | --- | --- |
| `camera` | yes | The companion camera entity of the entrypoint to show. |
| `name` | no | Card title. Defaults to the camera's friendly name. |

What the card does:

- Shows the latest snapshot while idle.
- **Live view** (green phone button): opens a WebRTC session through Home Assistant and shows the
  live video with audio.
- **Incoming call**: shows a ringing overlay and a muted ringing preview. The green button answers
  the call; the microphone is requested only when you answer.
- **Hang up / end live view** (red button), **mute microphone** while in a call, and **unlock** the
  entrypoint.
- Shows *Answered elsewhere* when another client answers the call.

The microphone needs a secure context: open Home Assistant over HTTPS (or `localhost`) for two-way
audio. Over plain HTTP the card still shows video and can answer, but cannot send audio.

## Doorbell notifications with the picture

Every stream-capable entrypoint gets an image entity, **"<entrypoint> last doorbell ring"**. The
Companion takes its picture about 2–3 seconds after each SIP ring, and the entity's state is the
time of that picture: it changes only on a new ring, never on a live view. Trigger notifications on
it rather than on the Ringing sensor, which turns on before the picture exists.

Pick the entity from the entity list (its id is derived from the device and entrypoint names), then:

```yaml
automation:
  - alias: Doorbell notification
    triggers:
      - trigger: state
        entity_id: image.REPLACE_WITH_YOUR_ENTITY
        not_from:
          - unknown
          - unavailable
        not_to:
          - unknown
          - unavailable
    actions:
      - action: notify.mobile_app_REPLACE_WITH_YOUR_PHONE
        data:
          message: Someone is at the door
          data:
            image: /api/image_proxy/image.REPLACE_WITH_YOUR_ENTITY
```

If the Companion cannot open the stream during a ring, no picture is taken and the entity does not
change, so no notification is sent for that ring.

## Entities

Entities are created from what the companion reports and appear or disappear as its capabilities
change. They include:

- **Camera** — one per stream-capable entrypoint, with live WebRTC view and snapshot preview.
- **Image** — `last doorbell ring` per stream-capable entrypoint, the picture taken at the last ring.
- **Binary sensor** — `Ringing`.
- **Sensors** — call state and diagnostics.
- **Buttons** — unlock per entrypoint, system reboot and service restarts when exposed by the
  companion.
- **Switches** — ringer mute and voicemail, when supported by the model.
- **Update** — `Companion Firmware`, when companion update control is enabled and exposed. Installing
  from Home Assistant makes the companion download, verify and install the release itself.

## Updating

- **HACS** — HACS shows new releases of this repository; update and restart Home Assistant.
- **Manual** — replace `custom_components/bticino_companion` and restart Home Assistant.

After an update, reload the dashboard (or clear the browser cache) so the browser loads the new card.

The companion itself is updated separately, from its `Companion Firmware` update entity or by running
its installer again.

## Troubleshooting

### Companion is not discovered

- Confirm the companion is running: `http://<intercom-ip>:8080/api/v3/health` must respond.
- Confirm mDNS (UDP `5353`) is allowed between Home Assistant and the intercom.
- Add the integration manually with the companion URL.

### Pairing fails

- Use the claim code for a new companion and a recovery code for one that is already claimed.
- The recovery code is temporary: issue a new one from the companion WebUI if it is rejected.

### Camera or card does not stream

- Confirm TCP `8080` on the intercom is reachable from Home Assistant, and UDP `8555` (WebRTC ICE)
  from the browser's network.
- Confirm the official BTicino Door Entry app has been paired at least once: an empty
  `/etc/flexisip/users/users.db.txt` on the intercom means SIP/media provisioning is missing.

### The card rings but has no Answer button

The companion SIP user is missing from Flexisip, so incoming calls are not forked to the companion.
Run the companion installer again and restart Flexisip or reboot the intercom. This also happens
after logging out and back in from the BTicino Door Entry app. See
[Answering Calls from Home Assistant](https://github.com/abeilprincipino/BTicino-GO-Companion#answering-calls-from-home-assistant).

### The card does not appear or shows an old version

- Confirm the integration is set up: the card resource is registered when the first entry loads.
- Reload the page with the browser cache cleared.

### Download diagnostics

`Settings > Devices & services > BTicino Companion > ⋮ > Download diagnostics` produces a file to
attach to an [issue](https://github.com/abeilprincipino/BTicino-GO-Integration/issues).

## Development

The integration is a standard Home Assistant custom component under
`custom_components/bticino_companion`:

- `manifest.json` — domain, `local_push` class, zeroconf type.
- `config_flow.py` — user, zeroconf, claim, recovery and reauth steps.
- `api.py`, `websocket.py` — client for the companion `/api/v3` API and its websocket.
- `coordinator.py` — connection and state.
- `dynamic_entities.py` — creates and removes entities as the companion state changes.
- `websocket_api.py` — Home Assistant websocket commands used by the card (WebRTC offer, candidate,
  close, unlock, answer, hang up).
- `www/bticino-go-intercom-card.js` — the intercom card.

Tests run in Docker: `docker build .` (see the [Dockerfile](Dockerfile)).

This integration is developed together with
[BTicino GO Companion](https://github.com/abeilprincipino/BTicino-GO-Companion), since most features
consume companion API capabilities directly.
