"""Authenticated Home Assistant WebSocket bridge for the bundled card."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .api import CompanionApiError
from .const import DATA_CAMERA_ENTITIES


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register the card's signaling commands with Home Assistant authentication."""
    websocket_api.async_register_command(hass, _handle_offer)
    websocket_api.async_register_command(hass, _handle_candidate)
    websocket_api.async_register_command(hass, _handle_close)
    websocket_api.async_register_command(hass, _handle_unlock)
    websocket_api.async_register_command(hass, _handle_call_answer)
    websocket_api.async_register_command(hass, _handle_call_hangup)


def _camera_for_message(hass: HomeAssistant, msg: dict[str, Any]):
    camera_entity_id = str(msg["camera_entity_id"]).strip()
    camera = hass.data.get(DATA_CAMERA_ENTITIES, {}).get(camera_entity_id)
    if camera is None:
        raise HomeAssistantError(f"Camera '{camera_entity_id}' is not available")
    return camera


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "bticino_companion/card_webrtc_offer",
        vol.Required("camera_entity_id"): str,
        vol.Required("session_id"): str,
        vol.Required("offer_sdp"): str,
    }
)
@websocket_api.async_response
async def _handle_offer(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Delegate an offer to the configured native camera entity."""
    try:
        answer_sdp = await _camera_for_message(hass, msg).async_handle_card_webrtc_offer(
            str(msg["offer_sdp"]), str(msg["session_id"]).strip()
        )
    except (CompanionApiError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "webrtc_offer_failed", str(err))
        return
    connection.send_result(msg["id"], {"answer_sdp": answer_sdp})


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "bticino_companion/card_webrtc_candidate",
        vol.Required("camera_entity_id"): str,
        vol.Required("session_id"): str,
        vol.Required("candidate"): dict,
    }
)
@websocket_api.async_response
async def _handle_candidate(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Delegate an ICE candidate to its camera-owned session."""
    try:
        await _camera_for_message(hass, msg).async_handle_card_webrtc_candidate(
            str(msg["session_id"]).strip(), msg["candidate"]
        )
    except (CompanionApiError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "webrtc_candidate_failed", str(err))
        return
    connection.send_result(msg["id"])


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "bticino_companion/card_webrtc_close",
        vol.Required("camera_entity_id"): str,
        vol.Required("session_id"): str,
        vol.Optional("leave", default=False): bool,
    }
)
@websocket_api.async_response
async def _handle_close(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Close only the session owned by the configured camera entity."""
    try:
        await _camera_for_message(hass, msg).async_close_card_webrtc_session(
            str(msg["session_id"]).strip(), leave=msg["leave"]
        )
    except (CompanionApiError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "webrtc_close_failed", str(err))
        return
    connection.send_result(msg["id"])


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "bticino_companion/card_unlock",
        vol.Required("camera_entity_id"): str,
    }
)
@websocket_api.async_response
async def _handle_unlock(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Unlock the entrypoint represented by the configured camera."""
    try:
        await _camera_for_message(hass, msg).async_handle_card_unlock()
    except (CompanionApiError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "unlock_failed", str(err))
        return
    connection.send_result(msg["id"])


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "bticino_companion/card_call_answer",
        vol.Required("camera_entity_id"): str,
    }
)
@websocket_api.async_response
async def _handle_call_answer(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Answer the ringing call for the card's camera."""
    try:
        await _camera_for_message(hass, msg).async_handle_card_call_answer()
    except (CompanionApiError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "call_answer_failed", str(err))
        return
    connection.send_result(msg["id"])


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "bticino_companion/card_call_hangup",
        vol.Required("camera_entity_id"): str,
    }
)
@websocket_api.async_response
async def _handle_call_hangup(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """End the active call for the card's camera."""
    try:
        await _camera_for_message(hass, msg).async_handle_card_call_hangup()
    except (CompanionApiError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "call_hangup_failed", str(err))
        return
    connection.send_result(msg["id"])
