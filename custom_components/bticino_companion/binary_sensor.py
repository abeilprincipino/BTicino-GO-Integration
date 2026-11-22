"""Binary sensors for pushed BTicino Companion state."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .entity import CompanionAvailabilityMixin


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the ringing binary sensor."""
    del hass
    runtime: IntegrationRuntime = entry.runtime_data
    async_add_entities([CompanionRingingBinarySensor(entry, runtime.coordinator)])


class CompanionRingingBinarySensor(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], BinarySensorEntity):
    """Report whether a configured entrypoint is ringing."""

    _attr_has_entity_name = True
    _attr_name = "Ringing"
    # Without the bus no ring can be seen.
    _requires_intercom = True
    _attr_icon = "mdi:bell-ring"

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ringing"

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)

    @property
    def is_on(self) -> bool:
        state = self.coordinator.data
        return bool(state and state.call_state == "ringing" and state.active_entrypoint_id)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        state = self.coordinator.data
        return {"entrypoint_id": state.active_entrypoint_id} if state and state.active_entrypoint_id else None
