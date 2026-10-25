"""Doorbell picture image entities."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .api import CompanionApiError
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .entity import CompanionAvailabilityMixin
from .models import Entrypoint

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Register dynamic doorbell picture entities."""
    del hass, async_add_entities
    runtime: IntegrationRuntime = entry.runtime_data
    await runtime.dynamic_entities.async_register_platform("image", async_get_current_platform())


class CompanionRingSnapshotImage(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], ImageEntity):
    """The picture taken at the last doorbell ring of one entrypoint.

    Its state is the time of that picture, so it changes only on a new ring.
    Notification automations trigger on it rather than on Ringing, whose picture
    is not taken yet when the ring starts. A live view never changes it.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:doorbell-video"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client,
        entrypoint_id: str,
        name: str,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._entry = entry
        # ImageEntity keeps its own HTTP client in self._client.
        self._companion = client
        self._entrypoint_id = entrypoint_id
        self._attr_name = f"{name} last doorbell ring"
        self._attr_unique_id = f"{entry.unique_id}_ring_snapshot_{entrypoint_id}"
        self._cached: bytes | None = None
        self._cached_at: datetime | None = None

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)

    def _entrypoint(self) -> Entrypoint | None:
        state = self.coordinator.data
        if not state:
            return None
        return next((item for item in state.entrypoints if item.id == self._entrypoint_id), None)

    @property
    def available(self) -> bool:
        entrypoint = self._entrypoint()
        return bool(super().available and entrypoint and entrypoint.capabilities.stream)

    @property
    def image_last_updated(self) -> datetime | None:
        """The time of the last doorbell picture: the entity's state."""
        entrypoint = self._entrypoint()
        return entrypoint.ring_snapshot_updated_at if entrypoint else None

    async def async_image(self) -> bytes | None:
        """Return the doorbell picture, fetched once per new ring."""
        updated = self.image_last_updated
        if updated is None:
            return None
        if self._cached is not None and self._cached_at == updated:
            return self._cached
        try:
            image = await self._companion.async_entrypoint_snapshot_ring(self._entrypoint_id)
        except CompanionApiError as err:
            _LOGGER.debug("Unable to fetch the doorbell picture for %s: %s", self._entrypoint_id, err)
            return None
        self._cached, self._cached_at = image, updated
        return image
