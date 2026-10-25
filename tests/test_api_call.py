"""Companion call-control client methods."""

from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion.api import CompanionApiClient, CompanionConnectionError
    from bticino_companion.const import (
        API_PATH_CALL_ANSWER,
        API_PATH_CALL_HANGUP,
    )
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err


def _recording_client() -> tuple[CompanionApiClient, list[tuple[str, str, bool]]]:
    """Build a client without running __init__, so no aiohttp session is needed."""
    client = CompanionApiClient.__new__(CompanionApiClient)
    requests: list[tuple[str, str, bool]] = []

    async def _async_request(method, path, *, auth=False, json_body=None):
        requests.append((method, path, auth))
        return {"ok": True}

    client._async_request = _async_request  # type: ignore[method-assign]

    return client, requests


class CompanionCallTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_call_answer_posts_to_answer_path(self) -> None:
        client, requests = _recording_client()

        assert await client.async_call_answer() == {"ok": True}
        assert requests == [("POST", API_PATH_CALL_ANSWER, True)]

    async def test_async_call_hangup_posts_to_hangup_path(self) -> None:
        client, requests = _recording_client()

        assert await client.async_call_hangup() == {"ok": True}
        assert requests == [("POST", API_PATH_CALL_HANGUP, True)]


class CompanionTimeoutTest(unittest.IsolatedAsyncioTestCase):
    """A silent Companion raises TimeoutError, which is not an aiohttp ClientError."""

    def _client(self) -> CompanionApiClient:
        session = MagicMock()
        session.request = AsyncMock(side_effect=TimeoutError)
        session.get = MagicMock(side_effect=TimeoutError)
        session.ws_connect = AsyncMock(side_effect=TimeoutError)
        return CompanionApiClient(session, "http://companion.invalid:8080", "token")

    async def test_request_timeout_is_a_connection_error(self) -> None:
        with self.assertRaises(CompanionConnectionError):
            await self._client().async_reboot()

    async def test_snapshot_timeout_is_a_connection_error(self) -> None:
        with self.assertRaises(CompanionConnectionError):
            await self._client().async_entrypoint_snapshot_latest("gate")

    async def test_webrtc_offer_timeout_is_a_connection_error(self) -> None:
        with self.assertRaises(CompanionConnectionError):
            await self._client().async_webrtc_offer(
                session_id="s", entrypoint_id="gate", origin="o", offer_sdp="sdp"
            )

