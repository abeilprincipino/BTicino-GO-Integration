"""BTicino Companion integration setup."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CompanionApiClient, CompanionAuthError
from .clip_archive import RingClipArchiver
from .const import (
    CARD_FILE,
    CONF_ACCESS_TOKEN,
    CONF_CLIP_RETENTION_DAYS,
    CONF_COMPANION_URL,
    CONF_DEVICE_ID,
    DATA_FRONTEND_REGISTERED,
    DEFAULT_CLIP_RETENTION_DAYS,
    DOMAIN,
    FRONTEND_PATH,
    PLATFORMS,
)
from .coordinator import CompanionCoordinator
from .dynamic_entities import DynamicEntityManager
from .websocket import CompanionWebSocketError
from .websocket_api import async_register_websocket_commands


@dataclass(slots=True)
class IntegrationRuntime:
    """Objects owned by a loaded config entry."""

    client: CompanionApiClient
    coordinator: CompanionCoordinator
    dynamic_entities: DynamicEntityManager
    clip_archive: RingClipArchiver

    async def async_update_base_url(self, base_url: str) -> None:
        """Move the live transports to a newly discovered Companion address."""
        self.client.update_base_url(base_url)
        await self.coordinator.async_update_base_url(base_url)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Companion config entry."""
    client = CompanionApiClient(
        session=async_get_clientsession(hass),
        base_url=str(entry.data[CONF_COMPANION_URL]),
        access_token=str(entry.data[CONF_ACCESS_TOKEN]),
    )
    coordinator = CompanionCoordinator(hass, client, str(entry.data[CONF_DEVICE_ID]))
    try:
        await coordinator.async_start()
    except CompanionAuthError as err:
        await coordinator.async_stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except CompanionWebSocketError as err:
        await coordinator.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    dynamic_entities = DynamicEntityManager(hass, entry, coordinator, client)
    clip_archive = RingClipArchiver(hass, entry, coordinator, client, str(entry.data[CONF_DEVICE_ID]))
    runtime = IntegrationRuntime(
        client=client,
        coordinator=coordinator,
        dynamic_entities=dynamic_entities,
        clip_archive=clip_archive,
    )
    entry.runtime_data = runtime
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await _async_register_frontend(hass)
    dynamic_entities.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await clip_archive.async_start()
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Expose the bundled Lovelace card and its authenticated HA bridge once."""
    if hass.data.get(DATA_FRONTEND_REGISTERED):
        return

    www = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_PATH, str(www), cache_headers=False)]
    )

    # The URL carries a digest of the card, so a new card is a new URL: the
    # frontend and the mobile apps cache modules by URL and would otherwise
    # keep running the old card after an update.
    digest = await hass.async_add_executor_job(
        lambda: hashlib.sha256((www / CARD_FILE).read_bytes()).hexdigest()[:12]
    )
    card_url = f"{FRONTEND_PATH}/{CARD_FILE}?v={digest}"

    resources = hass.data["lovelace"].resources
    if not resources.loaded:
        await resources.async_load()

    for resource in resources.async_items():
        if not resource["url"].startswith(f"{FRONTEND_PATH}/"):
            continue
        if resource["url"] != card_url:
            await resources.async_update_item(
                resource["id"], {"res_type": "module", "url": card_url}
            )
        break
    else:
        await resources.async_create_item({"res_type": "module", "url": card_url})

    async_register_websocket_commands(hass)
    hass.data[DATA_FRONTEND_REGISTERED] = True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Companion config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if isinstance(runtime, IntegrationRuntime):
        await runtime.clip_archive.async_stop()
        await runtime.coordinator.async_stop()
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A new retention takes effect through a reload, which restarts the archive.

    Entry data updates (reauth, a new address) also call this; they must not reload.
    """
    runtime = getattr(entry, "runtime_data", None)
    retention = int(entry.options.get(CONF_CLIP_RETENTION_DAYS, DEFAULT_CLIP_RETENTION_DAYS))
    if isinstance(runtime, IntegrationRuntime) and runtime.clip_archive.retention_days == retention:
        return
    await hass.config_entries.async_reload(entry.entry_id)
