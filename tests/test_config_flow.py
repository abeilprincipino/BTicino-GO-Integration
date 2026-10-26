"""Tests for Companion pairing-state configuration flow routing."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion.config_flow import (
        CompanionConfigFlow,
        CompanionOptionsFlow,
        _normalize_url,
        _zeroconf_url,
        options_schema,
    )
    from bticino_companion.const import (
        CONF_ACCESS_TOKEN,
        CONF_CLIP_RETENTION_DAYS,
        CONF_COMPANION_URL,
        CONF_DEVICE_ID,
        CONF_INSTANCE_ID,
        CONF_REPAIR_CODE,
    )
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err


class ZeroconfUrlTest(unittest.TestCase):
    def test_manual_url_adds_the_fixed_api_port(self) -> None:
        self.assertEqual(_normalize_url("10.0.0.143"), "http://10.0.0.143:8080")
        self.assertEqual(_normalize_url("companion.local"), "http://companion.local:8080")
        self.assertEqual(_normalize_url("[2001:db8::1]"), "http://[2001:db8::1]:8080")

    def test_manual_url_preserves_an_explicit_port_and_scheme(self) -> None:
        self.assertEqual(_normalize_url("http://companion.local:8081"), "http://companion.local:8081")
        self.assertEqual(_normalize_url("https://companion.local"), "https://companion.local:8080")

    def test_manual_url_preserves_reverse_proxy_path(self) -> None:
        self.assertEqual(
            _normalize_url("https://companion.local/bticino/"),
            "https://companion.local:8080/bticino",
        )

    def test_manual_url_rejects_unsafe_or_invalid_components(self) -> None:
        for value in (
            "ftp://companion.local",
            "http://user:pass@companion.local",
            "http://companion.local/../path",
            "http://companion.local?query=value",
            "http://companion.local#fragment",
            "http://companion.local:not-a-port",
        ):
            with self.subTest(value=value):
                self.assertEqual(_normalize_url(value), "")

    def test_uses_normalized_discovered_hostname_and_port(self) -> None:
        self.assertEqual(_zeroconf_url(" Companion.Local. ", 8181), "http://companion.local:8181")

    def test_uses_bracketed_ipv6_address(self) -> None:
        self.assertEqual(_zeroconf_url("2001:0db8::1", 8080), "http://[2001:db8::1]:8080")

    def test_rejects_missing_or_invalid_address_data(self) -> None:
        self.assertIsNone(_zeroconf_url(None, 8080))
        self.assertIsNone(_zeroconf_url("companion.local", 0))
        self.assertIsNone(_zeroconf_url("http://companion.local", 8080))


class ZeroconfConfigFlowTest(unittest.IsolatedAsyncioTestCase):
    def _flow(self, expected_result: dict[str, str]) -> SimpleNamespace:
        flow = SimpleNamespace()
        flow.context = {}
        flow.hass = SimpleNamespace(data={})
        flow.async_set_unique_id = AsyncMock()
        flow._async_current_entries = MagicMock(return_value=[])
        flow._async_step_for_pairing_state = AsyncMock(return_value=expected_result)
        flow._set_pairing_status = lambda status, url: CompanionConfigFlow._set_pairing_status(
            flow, status, url
        )
        flow._async_handle_existing_discovery = lambda entry: CompanionConfigFlow._async_handle_existing_discovery(
            flow, entry
        )
        return flow

    async def test_claimable_discovery_starts_initial_claim(self) -> None:
        expected = {"type": "form", "step_id": "claim"}
        flow = self._flow(expected)
        discovery_info = SimpleNamespace(
            properties={
                "device_id": "device-id",
                "model": "C300X",
                "pairing_state": "claimable",
                "instance_id": "a" * 32,
            },
            host="192.0.2.42",
            port=8181,
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, expected)
        flow.async_set_unique_id.assert_awaited_once_with("device-id")
        flow._async_current_entries.assert_called_once_with()
        self.assertEqual(flow.context["title_placeholders"], {"name": "C300X"})
        self.assertEqual(flow._pairing_state, "claimable")
        self.assertEqual(flow._instance_id, "a" * 32)

    async def test_claimed_discovery_starts_owner_recovery(self) -> None:
        expected = {"type": "form", "step_id": "recover"}
        flow = self._flow(expected)
        discovery_info = SimpleNamespace(
            properties={
                "device_id": "device-id",
                "pairing_state": "claimed",
                "instance_id": "b" * 32,
            },
            host="192.0.2.42",
            port=8181,
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, expected)
        self.assertEqual(flow.context["title_placeholders"], {"name": "device-id"})
        self.assertEqual(flow._pairing_state, "claimed")

    async def test_setup_required_discovery_routes_to_setup_message(self) -> None:
        flow = self._flow({"type": "abort", "reason": "setup_required"})
        discovery_info = SimpleNamespace(
            properties={
                "device_id": "device-id",
                "pairing_state": "setup_required",
                "instance_id": "c" * 32,
            },
            host="192.0.2.42",
            port=8181,
        )

        await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(flow._pairing_state, "setup_required")

    async def test_discovery_without_instance_id_aborts(self) -> None:
        flow = self._flow({"type": "form"})
        flow.async_abort = MagicMock(return_value={"type": "abort", "reason": "cannot_connect"})
        discovery_info = SimpleNamespace(
            properties={"device_id": "device-id", "pairing_state": "claimable"},
            host="192.0.2.42",
            port=8181,
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, {"type": "abort", "reason": "cannot_connect"})

    async def test_changed_instance_starts_native_reauth_without_updating_entry(self) -> None:
        flow = self._flow({"type": "form"})
        flow.async_abort = MagicMock(return_value={"type": "abort", "reason": "reauth_started"})
        flow.hass.config_entries = MagicMock()
        entry = SimpleNamespace(
            entry_id="entry-id",
            data={CONF_INSTANCE_ID: "old-instance", CONF_COMPANION_URL: "http://old"},
            async_start_reauth=MagicMock(),
        )
        flow._async_current_entries.return_value = [entry]
        discovery_info = SimpleNamespace(
            properties={
                "device_id": "device-id",
                "pairing_state": "claimable",
                "instance_id": "new-instance",
            },
            host="192.0.2.42",
            port=8181,
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, {"type": "abort", "reason": "reauth_started"})
        entry.async_start_reauth.assert_called_once_with(flow.hass)
        self.assertEqual(
            flow.hass.data["bticino_companion"]["bticino_companion_pending_reauth_urls"],
            {"entry-id": "http://192.0.2.42:8181"},
        )
        flow.hass.config_entries.async_update_entry.assert_not_called()

    async def test_same_claimed_instance_updates_only_the_discovered_url(self) -> None:
        flow = self._flow({"type": "form"})
        flow.async_abort = MagicMock(return_value={"type": "abort", "reason": "already_configured"})
        flow.hass.config_entries = MagicMock()
        runtime = SimpleNamespace(async_update_base_url=AsyncMock())
        flow.hass.data["bticino_companion"] = {"entry-id": runtime}
        entry = SimpleNamespace(
            entry_id="entry-id",
            data={CONF_INSTANCE_ID: "same-instance", CONF_COMPANION_URL: "http://old"},
            async_start_reauth=MagicMock(),
        )
        flow._async_current_entries.return_value = [entry]
        discovery_info = SimpleNamespace(
            properties={
                "device_id": "device-id",
                "pairing_state": "claimed",
                "instance_id": "same-instance",
            },
            host="192.0.2.42",
            port=8181,
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, {"type": "abort", "reason": "already_configured"})
        flow.hass.config_entries.async_update_entry.assert_called_once_with(
            entry,
            data={CONF_INSTANCE_ID: "same-instance", CONF_COMPANION_URL: "http://192.0.2.42:8181"},
        )
        runtime.async_update_base_url.assert_awaited_once_with("http://192.0.2.42:8181")
        entry.async_start_reauth.assert_not_called()

    async def test_recovery_persists_the_authorized_instance_id(self) -> None:
        flow = SimpleNamespace(
            hass=MagicMock(),
            _device_id="device-id",
            _url="http://192.0.2.42:8080",
            _instance_id="d" * 32,
        )
        client = MagicMock(access_token="replacement-bearer")
        client.async_recover_bearer = AsyncMock()

        with (
            patch("bticino_companion.config_flow.async_get_clientsession", return_value=MagicMock()),
            patch("bticino_companion.config_flow.CompanionApiClient", return_value=client),
        ):
            result = await CompanionConfigFlow._async_authorize(
                flow, "abcd-1234", recovery=True
            )

        client.async_recover_bearer.assert_awaited_once_with("abcd-1234")
        self.assertEqual(
            result,
            {
                CONF_DEVICE_ID: "device-id",
                CONF_COMPANION_URL: "http://192.0.2.42:8080",
                CONF_ACCESS_TOKEN: "replacement-bearer",
                CONF_INSTANCE_ID: "d" * 32,
            },
        )

    async def test_reauth_claim_updates_and_reloads_the_existing_entry(self) -> None:
        entry = object()
        expected = {"type": "abort", "reason": "reauth_successful"}
        flow = SimpleNamespace(
            _is_reauth=True,
            _device_id="device-id",
            async_set_unique_id=AsyncMock(),
            _abort_if_unique_id_mismatch=MagicMock(),
            _get_reauth_entry=MagicMock(return_value=entry),
            async_update_reload_and_abort=MagicMock(return_value=expected),
        )
        data = {CONF_ACCESS_TOKEN: "new-bearer", CONF_INSTANCE_ID: "e" * 32}

        result = await CompanionConfigFlow._async_finish_authorization(flow, data)

        self.assertEqual(result, expected)
        flow.async_set_unique_id.assert_awaited_once_with("device-id")
        flow._abort_if_unique_id_mismatch.assert_called_once_with()
        flow.async_update_reload_and_abort.assert_called_once_with(entry, data_updates=data)

    async def test_reauth_uses_current_claimable_state_for_a_reset_companion(self) -> None:
        expected = {"type": "form", "step_id": "claim"}
        flow = SimpleNamespace(
            hass=SimpleNamespace(data={}),
            _is_reauth=False,
            _device_id="",
            _pairing_state="error",
            _get_reauth_entry=MagicMock(return_value=SimpleNamespace(entry_id="entry-id")),
            _async_step_for_pairing_state=AsyncMock(return_value=expected),
            async_set_unique_id=AsyncMock(),
        )

        async def load_status(url: str) -> None:
            self.assertEqual(url, "http://192.0.2.42:8080")
            flow._device_id = "device-id"
            flow._pairing_state = "claimable"

        flow._async_load_pairing_status = AsyncMock(side_effect=load_status)

        result = await CompanionConfigFlow.async_step_reauth(
            flow,
            {CONF_DEVICE_ID: "device-id", CONF_COMPANION_URL: "http://192.0.2.42:8080"},
        )

        self.assertTrue(flow._is_reauth)
        flow.async_set_unique_id.assert_awaited_once_with("device-id")
        flow._async_step_for_pairing_state.assert_awaited_once_with()
        self.assertEqual(result, expected)

    async def test_pairing_state_routes_to_recovery(self) -> None:
        flow = SimpleNamespace(
            _pairing_state="claimed",
            async_step_claim=AsyncMock(),
            async_step_recover=AsyncMock(return_value={"type": "form", "step_id": "recover"}),
            async_abort=MagicMock(),
        )

        result = await CompanionConfigFlow._async_step_for_pairing_state(flow)

        self.assertEqual(result, {"type": "form", "step_id": "recover"})
        flow.async_step_claim.assert_not_awaited()
        flow.async_step_recover.assert_awaited_once_with()

    async def test_invalid_discovered_address_aborts(self) -> None:
        flow = self._flow({"type": "form"})
        flow.async_abort = MagicMock(return_value={"type": "abort", "reason": "cannot_connect"})
        discovery_info = SimpleNamespace(
            properties={"device_id": "device-id"}, host="", port=8080
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, {"type": "abort", "reason": "cannot_connect"})
        flow.async_set_unique_id.assert_not_called()


class OptionsFlowTest(unittest.IsolatedAsyncioTestCase):
    def test_schema_defaults_and_range(self) -> None:
        schema = options_schema(30)

        self.assertEqual(schema({}), {CONF_CLIP_RETENTION_DAYS: 30})
        self.assertEqual(schema({CONF_CLIP_RETENTION_DAYS: "0"}), {CONF_CLIP_RETENTION_DAYS: 0})
        for invalid in (-1, 3651):
            with self.subTest(invalid=invalid), self.assertRaises(Exception):
                schema({CONF_CLIP_RETENTION_DAYS: invalid})

    async def test_form_shows_the_current_retention(self) -> None:
        flow = CompanionOptionsFlow()
        flow.async_show_form = MagicMock(return_value="form")
        entry = SimpleNamespace(options={CONF_CLIP_RETENTION_DAYS: 7})

        with patch.object(CompanionOptionsFlow, "config_entry", new_callable=PropertyMock, return_value=entry):
            self.assertEqual(await flow.async_step_init(), "form")

        schema = flow.async_show_form.call_args.kwargs["data_schema"]
        self.assertEqual(schema({}), {CONF_CLIP_RETENTION_DAYS: 7})

    async def test_saving_stores_the_option(self) -> None:
        flow = CompanionOptionsFlow()
        flow.async_create_entry = MagicMock(return_value="created")

        self.assertEqual(await flow.async_step_init({CONF_CLIP_RETENTION_DAYS: 5}), "created")
        flow.async_create_entry.assert_called_once_with(data={CONF_CLIP_RETENTION_DAYS: 5})
