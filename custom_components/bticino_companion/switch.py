"""Switch entities for Companion controls."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .entity import CompanionAvailabilityMixin


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up static mute and register dynamic voicemail lifecycle management."""
    runtime: IntegrationRuntime = entry.runtime_data
    async_add_entities([CompanionMuteSwitch(entry, runtime.coordinator, runtime.client)])
    await runtime.dynamic_entities.async_register_platform("switch", async_get_current_platform())


class _CompanionSwitch(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)


class CompanionMuteSwitch(_CompanionSwitch):
    _requires_intercom = True

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, client) -> None:
        super().__init__(entry, coordinator, "mute", "Mute", "mdi:microphone-off")
        self._client = client

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.muted)

    async def async_turn_on(self, **kwargs: Any) -> None:
        del kwargs
        await self._client.async_set_muted(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        del kwargs
        await self._client.async_set_muted(False)


class CompanionVoicemailSwitch(_CompanionSwitch):
    _requires_intercom = True

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, client) -> None:
        super().__init__(entry, coordinator, "voicemail", "Voicemail", "mdi:voicemail")
        self._client = client

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.voicemail_enabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        del kwargs
        await self._client.async_set_voicemail_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        del kwargs
        await self._client.async_set_voicemail_enabled(False)
