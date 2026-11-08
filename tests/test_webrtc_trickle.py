"""Trickled local candidates: the client's socket reader and the card's subscription."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from aiohttp import WSMsgType
    from bticino_companion.api import CompanionApiClient, CompanionApiError
    from bticino_companion.const import DATA_CAMERA_ENTITIES
    from bticino_companion.websocket_api import _handle_candidates
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err


def _text(data: dict) -> MagicMock:
    return MagicMock(type=WSMsgType.TEXT, json=lambda: json.loads(json.dumps(data)))


def _candidate(value: str, session_id: str = "session") -> MagicMock:
    return _text({"type": "candidate", "id": session_id, "payload": {"session_id": session_id, "candidate": {"candidate": value}}})


class _ScriptedSocket:
    """Answers each request with what the test queued; receive blocks like a real socket."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[MagicMock] = asyncio.Queue()
        self.sent: list[dict] = []
        self.responses: dict[str, list[MagicMock]] = {}
        self.closed = False

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)
        for response in self.responses.get(message["type"], []):
            self.incoming.put_nowait(response)

    async def receive(self) -> MagicMock:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True


def _client(socket: _ScriptedSocket) -> CompanionApiClient:
    session = MagicMock()
    session.ws_connect = AsyncMock(return_value=socket)
    return CompanionApiClient(session, "http://companion", "token")


_ANSWER = {"type": "answer", "id": "session", "payload": {"answer_sdp": "answer"}}
_ACK = {"type": "ack", "id": "session"}


class TrickleClientTest(unittest.IsolatedAsyncioTestCase):
    async def _offer(self, client: CompanionApiClient, received: list[dict]) -> dict:
        return await client.async_webrtc_offer(
            entrypoint_id="main", offer_sdp="offer", session_id="session", origin="bticino_card", on_candidate=received.append
        )

    async def test_candidates_around_the_responses_reach_the_listener(self) -> None:
        socket = _ScriptedSocket()
        socket.responses = {"offer": [_candidate("early"), _text(_ANSWER)], "candidate": [_candidate("between"), _text(_ACK)], "close": [_text(_ACK)]}
        client = _client(socket)
        received: list[dict] = []

        response = await self._offer(client, received)
        self.assertEqual(response["answer_sdp"], "answer")
        self.assertIs(socket.sent[0]["payload"]["trickle"], True)

        socket.incoming.put_nowait(_candidate("late"))
        await asyncio.sleep(0)
        await client.async_webrtc_candidate(session_id="session", candidate={"candidate": "browser"})
        await client.async_webrtc_close(session_id="session")

        self.assertEqual([item["candidate"] for item in received], ["early", "late", "between"])
        self.assertTrue(socket.closed)
        self.assertNotIn("session", client._webrtc_sessions)

    async def test_without_a_listener_the_offer_does_not_ask_for_trickle(self) -> None:
        socket = _ScriptedSocket()
        socket.responses = {"offer": [_text(_ANSWER)]}
        client = _client(socket)

        await client.async_webrtc_offer(entrypoint_id="main", offer_sdp="offer", session_id="session", origin="bticino_card")

        self.assertNotIn("trickle", socket.sent[0]["payload"])
        self.assertIsNone(client._webrtc_sessions["session"].reader)

    async def test_a_socket_closed_during_the_offer_is_an_error(self) -> None:
        socket = _ScriptedSocket()
        socket.responses = {"offer": [MagicMock(type=WSMsgType.CLOSED)]}
        client = _client(socket)

        with self.assertRaisesRegex(CompanionApiError, "closed the WebRTC signaling socket"):
            await self._offer(client, [])

        self.assertNotIn("session", client._webrtc_sessions)
        self.assertTrue(socket.closed)

    async def test_a_request_after_the_socket_closed_is_an_error(self) -> None:
        socket = _ScriptedSocket()
        socket.responses = {"offer": [_text(_ANSWER)]}
        client = _client(socket)

        await self._offer(client, [])
        socket.incoming.put_nowait(MagicMock(type=WSMsgType.CLOSED))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        with self.assertRaisesRegex(CompanionApiError, "closed the WebRTC signaling socket"):
            await client.async_webrtc_candidate(session_id="session", candidate={"candidate": "browser"})

        # Closing says so too, as without trickle, and still drops the session.
        with self.assertRaisesRegex(CompanionApiError, "closed the WebRTC signaling socket"):
            await client.async_webrtc_close(session_id="session")

        self.assertNotIn("session", client._webrtc_sessions)
        self.assertTrue(socket.closed)

    async def test_a_source_failure_around_the_answer_reaches_its_listener(self) -> None:
        def failure(message: str, session_id: str = "session") -> MagicMock:
            return _text({"type": "source_failed", "id": session_id, "payload": {"session_id": session_id, "code": "source_failed", "message": message}})

        socket = _ScriptedSocket()
        socket.responses = {"offer": [failure("early"), _text(_ANSWER)], "candidate": [_text(_ACK)]}
        client = _client(socket)
        failures: list[str] = []

        response = await client.async_webrtc_offer(
            entrypoint_id="main", offer_sdp="offer", session_id="session", origin="bticino_card", on_candidate=MagicMock(), on_source_failed=failures.append
        )
        self.assertEqual(response["answer_sdp"], "answer")

        socket.incoming.put_nowait(failure("late"))
        socket.incoming.put_nowait(failure("another session", "other"))
        await client.async_webrtc_candidate(session_id="session", candidate={"candidate": "browser"})

        self.assertEqual(failures, ["early", "late"])
        await client._async_webrtc_discard("session")

    async def test_a_source_failure_without_a_listener_does_not_disturb_a_request(self) -> None:
        socket = _ScriptedSocket()
        failure = _text({"type": "source_failed", "id": "session", "payload": {"session_id": "session", "message": "refused"}})
        socket.responses = {"offer": [failure, _text(_ANSWER)]}
        client = _client(socket)

        response = await self._offer(client, [])

        self.assertEqual(response["answer_sdp"], "answer")
        await client._async_webrtc_discard("session")

    async def test_a_failing_listener_does_not_stop_the_session(self) -> None:
        socket = _ScriptedSocket()
        socket.responses = {"offer": [_candidate("boom"), _text(_ANSWER)]}
        client = _client(socket)

        response = await client.async_webrtc_offer(
            entrypoint_id="main",
            offer_sdp="offer",
            session_id="session",
            origin="bticino_card",
            on_candidate=MagicMock(side_effect=RuntimeError("listener")),
        )

        self.assertEqual(response["answer_sdp"], "answer")
        await client._async_webrtc_discard("session")


class CandidateSubscriptionTest(unittest.TestCase):
    def _message(self) -> dict:
        return {"id": 7, "type": "bticino_companion/card_webrtc_candidates", "camera_entity_id": "camera.main", "session_id": " session "}

    def test_subscription_forwards_candidates_as_events(self) -> None:
        camera = MagicMock()
        unsubscribe = MagicMock()
        camera.async_subscribe_card_webrtc_candidates.return_value = unsubscribe
        hass = MagicMock(data={DATA_CAMERA_ENTITIES: {"camera.main": camera}})
        connection = MagicMock(subscriptions={})

        _handle_candidates(hass, connection, self._message())

        session_id, forward = camera.async_subscribe_card_webrtc_candidates.call_args.args
        self.assertEqual(session_id, "session")
        self.assertIs(connection.subscriptions[7], unsubscribe)
        connection.send_result.assert_called_once_with(7)

        forward({"candidate": "one"})
        event = connection.send_message.call_args.args[0]
        self.assertEqual(event["id"], 7)
        self.assertEqual(event["event"], {"candidate": {"candidate": "one"}})

    def test_subscription_forwards_a_source_failure_as_an_event(self) -> None:
        camera = MagicMock()
        hass = MagicMock(data={DATA_CAMERA_ENTITIES: {"camera.main": camera}})
        connection = MagicMock(subscriptions={})

        _handle_candidates(hass, connection, self._message())

        camera.async_subscribe_card_webrtc_candidates.call_args.kwargs["on_source_failed"]("refused")
        event = connection.send_message.call_args.args[0]
        self.assertEqual(event["id"], 7)
        self.assertEqual(event["event"], {"source_failed": "refused"})

    def test_unknown_camera_is_an_error(self) -> None:
        connection = MagicMock(subscriptions={})

        _handle_candidates(MagicMock(data={}), connection, self._message())

        self.assertEqual(connection.send_error.call_args.args[:2], (7, "webrtc_candidates_failed"))
        self.assertEqual(connection.subscriptions, {})


if __name__ == "__main__":
    unittest.main()
