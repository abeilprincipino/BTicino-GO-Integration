"""Home Assistant actions offered by the BTicino Companion integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .api import CompanionApiError
from .const import DOMAIN

if TYPE_CHECKING:
    from . import IntegrationRuntime

SERVICE_HANGUP_CALL = "hangup_call"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

HANGUP_CALL_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's actions once, independent of any config entry."""
    if hass.services.has_service(DOMAIN, SERVICE_HANGUP_CALL):
        return

    async def _async_hangup_call(call: ServiceCall) -> None:
        runtime = _runtime_for_call(hass, call)
        try:
            await runtime.client.async_call_hangup()
        except CompanionApiError as err:
            raise HomeAssistantError(f"Companion did not end the call: {err}") from err

    hass.services.async_register(DOMAIN, SERVICE_HANGUP_CALL, _async_hangup_call, schema=HANGUP_CALL_SCHEMA)


def _runtime_for_call(hass: HomeAssistant, call: ServiceCall) -> IntegrationRuntime:
    """Resolve the Companion an action targets; with one Companion it may be omitted."""
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id is None:
        loaded = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not loaded:
            raise ServiceValidationError("No BTicino Companion is loaded")
        if len(loaded) > 1:
            raise ServiceValidationError(
                f"Several BTicino Companions are loaded; set {ATTR_CONFIG_ENTRY_ID} to choose one"
            )
        entry = loaded[0]
    else:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(f"{entry_id} is not a BTicino Companion config entry")
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(f"BTicino Companion {entry.title} is not loaded")
    return entry.runtime_data
