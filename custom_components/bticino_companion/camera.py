"""Native WebRTC camera entities for Companion entrypoint streams."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature, WebRTCAnswer, WebRTCError, WebRTCSendMessage
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from webrtc_models import RTCIceCandidateInit

from . import IntegrationRuntime
from .api import CompanionApiError
from .const import DATA_CAMERA_ENTITIES
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .entity import CompanionAvailabilityMixin

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Register dynamic entrypoint cameras."""
    del hass, async_add_entities
    runtime: IntegrationRuntime = entry.runtime_data
    await runtime.dynamic_entities.async_register_platform("camera", async_get_current_platform())


class CompanionEntrypointCamera(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], Camera):
    """Expose one Companion WebRTC camera per stream-capable entrypoint."""

    _attr_has_entity_name = True
    _attr_force_update = True
    _attr_icon = "mdi:video"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client,
        entrypoint_id: str,
        name: str,
    ) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._entry = entry
        self._client = client
        self._entrypoint_id = entrypoint_id
        self._attr_name = name
        self._attr_unique_id = f"{entry.unique_id}_camera_{entrypoint_id}"
        self._active_webrtc_sessions: set[str] = set()

    @property
    def device_info(self):
        """Associate cameras with the Companion device."""
        return device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        """Require both the push transport and stream availability."""
        state = self.coordinator.data
        entrypoint = (
            next((item for item in state.entrypoints if item.id == self._entrypoint_id), None)
            if state
            else None
        )
        return bool(super().available and entrypoint and entrypoint.capabilities.stream)

    @property
    def is_streaming(self) -> bool:
        """Report whether this entrypoint owns the current preview or call stream."""
        state = self.coordinator.data
        return bool(
            state
            and state.active_entrypoint_id == self._entrypoint_id
            and state.call_state in {"preview", "active"}
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | bool]:
        """Expose entrypoint-scoped call state for the bundled dynamic card."""
        state = self.coordinator.data
        entrypoint = (
            next((item for item in state.entrypoints if item.id == self._entrypoint_id), None)
            if state
            else None
        )
        active = bool(state and state.active_entrypoint_id == self._entrypoint_id)
        call_state = state.call_state if state and active else "idle"
        return {
            "bticino_entrypoint_id": self._entrypoint_id,
            "bticino_entrypoint_label": entrypoint.label if entrypoint and entrypoint.label else self._attr_name,
            "bticino_call_state": call_state,
            "bticino_is_active_entrypoint": active,
            "bticino_is_ringing": call_state == "ringing",
            # An answerable call requires a real SIP dialog. Without Flexisip
            # provisioning no INVITE arrives, incoming_dialog_id stays unset and
            # the card must not offer an Answer button that cannot work.
            "bticino_can_answer": bool(
                active and call_state == "ringing" and state and state.incoming_dialog_id
            ),
            "bticino_answered_elsewhere": bool(
                active and state and state.last_call_end_reason == "elsewhere"
            ),
        }

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Send Home Assistant's offer to Companion and return its SDP answer."""
        try:
            answer_sdp = await self._async_open_webrtc_session(offer_sdp, session_id, "native_camera")
        except CompanionApiError as err:
            send_message(WebRTCError("webrtc_offer_failed", str(err)))
            return
        except ValueError as err:
            send_message(WebRTCError("webrtc_offer_failed", str(err)))
            return
        send_message(WebRTCAnswer(answer_sdp))

    async def async_handle_card_webrtc_offer(self, offer_sdp: str, session_id: str) -> str:
        """Create a session for the bundled card through this camera authority."""
        return await self._async_open_webrtc_session(offer_sdp, session_id, "bticino_card")

    async def _async_open_webrtc_session(self, offer_sdp: str, session_id: str, origin: str) -> str:
        """Open a server-auditable session owned by this camera."""
        self._active_webrtc_sessions.add(session_id)
        try:
            response = await self._client.async_webrtc_offer(
                entrypoint_id=self._entrypoint_id,
                offer_sdp=offer_sdp,
                session_id=session_id,
                origin=origin,
                ice_servers=await self._async_ice_servers(),
            )
        except CompanionApiError:
            self._active_webrtc_sessions.discard(session_id)
            raise

        answer_sdp = response.get("answer_sdp")
        if not isinstance(answer_sdp, str) or not answer_sdp.strip():
            self._active_webrtc_sessions.discard(session_id)
            try:
                await self._client.async_webrtc_close(session_id=session_id)
            except CompanionApiError:
                _LOGGER.debug("Unable to close invalid Companion WebRTC session %s", session_id)
            raise ValueError("Companion returned an empty answer_sdp")
        return answer_sdp

    async def _async_ice_servers(self) -> list[dict[str, Any]]:
        """Reuse Home Assistant's resolved ICE servers (incl. Nabu Casa Cloud TURN) for the Companion peer.

        Companion cannot reach Home Assistant Cloud or read configuration.yaml itself, so it
        needs these forwarded per-offer; without them it only gathers LAN host candidates and
        remote clients (e.g. cellular) cannot reach it.
        """
        try:
            client_config = await self.async_get_webrtc_client_configuration()
        except Exception as err:  # noqa: BLE001 - never block the offer on ICE server discovery
            _LOGGER.debug("Unable to resolve WebRTC ICE servers for %s: %s", self._entrypoint_id, err)
            return []

        servers: list[dict[str, Any]] = []
        for server in client_config.configuration.ice_servers:
            urls = server.urls if isinstance(server.urls, list) else [server.urls]
            urls = [url for url in urls if url]
            if not urls:
                continue
            payload: dict[str, Any] = {"urls": urls}
            if server.username:
                payload["username"] = server.username
            if server.credential:
                payload["credential"] = server.credential
            servers.append(payload)
        return servers

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Forward an ICE candidate to the matching Companion session."""
        await self.async_handle_card_webrtc_candidate(session_id, _candidate_to_payload(candidate))

    async def async_handle_card_webrtc_candidate(self, session_id: str, candidate: dict[str, Any]) -> None:
        """Forward a bundled-card ICE candidate for one of this camera's sessions."""
        if session_id not in self._active_webrtc_sessions:
            raise ValueError("Unknown WebRTC session for this camera")
        await self._client.async_webrtc_candidate(session_id=session_id, candidate=candidate)

    async def async_handle_card_unlock(self) -> None:
        """Unlock the entrypoint represented by this camera."""
        await self._client.async_unlock_entrypoint(self._entrypoint_id)

    async def async_handle_card_call_answer(self) -> None:
        """Answer the call ringing on this camera's entrypoint."""
        await self._client.async_call_answer()

    async def async_handle_card_call_hangup(self) -> None:
        """End the active call. Idempotent on the Companion side."""
        await self._client.async_call_hangup()

    async def async_close_card_webrtc_session(self, session_id: str) -> None:
        """Close a bundled-card session only when it belongs to this camera."""
        if session_id not in self._active_webrtc_sessions:
            return
        self._active_webrtc_sessions.remove(session_id)
        await self._async_close_webrtc_session(session_id)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Release the Companion media session when HA closes its peer connection."""
        if session_id not in self._active_webrtc_sessions:
            return
        self._active_webrtc_sessions.remove(session_id)
        self.hass.async_create_background_task(
            self._async_close_webrtc_session(session_id),
            f"close Companion WebRTC session {session_id}",
        )

    async def _async_close_webrtc_session(self, session_id: str) -> None:
        try:
            await self._client.async_webrtc_close(session_id=session_id)
        except CompanionApiError as err:
            _LOGGER.debug("Unable to close Companion WebRTC session %s: %s", session_id, err)

    async def async_added_to_hass(self) -> None:
        """Make this authoritative camera discoverable by the frontend bridge."""
        await super().async_added_to_hass()
        self.hass.data.setdefault(DATA_CAMERA_ENTITIES, {})[self.entity_id] = self

    async def async_will_remove_from_hass(self) -> None:
        """Release every active Companion media session before removing the camera."""
        self.hass.data.get(DATA_CAMERA_ENTITIES, {}).pop(self.entity_id, None)
        for session_id in tuple(self._active_webrtc_sessions):
            self.close_webrtc_session(session_id)
        await super().async_will_remove_from_hass()

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Return the latest passive Companion snapshot, when available."""
        del width, height
        try:
            return await self._client.async_entrypoint_snapshot_latest(self._entrypoint_id)
        except CompanionApiError as err:
            _LOGGER.debug("Unable to fetch snapshot for %s: %s", self._entrypoint_id, err)
            return None


def _candidate_to_payload(candidate: RTCIceCandidateInit) -> dict[str, Any]:
    """Serialize a WebRTC candidate for the Companion API."""
    raw = candidate.to_dict()
    payload = {"candidate": str(raw.get("candidate", "")).strip()}
    for key in ("sdpMid", "sdpMLineIndex", "usernameFragment"):
        value = raw.get(key)
        if value is not None:
            payload[key] = value
    return payload
