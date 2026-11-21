"""The integration's Home Assistant actions."""

from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

    from bticino_companion.api import CompanionConnectionError
    from bticino_companion.const import DOMAIN
    from bticino_companion.services import (
        ATTR_CONFIG_ENTRY_ID,
        HANGUP_CALL_SCHEMA,
        SERVICE_HANGUP_CALL,
        async_register_services,
    )
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err


def _entry(entry_id: str, state=None, domain: str = DOMAIN) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.domain = domain
    entry.title = entry_id
    entry.state = ConfigEntryState.LOADED if state is None else state
    entry.runtime_data.client.async_call_hangup = AsyncMock(return_value={"ok": True})
    return entry


def _hass(*entries: MagicMock) -> tuple[MagicMock, dict]:
    """A hass whose service registry records the handler instead of running it."""
    registered: dict = {}
    hass = MagicMock()
    hass.services.has_service = MagicMock(side_effect=lambda domain, name: (domain, name) in registered)
    hass.services.async_register = MagicMock(
        side_effect=lambda domain, name, handler, schema=None: registered.__setitem__((domain, name), handler)
    )
    by_id = {entry.entry_id: entry for entry in entries}
    hass.config_entries.async_entries = MagicMock(
        side_effect=lambda domain: [entry for entry in entries if entry.domain == domain]
    )
    hass.config_entries.async_get_entry = MagicMock(side_effect=by_id.get)
    return hass, registered


def _call(**data) -> MagicMock:
    call = MagicMock()
    call.data = HANGUP_CALL_SCHEMA(data)
    return call


class HangupCallServiceTest(unittest.IsolatedAsyncioTestCase):
    def _handler(self, hass: MagicMock, registered: dict):
        async_register_services(hass)
        return registered[(DOMAIN, SERVICE_HANGUP_CALL)]

    async def test_registers_once(self) -> None:
        hass, registered = _hass()
        async_register_services(hass)
        async_register_services(hass)
        assert hass.services.async_register.call_count == 1
        assert (DOMAIN, SERVICE_HANGUP_CALL) in registered

    async def test_hangs_up_the_only_loaded_companion(self) -> None:
        entry = _entry("one")
        hass, registered = _hass(entry, _entry("gone", ConfigEntryState.NOT_LOADED))

        await self._handler(hass, registered)(_call())

        entry.runtime_data.client.async_call_hangup.assert_awaited_once()

    async def test_hangs_up_the_chosen_companion(self) -> None:
        first, second = _entry("first"), _entry("second")
        hass, registered = _hass(first, second)

        await self._handler(hass, registered)(_call(**{ATTR_CONFIG_ENTRY_ID: "second"}))

        second.runtime_data.client.async_call_hangup.assert_awaited_once()
        first.runtime_data.client.async_call_hangup.assert_not_awaited()

    async def test_several_companions_need_a_choice(self) -> None:
        hass, registered = _hass(_entry("first"), _entry("second"))
        with self.assertRaises(ServiceValidationError):
            await self._handler(hass, registered)(_call())

    async def test_no_loaded_companion_is_a_validation_error(self) -> None:
        hass, registered = _hass(_entry("gone", ConfigEntryState.SETUP_RETRY))
        with self.assertRaises(ServiceValidationError):
            await self._handler(hass, registered)(_call())

    async def test_rejects_an_entry_of_another_integration_or_not_loaded(self) -> None:
        hass, registered = _hass(_entry("other", domain="demo"), _entry("down", ConfigEntryState.NOT_LOADED))
        handler = self._handler(hass, registered)
        for entry_id in ("other", "down", "missing"):
            with self.subTest(entry_id=entry_id), self.assertRaises(ServiceValidationError):
                await handler(_call(**{ATTR_CONFIG_ENTRY_ID: entry_id}))

    async def test_companion_failure_is_a_home_assistant_error(self) -> None:
        entry = _entry("one")
        entry.runtime_data.client.async_call_hangup.side_effect = CompanionConnectionError("unreachable")
        hass, registered = _hass(entry)
        with self.assertRaises(HomeAssistantError) as raised:
            await self._handler(hass, registered)(_call())
        assert not isinstance(raised.exception, ServiceValidationError)


if __name__ == "__main__":
    unittest.main()
