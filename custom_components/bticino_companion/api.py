"""REST client for Companion pairing and typed control endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientResponse, ClientSession, WSMsgType

from .const import (
    API_PATH_PAIR_CHALLENGE,
    API_PATH_PAIR_CLAIM,
    API_PATH_RECOVER_BEARER,
    API_PATH_AUDIO_MUTE,
    API_PATH_AUDIO_UNMUTE,
    API_PATH_AUTH_STATUS,
    API_PATH_ENTRYPOINT_UNLOCK,
    API_PATH_SYSTEM_REBOOT,
    API_PATH_SYSTEM_SERVICE_RESTART,
    API_PATH_SNAPSHOT_LATEST,
    API_PATH_UPDATE_INSTALL,
    API_PATH_VOICEMAIL_DISABLE,
    API_PATH_VOICEMAIL_ENABLE,
    API_PATH_WEBRTC_WS,
)


class CompanionApiError(Exception):
    """Raised when a Companion REST request fails."""

    def __init__(self, message: str, code: str = "cannot_connect") -> None:
        super().__init__(message)
        self.code = code


class CompanionAuthError(CompanionApiError):
    """Raised when a Companion request is unauthorized."""


@dataclass
class _WebRTCSession:
    websocket: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closing: bool = False


class CompanionApiClient:
    """REST operations that are not available through the WebSocket protocol."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        access_token: str = "",
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._webrtc_sessions: dict[str, _WebRTCSession] = {}

    @property
    def access_token(self) -> str:
        """Return the active bearer token."""
        return self._access_token

    @property
    def session(self) -> ClientSession:
        """Return the shared Home Assistant HTTP session."""
        return self._session

    @property
    def base_url(self) -> str:
        """Return the current Companion base URL."""
        return self._base_url

    def update_base_url(self, base_url: str) -> None:
        """Use a newly discovered Companion endpoint."""
        self._base_url = base_url.rstrip("/")

    async def async_get_pairing_status(self) -> dict[str, Any]:
        """Return the Companion's public, non-secret pairing state."""
        return await self._async_request("GET", API_PATH_AUTH_STATUS, auth=False)

    async def async_pair_challenge(self) -> dict[str, Any]:
        return await self._async_request("POST", API_PATH_PAIR_CHALLENGE, auth=False)

    async def async_pair_claim(self, *, challenge_id: str, claim_code: str) -> dict[str, Any]:
        payload = await self._async_request(
            "POST",
            API_PATH_PAIR_CLAIM,
            auth=False,
            json_body={
                "challenge_id": challenge_id,
                "claim_code": claim_code,
            },
        )
        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            raise CompanionApiError("pair claim response did not contain an access token")
        self._access_token = access_token
        return payload

    async def async_recover_bearer(self, repair_code: str) -> dict[str, Any]:
        """Exchange an owner-issued repair code for a replacement bearer token."""
        payload = await self._async_request(
            "POST", API_PATH_RECOVER_BEARER, auth=False, json_body={"repair_code": repair_code}
        )
        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            raise CompanionApiError("bearer recovery response did not contain an access token")
        self._access_token = access_token
        return payload

    async def async_unlock_entrypoint(self, entrypoint_id: str) -> dict[str, Any]:
        return await self._async_request(
            "POST", API_PATH_ENTRYPOINT_UNLOCK.format(entrypoint_id=quote(entrypoint_id, safe="")), auth=True
        )

    async def async_set_muted(self, muted: bool) -> dict[str, Any]:
        return await self._async_request("POST", API_PATH_AUDIO_MUTE if muted else API_PATH_AUDIO_UNMUTE, auth=True)

    async def async_set_voicemail_enabled(self, enabled: bool) -> dict[str, Any]:
        return await self._async_request(
            "POST", API_PATH_VOICEMAIL_ENABLE if enabled else API_PATH_VOICEMAIL_DISABLE, auth=True
        )

    async def async_install_update(self) -> dict[str, Any]:
        return await self._async_request("POST", API_PATH_UPDATE_INSTALL, auth=True)

    async def async_reboot(self) -> dict[str, Any]:
        return await self._async_request("POST", API_PATH_SYSTEM_REBOOT, auth=True)

    async def async_restart_service(self, service: str) -> dict[str, Any]:
        return await self._async_request(
            "POST", API_PATH_SYSTEM_SERVICE_RESTART.format(service=quote(service, safe="")), auth=True
        )

    async def async_webrtc_offer(
        self,
        *,
        entrypoint_id: str,
        offer_sdp: str,
        session_id: str,
        origin: str,
        ice_servers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Open a session-scoped WebRTC signaling socket and submit its offer."""
        if not self._access_token:
            raise CompanionAuthError("an access token is required")
        if session_id in self._webrtc_sessions:
            raise CompanionApiError("WebRTC session already exists")
        try:
            websocket = await self._session.ws_connect(
                f"{self._base_url}{API_PATH_WEBRTC_WS}",
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            session = _WebRTCSession(websocket)
            self._webrtc_sessions[session_id] = session
            response = await self._async_webrtc_message(
                session,
                "offer",
                session_id,
                {
                    "session_id": session_id,
                    "entrypoint_id": entrypoint_id,
                    "origin": origin,
                    "offer_sdp": offer_sdp,
                    "ice_servers": ice_servers or [],
                },
            )
            return response
        except ClientError as err:
            await self._async_webrtc_discard(session_id)
            raise CompanionApiError("unable to contact Companion") from err
        except Exception:
            await self._async_webrtc_discard(session_id)
            raise

    async def async_webrtc_candidate(
        self, *, session_id: str, candidate: dict[str, Any]
    ) -> dict[str, Any]:
        """Forward a Home Assistant WebRTC ICE candidate over its session socket."""
        session = self._webrtc_sessions.get(session_id)
        if session is None:
            return {"session_id": session_id, "ignored": True}
        return await self._async_webrtc_message(
            session, "candidate", session_id, {"session_id": session_id, "candidate": candidate}
        )

    async def async_webrtc_close(self, *, session_id: str) -> dict[str, Any]:
        """Close a Companion WebRTC session and its signaling socket."""
        session = self._webrtc_sessions.get(session_id)
        if session is None:
            return {"session_id": session_id}
        try:
            return await self._async_webrtc_message(
                session, "close", session_id, {"session_id": session_id, "reason": "ha_session_closed"}
            )
        except ClientError as err:
            raise CompanionApiError("unable to close Companion WebRTC session") from err
        finally:
            await self._async_webrtc_discard(session_id)

    async def _async_webrtc_message(
        self, session: _WebRTCSession, message_type: str, session_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with session.lock:
            if session.closing and message_type != "close":
                return {"session_id": session_id, "ignored": True}
            if message_type == "close":
                session.closing = True
            await session.websocket.send_json({"type": message_type, "id": session_id, "payload": payload})
            message = await session.websocket.receive()
        if message.type is not WSMsgType.TEXT:
            raise CompanionApiError("Companion closed the WebRTC signaling socket")
        try:
            response = message.json()
        except ValueError as err:
            raise CompanionApiError("Companion returned an invalid WebRTC response") from err
        if not isinstance(response, dict) or response.get("id") != session_id:
            raise CompanionApiError("Companion returned an invalid WebRTC response")
        if response.get("type") == "error":
            if not isinstance(response.get("payload"), dict):
                raise CompanionApiError("Companion returned an invalid WebRTC response")
            error = response["payload"]
            raise CompanionApiError(str(error.get("message", "WebRTC signaling failed")), str(error.get("code", "webrtc_failed")))
        if response.get("type") == "answer":
            if not isinstance(response.get("payload"), dict):
                raise CompanionApiError("Companion returned an invalid WebRTC response")
            return response["payload"]
        if response.get("type") == "ack":
            return {"session_id": session_id}
        raise CompanionApiError("Companion returned an invalid WebRTC response")

    async def _async_webrtc_discard(self, session_id: str) -> None:
        session = self._webrtc_sessions.pop(session_id, None)
        if session is not None:
            await session.websocket.close()

    async def async_entrypoint_snapshot_latest(self, entrypoint_id: str) -> bytes | None:
        """Return the last passive snapshot without requesting a new capture."""
        if not self._access_token:
            raise CompanionAuthError("an access token is required")
        try:
            async with self._session.get(
                f"{self._base_url}{API_PATH_SNAPSHOT_LATEST.format(entrypoint_id=quote(entrypoint_id, safe=''))}",
                headers={"Accept": "image/jpeg", "Authorization": f"Bearer {self._access_token}"},
            ) as response:
                if response.status == 404:
                    return None
                if response.status >= 400:
                    self._raise_for_status(response, await self._async_json(response))
                image = await response.read()
                return image or None
        except ClientError as err:
            raise CompanionApiError("unable to contact Companion") from err

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        auth: bool,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._async_open_request(method, path, auth=auth, json_body=json_body)
        async with response:
            payload = await self._async_json(response)
            self._raise_for_status(response, payload)
            if not isinstance(payload, dict):
                raise CompanionApiError("Companion returned a non-object response")
            return payload

    async def _async_open_request(
        self,
        method: str,
        path: str,
        *,
        auth: bool,
        json_body: dict[str, Any] | None = None,
    ) -> ClientResponse:
        if auth and not self._access_token:
            raise CompanionAuthError("an access token is required")
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token}"
        try:
            return await self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json_body,
            )
        except ClientError as err:
            raise CompanionApiError("unable to contact Companion") from err

    @staticmethod
    async def _async_json(response: ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except (ClientError, ValueError):
            return {}

    @staticmethod
    def _raise_for_status(response: ClientResponse, payload: Any) -> None:
        if response.status < 400:
            return
        message = f"Companion request failed ({response.status})"
        code = "cannot_connect"
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            message = str(payload["error"].get("message", message))
            code = str(payload["error"].get("code", code))
        if response.status in (401, 403):
            raise CompanionAuthError(message, code)
        raise CompanionApiError(message, code)
