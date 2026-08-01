"""Tests for typed Companion control entities."""

from __future__ import annotations

import sys
import asyncio
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientError, WSMsgType

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from homeassistant.const import EntityCategory
    from bticino_companion.button import CompanionEntrypointButton, CompanionRebootButton, CompanionServiceRestartButton
    from bticino_companion.camera import CompanionEntrypointCamera
    from bticino_companion.api import CompanionApiClient, CompanionApiError
    from bticino_companion.coordinator import CompanionCoordinator
    from bticino_companion.dynamic_entities import DynamicEntityManager
    from bticino_companion.models import CompanionState, Diagnostics, Entrypoint, SystemService, UpdateInfo
    from bticino_companion.switch import CompanionMuteSwitch, CompanionVoicemailSwitch
    from bticino_companion.update import CompanionUpdate
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err


class _MockEntry:
    def __init__(self) -> None:
        self.entry_id = "entry-123"
        self.unique_id = "device-123"
        self.runtime_data = MagicMock()
        self.runtime_data.coordinator = MagicMock(spec=CompanionCoordinator)
        self.runtime_data.client = MagicMock()
        self.runtime_data.client.async_unlock_entrypoint = AsyncMock()
        self.runtime_data.client.async_set_muted = AsyncMock()
        self.runtime_data.client.async_set_voicemail_enabled = AsyncMock()
        self.runtime_data.client.async_install_update = AsyncMock()
        self.runtime_data.client.async_reboot = AsyncMock()
        self.runtime_data.client.async_restart_service = AsyncMock()
        self.runtime_data.client.async_webrtc_offer = AsyncMock(return_value={"answer_sdp": "answer-sdp"})
        self.runtime_data.client.async_webrtc_candidate = AsyncMock()
        self.runtime_data.client.async_webrtc_close = AsyncMock()
        self.runtime_data.client.async_entrypoint_snapshot_latest = AsyncMock(return_value=None)

    def async_on_unload(self, listener):
        return lambda: None


class _SerializedWebSocket:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = responses
        self.active_receives = 0
        self.max_active_receives = 0
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)

    async def receive(self):
        self.active_receives += 1
        self.max_active_receives = max(self.max_active_receives, self.active_receives)
        if self.active_receives > 1:
            raise RuntimeError("Concurrent call to receive() is not allowed")
        await asyncio.sleep(0)
        self.active_receives -= 1
        return MagicMock(type=WSMsgType.TEXT, json=lambda: self._responses.pop(0))

    async def close(self) -> None:
        return None


def _coordinator(state: CompanionState | None = None, connected: bool = True) -> MagicMock:
    coordinator = MagicMock(spec=CompanionCoordinator)
    coordinator.data = state
    coordinator.runtime = MagicMock(connected=connected)
    return coordinator


def _state(**kwargs) -> CompanionState:
    defaults = {
        "revision": 1,
        "entrypoints": (),
        "muted": False,
        "voicemail_enabled": None,
        "update": UpdateInfo(),
        "diagnostics": Diagnostics(),
    }
    defaults.update(kwargs)
    return CompanionState(**defaults)


class TypedControlTest(unittest.IsolatedAsyncioTestCase):
    async def test_unlock_uses_typed_rest_client(self) -> None:
        entry = _MockEntry()
        coordinator = _coordinator()
        entrypoint = Entrypoint.from_dict(
            {"id": "main", "label": "Main Gate", "capabilities": {"unlock": True}, "availability": {"unlock": True}}
        )
        entity = CompanionEntrypointButton(
            entry,
            coordinator,
            entry.runtime_data.client,
            entrypoint.id,
            "Main Gate",
        )

        await entity.async_press()

        entry.runtime_data.client.async_unlock_entrypoint.assert_awaited_once_with("main")

    async def test_audio_and_voicemail_use_typed_rest_client(self) -> None:
        entry = _MockEntry()
        coordinator = _coordinator()

        mute = CompanionMuteSwitch(entry, coordinator, entry.runtime_data.client)
        voicemail = CompanionVoicemailSwitch(entry, coordinator, entry.runtime_data.client)
        await mute.async_turn_on()
        await mute.async_turn_off()
        await voicemail.async_turn_on()
        await voicemail.async_turn_off()

        entry.runtime_data.client.async_set_muted.assert_any_await(True)
        entry.runtime_data.client.async_set_muted.assert_any_await(False)
        entry.runtime_data.client.async_set_voicemail_enabled.assert_any_await(True)
        entry.runtime_data.client.async_set_voicemail_enabled.assert_any_await(False)

    async def test_update_uses_typed_rest_client(self) -> None:
        entry = _MockEntry()
        update = CompanionUpdate(entry, _coordinator(), entry.runtime_data.client)
        await update.async_install(version=None, backup=False)
        entry.runtime_data.client.async_install_update.assert_awaited_once()

    async def test_reboot_uses_typed_rest_client(self) -> None:
        entry = _MockEntry()
        reboot = CompanionRebootButton(entry, _coordinator(), entry.runtime_data.client)

        await reboot.async_press()

        entry.runtime_data.client.async_reboot.assert_awaited_once()

    async def test_reboot_client_uses_reboot_rest_endpoint(self) -> None:
        client = CompanionApiClient(MagicMock(), "http://companion", "token")
        client._async_request = AsyncMock()

        await client.async_reboot()

        client._async_request.assert_awaited_once_with("POST", "/api/v3/system/reboot", auth=True)

    async def test_service_restart_uses_typed_rest_client(self) -> None:
        entry = _MockEntry()
        restart = CompanionServiceRestartButton(entry, _coordinator(), entry.runtime_data.client, "dropbear")

        self.assertEqual(restart.entity_category, EntityCategory.CONFIG)
        await restart.async_press()

        entry.runtime_data.client.async_restart_service.assert_awaited_once_with("dropbear")

    async def test_service_restart_client_uses_service_endpoint(self) -> None:
        client = CompanionApiClient(MagicMock(), "http://companion", "token")
        client._async_request = AsyncMock()

        await client.async_restart_service("dropbear")

        client._async_request.assert_awaited_once_with(
            "POST", "/api/v3/system/services/dropbear/restart", auth=True
        )

    async def test_webrtc_client_starts_without_sessions(self) -> None:
        client = CompanionApiClient(MagicMock(), "http://companion", "token")
        self.assertEqual(client._webrtc_sessions, {})

    async def test_webrtc_candidates_are_serialized_per_session(self) -> None:
        websocket = _SerializedWebSocket(
            [
                {"type": "answer", "id": "session", "payload": {"answer_sdp": "answer"}},
                {"type": "ack", "id": "session"},
                {"type": "ack", "id": "session"},
            ]
        )
        session = MagicMock()
        session.ws_connect = AsyncMock(return_value=websocket)
        client = CompanionApiClient(session, "http://companion", "token")

        await client.async_webrtc_offer(
            entrypoint_id="main", offer_sdp="offer", session_id="session", origin="native_camera"
        )
        await asyncio.gather(
            client.async_webrtc_candidate(session_id="session", candidate={"candidate": "one"}),
            client.async_webrtc_candidate(session_id="session", candidate={"candidate": "two"}),
        )

        self.assertEqual(websocket.max_active_receives, 1)

    async def test_webrtc_close_normalizes_transport_errors(self) -> None:
        websocket = _SerializedWebSocket(
            [{"type": "answer", "id": "session", "payload": {"answer_sdp": "answer"}}]
        )
        session = MagicMock()
        session.ws_connect = AsyncMock(return_value=websocket)
        client = CompanionApiClient(session, "http://companion", "token")

        await client.async_webrtc_offer(
            entrypoint_id="main", offer_sdp="offer", session_id="session", origin="native_camera"
        )
        websocket.send_json = AsyncMock(side_effect=ClientError("connection closed"))

        with self.assertRaisesRegex(CompanionApiError, "unable to close Companion WebRTC session"):
            await client.async_webrtc_close(session_id="session")

        self.assertNotIn("session", client._webrtc_sessions)

    async def test_camera_forwards_companion_answer_to_frontend(self) -> None:
        entry = _MockEntry()
        stream = Entrypoint.from_dict(
            {"id": "main", "label": "Main", "capabilities": {"stream": True}, "availability": {"stream": True}}
        )
        camera = CompanionEntrypointCamera(entry, _coordinator(_state(entrypoints=(stream,))), entry.runtime_data.client, "main", "Main")
        send_message = MagicMock()

        await camera.async_handle_async_webrtc_offer("offer-sdp", "session-1", send_message)

        entry.runtime_data.client.async_webrtc_offer.assert_awaited_once_with(
            entrypoint_id="main", offer_sdp="offer-sdp", session_id="session-1", origin="native_camera", ice_servers=[]
        )
        self.assertEqual(send_message.call_args.args[0].answer, "answer-sdp")
        self.assertTrue(camera.available)
        self.assertTrue(camera.force_update)

    async def test_camera_card_bridge_owns_and_releases_its_session(self) -> None:
        entry = _MockEntry()
        stream = Entrypoint.from_dict(
            {"id": "main", "label": "Main", "capabilities": {"stream": True}, "availability": {"stream": True}}
        )
        camera = CompanionEntrypointCamera(
            entry, _coordinator(_state(entrypoints=(stream,))), entry.runtime_data.client, "main", "Main"
        )

        answer = await camera.async_handle_card_webrtc_offer("offer-sdp", "session-1")
        await camera.async_handle_card_webrtc_candidate("session-1", {"candidate": "candidate"})
        await camera.async_close_card_webrtc_session("session-1")

        self.assertEqual(answer, "answer-sdp")
        entry.runtime_data.client.async_webrtc_candidate.assert_awaited_once_with(
            session_id="session-1", candidate={"candidate": "candidate"}
        )
        entry.runtime_data.client.async_webrtc_close.assert_awaited_once_with(session_id="session-1")
        with self.assertRaisesRegex(ValueError, "Unknown WebRTC session"):
            await camera.async_handle_card_webrtc_candidate("session-1", {"candidate": "candidate"})

    async def test_camera_closes_session_when_companion_returns_invalid_answer(self) -> None:
        entry = _MockEntry()
        entry.runtime_data.client.async_webrtc_offer = AsyncMock(return_value={"answer_sdp": ""})
        stream = Entrypoint.from_dict(
            {"id": "main", "label": "Main", "capabilities": {"stream": True}, "availability": {"stream": True}}
        )
        camera = CompanionEntrypointCamera(
            entry, _coordinator(_state(entrypoints=(stream,))), entry.runtime_data.client, "main", "Main"
        )

        with self.assertRaisesRegex(ValueError, "empty answer_sdp"):
            await camera.async_handle_card_webrtc_offer("offer-sdp", "session-1")

        entry.runtime_data.client.async_webrtc_close.assert_awaited_once_with(session_id="session-1")
        with self.assertRaisesRegex(ValueError, "Unknown WebRTC session"):
            await camera.async_handle_card_webrtc_candidate("session-1", {"candidate": "candidate"})

    async def test_camera_reports_only_its_active_stream(self) -> None:
        entry = _MockEntry()
        stream = Entrypoint.from_dict(
            {"id": "main", "label": "Main", "capabilities": {"stream": True}, "availability": {"stream": True}}
        )
        camera = CompanionEntrypointCamera(
            entry,
            _coordinator(_state(call_state="preview", active_entrypoint_id="main", entrypoints=(stream,))),
            entry.runtime_data.client,
            "main",
            "Main",
        )

        self.assertTrue(camera.is_streaming)
        self.assertIsNone(await camera.async_camera_image())
        entry.runtime_data.client.async_entrypoint_snapshot_latest.assert_awaited_once_with("main")

        camera.coordinator.data = _state(call_state="idle", active_entrypoint_id="main", entrypoints=(stream,))
        self.assertFalse(camera.is_streaming)

    async def test_camera_exposes_only_its_own_call_state(self) -> None:
        entry = _MockEntry()
        main = Entrypoint.from_dict(
            {"id": "main", "label": "Main Gate", "capabilities": {"stream": True}}
        )
        side = Entrypoint.from_dict(
            {"id": "side", "label": "Side Gate", "capabilities": {"stream": True}}
        )
        camera = CompanionEntrypointCamera(
            entry,
            _coordinator(_state(call_state="ringing", active_entrypoint_id="side", entrypoints=(main, side))),
            entry.runtime_data.client,
            "main",
            "Main Gate",
        )

        self.assertEqual(
            camera.extra_state_attributes,
            {
                "bticino_entrypoint_id": "main",
                "bticino_entrypoint_label": "Main Gate",
                "bticino_call_state": "idle",
                "bticino_is_active_entrypoint": False,
                "bticino_is_ringing": False,
            },
        )

        camera.coordinator.data = _state(
            call_state="ringing", active_entrypoint_id="main", entrypoints=(main, side)
        )
        self.assertEqual(camera.extra_state_attributes["bticino_call_state"], "ringing")
        self.assertTrue(camera.extra_state_attributes["bticino_is_ringing"])

    async def test_camera_is_available_when_stream_is_configured_but_idle(self) -> None:
        entry = _MockEntry()
        stream = Entrypoint.from_dict(
            {"id": "main", "label": "Main", "capabilities": {"stream": True}, "availability": {"stream": False}}
        )
        camera = CompanionEntrypointCamera(entry, _coordinator(_state(entrypoints=(stream,))), entry.runtime_data.client, "main", "Main")

        self.assertTrue(camera.available)


class _MockPlatform:
    def __init__(self) -> None:
        self.entities: dict[str, object] = {}
        self.added: list[object] = []
        self.removed: list[str] = []

    async def async_add_entities(self, entities) -> None:
        for entity in entities:
            self.added.append(entity)
            self.entities[f"entity_{len(self.entities)}"] = entity

    async def async_remove_entity(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.entities.pop(entity_id)


class DynamicEntityManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconciles_exposed_service_restart_button(self) -> None:
        entry = _MockEntry()
        manager = DynamicEntityManager(MagicMock(), entry, _coordinator(), entry.runtime_data.client)

        desired = manager._desired_entities(
            _state(services=(SystemService(name="dropbear", enabled=True, exposed=True),))
        )

        self.assertEqual([entity.unique_id for entity in desired], ["device-123_restart_dropbear"])
        self.assertIsInstance(desired[0].create(), CompanionServiceRestartButton)

    async def test_reconciles_entrypoint_capability_changes_and_readd(self) -> None:
        entry = _MockEntry()
        unlocked = Entrypoint.from_dict(
            {"id": "main", "label": "Main", "capabilities": {"unlock": True}, "availability": {"unlock": True}}
        )
        entry.runtime_data.coordinator = _coordinator(_state(entrypoints=(unlocked,)))
        manager = DynamicEntityManager(
            MagicMock(),
            entry,
            entry.runtime_data.coordinator,
            entry.runtime_data.client,
        )
        platform = _MockPlatform()

        await manager.async_register_platform("button", platform)

        self.assertEqual([entity.unique_id for entity in platform.added], ["device-123_unlock_main"])

        disabled = Entrypoint.from_dict(
            {"id": "main", "label": "Main", "capabilities": {"unlock": False}}
        )
        entry.runtime_data.coordinator.data = _state(entrypoints=(disabled,))
        await manager.async_reconcile()
        self.assertEqual(len(platform.removed), 1)

        entry.runtime_data.coordinator.data = _state(
            entrypoints=(
                unlocked,
            )
        )
        await manager.async_reconcile()

        self.assertEqual(
            [entity.unique_id for entity in platform.added],
            ["device-123_unlock_main", "device-123_unlock_main"],
        )

        replacement = Entrypoint.from_dict(
            {"id": "side", "label": "Side", "capabilities": {"unlock": True}}
        )
        entry.runtime_data.coordinator.data = _state(entrypoints=(replacement,))
        await manager.async_reconcile()

        self.assertEqual(len(platform.removed), 2)
        self.assertEqual(platform.added[-1].unique_id, "device-123_unlock_side")

    async def test_reconciles_one_camera_per_stream_capable_entrypoint(self) -> None:
        entry = _MockEntry()
        stream = Entrypoint.from_dict({"id": "main", "capabilities": {"stream": True}})
        entry.runtime_data.coordinator = _coordinator(_state(entrypoints=(stream,)))
        manager = DynamicEntityManager(MagicMock(), entry, entry.runtime_data.coordinator, entry.runtime_data.client)
        platform = _MockPlatform()

        await manager.async_register_platform("camera", platform)

        self.assertEqual([entity.unique_id for entity in platform.added], ["device-123_camera_main"])
