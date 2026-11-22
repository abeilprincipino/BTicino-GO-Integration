"""Button entities for Companion commands."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .entity import CompanionAvailabilityMixin


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Register button lifecycle management for dynamic Companion controls."""
    del hass, async_add_entities
    runtime: IntegrationRuntime = entry.runtime_data
    await runtime.dynamic_entities.async_register_platform("button", async_get_current_platform())


class _CompanionButton(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], ButtonEntity):
    """Base for fixed v3 command buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)


class CompanionEntrypointButton(_CompanionButton):
    """Run a fixed entrypoint-scoped v3 command."""

    _attr_entity_category = None
    _requires_intercom = True

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client,
        entrypoint_id: str,
        name: str,
    ) -> None:
        super().__init__(entry, coordinator, f"unlock_{entrypoint_id}", name, "mdi:door-open")
        self._entrypoint_id = entrypoint_id
        self._client = client

    @property
    def available(self) -> bool:
        state = self.coordinator.data
        entrypoint = next((item for item in state.entrypoints if item.id == self._entrypoint_id), None) if state else None
        return bool(super().available and entrypoint and entrypoint.availability.unlock)

    async def async_press(self) -> None:
        await self._client.async_unlock_entrypoint(self._entrypoint_id)


class CompanionRebootButton(_CompanionButton):
    """Reboot the intercom through the typed REST control endpoint."""

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, client) -> None:
        super().__init__(entry, coordinator, "reboot", "Reboot", "mdi:restart")
        self._client = client

    async def async_press(self) -> None:
        self.coordinator.async_expect_disconnect()
        try:
            await self._client.async_reboot()
        except Exception:
            self.coordinator.async_cancel_expected_disconnect()
            raise


class CompanionServiceRestartButton(_CompanionButton):
    """Restart one Companion service through the typed REST endpoint."""

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, client, service: str) -> None:
        super().__init__(entry, coordinator, f"restart_{service}", f"Restart {service}", "mdi:restart")
        self._client = client
        self._service = service

    async def async_press(self) -> None:
        await self._client.async_restart_service(self._service)
