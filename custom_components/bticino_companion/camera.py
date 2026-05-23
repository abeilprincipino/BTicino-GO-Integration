"""Camera platform for BTicino Companion entrypoint streams."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .api import CompanionApiError
from .const import CONF_COMPANION_URL, DEFAULT_COMPANION_URL
from .coordinator import CompanionCoordinator
from .device_info import build_device_info

_DEFAULT_RTSP_PORT = 8554
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: IntegrationRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    known_entrypoint_ids: set[str] = set()

    def _sync_entrypoint_cameras() -> None:
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
        rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
        if not isinstance(rows, list):
            return

        new_entities: list[CompanionEntrypointCamera] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("has_stream") is False:
                continue

            entrypoint_id = str(row.get("id", "")).strip()
            if not entrypoint_id or entrypoint_id in known_entrypoint_ids:
                continue

            entrypoint_label = str(row.get("label") or entrypoint_id).strip() or entrypoint_id
            devaddr = str(row.get("devaddr", "")).strip() or None
            known_entrypoint_ids.add(entrypoint_id)
            new_entities.append(
                CompanionEntrypointCamera(
                    entry=entry,
                    coordinator=coordinator,
                    entrypoint_id=entrypoint_id,
                    entrypoint_label=entrypoint_label,
                    devaddr=devaddr,
                )
            )

        if new_entities:
            async_add_entities(new_entities)

    _sync_entrypoint_cameras()
    entry.async_on_unload(coordinator.async_add_listener(_sync_entrypoint_cameras))


class CompanionEntrypointCamera(CoordinatorEntity[CompanionCoordinator], Camera):
    """Camera entity backed by a specific entrypoint stream."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:video"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        *,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        entrypoint_id: str,
        entrypoint_label: str,
        devaddr: str | None,
    ) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._entry = entry
        self._entrypoint_id = entrypoint_id
        self._devaddr = devaddr
        self._client = entry.runtime_data.client
        self._companion_url = str(entry.options.get(CONF_COMPANION_URL, entry.data.get(CONF_COMPANION_URL, DEFAULT_COMPANION_URL))).strip()
        self._attr_name = entrypoint_label
        self._attr_unique_id = f"{entry.entry_id}_entrypoint_camera_{entrypoint_id}"
        self.stream_options["rtsp_transport"] = "tcp"

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.coordinator.entities_available

    @property
    def is_streaming(self) -> bool:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        state = data.get("state", {}) if isinstance(data, dict) else {}
        stream_active = bool((state if isinstance(state, dict) else {}).get("stream_active"))
        active_entrypoint = str((state if isinstance(state, dict) else {}).get("active_entrypoint", "")).strip()
        return stream_active and active_entrypoint == self._entrypoint_id

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "entrypoint_id": self._entrypoint_id,
        }
        if self._devaddr:
            attrs["devaddr"] = self._devaddr
        return attrs

    async def stream_source(self) -> str | None:
        return _build_rtsp_stream_url(self._companion_url, self.coordinator.data, self._entrypoint_id)

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        del width, height
        try:
            return await self._client.async_entrypoint_snapshot_latest(self._entrypoint_id)
        except CompanionApiError as err:
            _LOGGER.debug("Snapshot latest fallback failed for %s: %s", self._entrypoint_id, err)
            return None


def _build_rtsp_stream_url(companion_url: str, data: dict[str, Any] | None, entrypoint_id: str) -> str | None:
    payload = data if isinstance(data, dict) else {}
    state = payload.get("state", {}) if isinstance(payload, dict) else {}
    stream_info = _entrypoint_stream_info(payload, entrypoint_id)
    if stream_info is None:
        return None
    stream_path, stream_port = stream_info

    host = ""
    if companion_url:
        parsed = urlsplit(companion_url)
        host = parsed.hostname or ""

    if not host:
        diagnostics = state.get("diagnostics", {}) if isinstance(state, dict) else {}
        network = diagnostics.get("network", {}) if isinstance(diagnostics, dict) else {}
        host = str((network if isinstance(network, dict) else {}).get("ip", "")).strip()

    if not host:
        return None

    host_for_url = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"rtsp://{host_for_url}:{stream_port}/{stream_path}"


def _entrypoint_stream_info(data: dict[str, Any], entrypoint_id: str) -> tuple[str, int] | None:
    entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
    rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
    if not isinstance(rows, list):
        return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")).strip() != entrypoint_id:
            continue
        stream_path = str(row.get("rtsp_path", "")).strip().lstrip("/")
        if not stream_path:
            return None
        stream_port = _positive_port(row.get("rtsp_port"), _DEFAULT_RTSP_PORT)
        return stream_path, stream_port
    return None


def _positive_port(raw: Any, fallback: int) -> int:
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return fallback
    if port <= 0 or port > 65535:
        return fallback
    return port
