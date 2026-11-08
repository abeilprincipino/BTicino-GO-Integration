# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Home Assistant custom integration (`custom_components/bticino_companion`, domain `bticino_companion`) plus a bundled Lovelace card for **BTicino GO Companion**, a service running on a rooted BTicino Classe 300X/100X intercom. Its sibling repo is `abeilprincipino/BTicino-GO-Companion` (often checked out at `../BTicino-GO-Companion`): most features consume a Companion `/api/v3` capability, so a change here may need a Companion version that provides it.

## Commands

Tests are plain `unittest` (no pytest, no linter/formatter config in the repo). CI runs them inside the Home Assistant image:

```sh
docker build --target test .        # what CI runs (ghcr.io/home-assistant/home-assistant:stable)
```

Without Docker, use a virtualenv with `homeassistant` (and `asyncssh`) installed, from the repo root:

```sh
python -m unittest discover -s tests                     # all tests
python -m unittest tests.test_entities                   # one module
python -m unittest tests.test_entities -k unlock          # tests matching a pattern
```

- HA-dependent test modules raise `unittest.SkipTest` when `homeassistant` cannot be imported. With a plain system Python, only a few pure-Python tests (such as `test_models_protocol`) actually run; the rest skip, or fail on a missing `aiohttp`. A green run without HA proves little, so check the skip count.
- CI uses the floating `home-assistant:stable` image, so a new HA release can break CI without any change here (e.g. HA 2026.11 moved `ImageEntity`). A local venv pinned to an older HA will not catch this.
- Tests put `custom_components/` on `sys.path` and import the package as `bticino_companion`, not `custom_components.bticino_companion`.

CI (`.github/workflows/ci.yml`) also runs HACS validation and hassfest; both must pass on PRs.

## Architecture

**Push-only state.** `CompanionCoordinator` (`coordinator.py`) is a `DataUpdateCoordinator` that never polls: `CompanionWebSocket` (`websocket.py`) holds a persistent connection to `/api/v3/ws`, and each `state` message is parsed into an immutable `CompanionState` (`models.py`) and pushed via `async_set_updated_data`. The socket also handles `event`/`trace`/`error`/`pong`, pings, exponential reconnect, "expected disconnect" around a reboot, and listens to zeroconf (`_bticomp._tcp.local.`) to follow the Companion to a new IP. Entity availability follows the socket's connected state (`entity.py`), not data freshness. `auth_failed` on the socket becomes `ConfigEntryAuthFailed` → reauth flow.

**Capability-driven entities.** Platform `async_setup_entry` functions do not add entities themselves; they register their `EntityPlatform` with `DynamicEntityManager` (`dynamic_entities.py`). On every coordinator update it computes the desired entity set from `CompanionState` (stream-capable entrypoint → camera + "last ring" image; unlock capability → button; `reboot_enabled`, exposed services, voicemail, update → their entities) and adds/removes entities (also from the entity registry) to match. Unique IDs are `{entry.unique_id}_{kind}_{id}`. To add a capability-gated entity, add it to `_desired_entities`, not to the platform's setup.

**REST client.** `CompanionApiClient` (`api.py`) wraps the Companion REST endpoints (paths in `const.py`) with a bearer token obtained at pairing. WebRTC signaling goes over a separate per-session websocket (`/api/v3/webrtc/ws`) managed inside the client, which delivers trickled ICE candidates and late "source failed" messages through callbacks.

**Two WebRTC entry points, one camera authority.** `CompanionEntrypointCamera` (`camera.py`) owns all sessions for an entrypoint:
- HA's native camera WebRTC (`async_handle_async_webrtc_offer`): Companion may send candidates/failures before its answer, so they are buffered and flushed after the `WebRTCAnswer`.
- The bundled card, via HA websocket commands registered in `websocket_api.py` (`bticino_companion/card_webrtc_offer`, `card_webrtc_candidates` subscription, `card_webrtc_candidate`, `card_webrtc_close`, `card_unlock`, `card_call_answer`, `card_call_hangup`, `card_ring_clips`). Commands locate the camera through `hass.data[DATA_CAMERA_ENTITIES]` keyed by entity_id. A card that subscribes to candidates before its offer gets trickle; an older card without the subscription gets all candidates inside the answer — keep that backward compatibility.
- Both paths forward HA's resolved ICE servers (including Home Assistant Cloud TURN and `web_rtc: ice_servers`) to the Companion with every offer, since the Companion cannot discover them itself. The video stream is browser ↔ intercom (UDP 8555), never relayed through HA.

**The card** (`www/bticino-go-intercom-card.js`) is a single vanilla `HTMLElement` (no build step). It never talks to the Companion directly or holds its token; everything goes through the HA websocket commands above. `__init__.py` serves `www/` as a static path and auto-creates/updates a Lovelace module resource whose URL carries a SHA-256 digest of the card file, so editing the card changes its URL and busts frontend/mobile caches.

**Ring clips.** `RingClipArchiver` (`clip_archive.py`) downloads the Companion's single latest clip per entrypoint into HA's media folder (`bticino_go/`), pairs it with the ring snapshot as a poster, purges by the `clip_retention_days` option, and fires `bticino_companion_ring_clip_saved`. Changing the retention option reloads the entry; entry-data updates (reauth, new address) deliberately do not (`_async_options_updated`).

**Config flow** (`config_flow.py`) has two branches: connect to an installed Companion (manual URL or zeroconf → claim code for an unclaimed Companion, recovery code for an already-claimed one), or install the Companion over SSH (`installer.py`, `asyncssh`): runs the installer, parses its progress/`FAIL:` lines, pairs locally over SSH, optionally sets the WebUI owner credentials, and offers a reboot. The SSH password is never stored; the host key is pinned at install and checked on reconfigure.

## Branches, releases, versions

- `develop` is the default branch and every PR targets it; `main` only receives what already shipped in a beta (merge `develop` → `main` with a merge commit). Hotfixes branch from `main`, then `main` is merged back into `develop`. Full process in `CONTRIBUTING.md`.
- Versions follow Home Assistant style: `2026.11.1` (or `2026.11.1.2`) for releases, `2026.11.1b1` for betas. The Release workflow writes the tag into `manifest.json` `version` and attaches `bticino_companion.zip`, which HACS installs (`zip_release`). Do not bump `manifest.json` `version` by hand.
- PR descriptions follow `.github/pull_request_template.md`: required Companion version, and whether it was tried on a real Home Assistant with a Companion.
- All GitHub links and operations target the forks `abeilprincipino/BTicino-GO-Integration` / `abeilprincipino/BTicino-GO-Companion`. The original `r0bb10` repos are archived; mention them only in the README credits and the LICENSE.
