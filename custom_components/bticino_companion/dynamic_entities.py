"""Lifecycle management for Companion entities selected by server state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform

from .coordinator import CompanionCoordinator
from .models import CompanionState

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _DesiredEntity:
    """One entity that should exist for the current Companion state."""

    platform: str
    unique_id: str
    create: Callable[[], Entity]


class DynamicEntityManager:
    """Keep capability-gated entities in sync with one config entry's state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._client = client
        self._platforms: dict[str, EntityPlatform] = {}
        self._managed: dict[str, str] = {}
        self._removed: set[str] = set()
        self._lock = asyncio.Lock()

    def async_start(self) -> None:
        """Subscribe once for all dynamic platforms in this config entry."""
        self._entry.async_on_unload(
            self._coordinator.async_add_listener(self._async_schedule_reconcile)
        )

    async def async_register_platform(self, platform: str, entity_platform: EntityPlatform) -> None:
        """Register an entity platform and immediately reconcile its entities."""
        self._platforms[platform] = entity_platform
        await self.async_reconcile()

    def _async_schedule_reconcile(self) -> None:
        """Reconcile after coordinator data changes without blocking its listeners."""
        self._entry.async_create_background_task(
            self._hass,
            self.async_reconcile(),
            f"{self._entry.entry_id} dynamic entity reconciliation",
        )

    async def async_reconcile(self) -> None:
        """Add and remove actual platform entities for the latest Companion state."""
        async with self._lock:
            desired = {
                entity.unique_id: entity
                for entity in self._desired_entities(self._coordinator.data)
                if entity.platform in self._platforms
            }

            for unique_id, platform_name in tuple(self._managed.items()):
                if unique_id in desired and desired[unique_id].platform == platform_name:
                    continue
                platform = self._platforms.get(platform_name)
                if platform is not None:
                    if await self._async_remove_entity(platform, unique_id):
                        _LOGGER.info("Removed Companion dynamic entity: %s", unique_id)
                        self._removed.add(unique_id)
                self._managed.pop(unique_id, None)

            for platform_name, platform in self._platforms.items():
                additions = [
                    (desired_entity.unique_id, desired_entity.create())
                    for desired_entity in desired.values()
                    if desired_entity.platform == platform_name
                    and self._managed.get(desired_entity.unique_id) != platform_name
                ]
                if additions:
                    await platform.async_add_entities([entity for _, entity in additions])
                    for unique_id, _ in additions:
                        if unique_id in self._removed:
                            _LOGGER.info("Re-added Companion dynamic entity: %s", unique_id)
                            self._removed.remove(unique_id)
                        else:
                            _LOGGER.info("Added Companion dynamic entity: %s", unique_id)

            self._managed = {
                unique_id: desired_entity.platform
                for unique_id, desired_entity in desired.items()
            }

    async def _async_remove_entity(self, platform: EntityPlatform, unique_id: str) -> bool:
        """Remove an entity that the Companion no longer declares."""
        for entity_id, entity in tuple(platform.entities.items()):
            if entity.unique_id == unique_id:
                await platform.async_remove_entity(entity_id)
                er.async_get(self._hass).async_remove(entity_id)
                return True
        return False

    def _desired_entities(self, state: CompanionState | None) -> tuple[_DesiredEntity, ...]:
        """Build every dynamic entity directly from the current Companion state."""
        if state is None:
            return ()

        # Imports stay here to keep platform modules free of an import cycle.
        from .button import CompanionEntrypointButton, CompanionRebootButton, CompanionServiceRestartButton
        from .camera import CompanionEntrypointCamera
        from .image import CompanionRingSnapshotImage
        from .switch import CompanionVoicemailSwitch
        from .update import CompanionUpdate

        desired: list[_DesiredEntity] = []
        for entrypoint in state.entrypoints:
            if entrypoint.capabilities.stream:
                unique_id = f"{self._entry.unique_id}_camera_{entrypoint.id}"
                desired.append(
                    _DesiredEntity(
                        "camera",
                        unique_id,
                        lambda entrypoint=entrypoint: CompanionEntrypointCamera(
                            self._entry,
                            self._coordinator,
                            self._client,
                            entrypoint.id,
                            entrypoint.label or entrypoint.id,
                        ),
                    )
                )
                desired.append(
                    _DesiredEntity(
                        "image",
                        f"{self._entry.unique_id}_ring_snapshot_{entrypoint.id}",
                        lambda entrypoint=entrypoint: CompanionRingSnapshotImage(
                            self._hass,
                            self._entry,
                            self._coordinator,
                            self._client,
                            entrypoint.id,
                            entrypoint.label or entrypoint.id,
                        ),
                    )
                )
            if not entrypoint.capabilities.unlock:
                continue
            unique_id = f"{self._entry.unique_id}_unlock_{entrypoint.id}"
            desired.append(
                _DesiredEntity(
                    "button",
                    unique_id,
                    lambda entrypoint=entrypoint: CompanionEntrypointButton(
                        self._entry,
                        self._coordinator,
                        self._client,
                        entrypoint.id,
                        entrypoint.label or entrypoint.id,
                    ),
                )
            )

        if state.reboot_enabled:
            desired.append(
                _DesiredEntity(
                    "button",
                    f"{self._entry.unique_id}_reboot",
                    lambda: CompanionRebootButton(self._entry, self._coordinator, self._client),
                )
            )
        for service in state.services:
            if not service.enabled or not service.exposed:
                continue
            desired.append(
                _DesiredEntity(
                    "button",
                    f"{self._entry.unique_id}_restart_{service.name}",
                    lambda service=service: CompanionServiceRestartButton(
                        self._entry, self._coordinator, self._client, service.name
                    ),
                )
            )
        if state.voicemail_enabled is not None:
            desired.append(
                _DesiredEntity(
                    "switch",
                    f"{self._entry.unique_id}_voicemail",
                    lambda: CompanionVoicemailSwitch(self._entry, self._coordinator, self._client),
                )
            )
        if state.update.enabled and state.update.exposed:
            desired.append(
                _DesiredEntity(
                    "update",
                    f"{self._entry.unique_id}_firmware_update",
                    lambda: CompanionUpdate(self._entry, self._coordinator, self._client),
                )
            )
        return tuple(desired)
