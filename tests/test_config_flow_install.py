"""Tests for installing the Companion from the config flow."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion import config_flow
    from bticino_companion.config_flow import CompanionConfigFlow
    from bticino_companion.const import (
        CONF_ACCESS_TOKEN,
        CONF_COMPANION_URL,
        CONF_OWNER_PASSWORD,
        CONF_OWNER_USERNAME,
        CONF_SSH_HOST_KEY,
        CONF_SSH_USERNAME,
        DOMAIN,
    )
    from bticino_companion.installer import (
        InstallerFailed,
        InstallerProgress,
        PairLocalError,
        ShellAuthError,
    )
    from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
except ImportError as err:
    if "homeassistant" not in str(err) and "asyncssh" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant or asyncssh is not installed") from err

DEVICE_ID = "c300x-001122334455"
INSTANCE_ID = "a" * 32
HOST_KEY = "ssh-ed25519 AAAAkey"
INSTALL_INPUT = {CONF_HOST: "192.0.2.10", CONF_USERNAME: "root", CONF_PASSWORD: "ssh-secret"}


def make_shell(pair_local: dict | None = None) -> MagicMock:
    shell = MagicMock()
    shell.host_key = HOST_KEY
    shell.async_connect = AsyncMock(return_value=HOST_KEY)
    shell.async_start_installer = AsyncMock()
    shell.async_wait_for_installer = AsyncMock(
        return_value=InstallerProgress(("INFO: Installed version: v3.1.0",), 0)
    )
    shell.async_pair_local = AsyncMock(
        return_value=pair_local
        or {
            "ok": True,
            "device_id": DEVICE_ID,
            "instance_id": INSTANCE_ID,
            "pairing_state": "claimable",
            "claim_code": "abcd-1234",
        }
    )
    shell.async_close = AsyncMock()
    return shell


def make_client(pairing_state: str = "claimable") -> MagicMock:
    client = MagicMock()
    client.access_token = "new-token"
    client.async_get_pairing_status = AsyncMock(
        return_value={
            "device_id": DEVICE_ID,
            "instance_id": INSTANCE_ID,
            "model": "C300X",
            "pairing_state": pairing_state,
        }
    )
    client.async_pair_challenge = AsyncMock(return_value={"challenge_id": "challenge"})
    client.async_pair_claim = AsyncMock(return_value={"access_token": "new-token"})
    client.async_recover_bearer = AsyncMock(return_value={"access_token": "new-token"})
    client.async_reboot = AsyncMock(return_value={})
    return client


def make_flow() -> CompanionConfigFlow:
    loop = asyncio.get_running_loop()
    flow = CompanionConfigFlow()
    flow.hass = MagicMock()
    flow.hass.data = {}
    flow.hass.async_create_task = lambda coro, *args, **kwargs: loop.create_task(coro)
    flow.context = {"source": "user"}
    flow.flow_id = "flow-id"
    flow.handler = DOMAIN
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow._abort_if_unique_id_mismatch = MagicMock()
    flow.async_create_entry = MagicMock(side_effect=lambda **kwargs: {"type": "create_entry", **kwargs})
    return flow


class FlowTestCase(unittest.IsolatedAsyncioTestCase):
    """Helpers shared by the install and reconfigure tests; holds no tests itself."""

    def patched(self, shell: MagicMock, client: MagicMock):
        return patch.multiple(
            config_flow,
            IntercomShell=MagicMock(return_value=shell),
            CompanionApiClient=MagicMock(return_value=client),
            async_get_clientsession=MagicMock(),
        )

    async def run_install(self, flow: CompanionConfigFlow) -> dict:
        result = await flow.async_step_install(dict(INSTALL_INPUT))
        self.assertEqual(result["type"], "progress")
        # wait() does not re-raise a failed install; async_step_install_run reads the outcome.
        await asyncio.wait([flow._install_task])
        return await flow.async_step_install_run()


class InstallFlowTest(FlowTestCase):
    async def test_user_step_offers_connect_and_install(self) -> None:
        result = await make_flow().async_step_user()

        self.assertEqual(result["type"], "menu")
        self.assertEqual(result["menu_options"], ["connect", "install"])

    async def test_wrong_ssh_password_stays_on_the_form(self) -> None:
        shell = make_shell()
        shell.async_connect.side_effect = ShellAuthError("denied")
        with self.patched(shell, make_client()):
            result = await make_flow().async_step_install(dict(INSTALL_INPUT))

        self.assertEqual((result["type"], result["step_id"]), ("form", "install"))
        self.assertEqual(result["errors"], {"base": "invalid_auth"})

    async def test_install_with_owner_setup_pairs_and_creates_the_entry(self) -> None:
        shell, client, flow = make_shell(), make_client("setup_required"), make_flow()
        with self.patched(shell, client):
            done = await self.run_install(flow)
            # async_show_progress_done puts the next step in step_id.
            self.assertEqual((done["type"], done["step_id"]), ("progress_done", "pair"))

            owner_form = await flow.async_step_pair()
            self.assertEqual((owner_form["type"], owner_form["step_id"]), ("form", "owner"))

            menu = await flow.async_step_owner(
                {CONF_OWNER_USERNAME: "owner", CONF_OWNER_PASSWORD: "correct-horse"}
            )
            self.assertEqual((menu["type"], menu["step_id"]), ("menu", "reboot"))
            shell.async_pair_local.assert_awaited_once_with(("owner", "correct-horse"))
            client.async_pair_claim.assert_awaited_once_with(challenge_id="challenge", claim_code="abcd-1234")

            entry = await flow.async_step_reboot_now()

        client.async_reboot.assert_awaited_once_with()
        self.assertEqual(entry["type"], "create_entry")
        self.assertEqual(entry["data"][CONF_ACCESS_TOKEN], "new-token")
        self.assertEqual(entry["data"][CONF_COMPANION_URL], "http://192.0.2.10:8080")
        self.assertEqual(entry["data"][CONF_SSH_USERNAME], "root")
        self.assertEqual(entry["data"][CONF_SSH_HOST_KEY], HOST_KEY)
        self.assertNotIn("ssh-secret", repr(entry))
        self.assertNotIn("correct-horse", repr(entry))
        shell.async_close.assert_awaited()

    async def test_owner_rules_are_checked_before_sending(self) -> None:
        shell, flow = make_shell(), make_flow()
        with self.patched(shell, make_client("setup_required")):
            await self.run_install(flow)
            await flow.async_step_pair()
            result = await flow.async_step_owner({CONF_OWNER_USERNAME: "companion", CONF_OWNER_PASSWORD: "correct-horse"})

        self.assertEqual(result["errors"], {"base": "invalid_owner"})
        shell.async_pair_local.assert_not_awaited()

    async def test_owner_refused_by_the_companion_returns_to_the_form(self) -> None:
        shell, flow = make_shell(), make_flow()
        shell.async_pair_local.side_effect = PairLocalError(400, "invalid_owner")
        with self.patched(shell, make_client("setup_required")):
            await self.run_install(flow)
            await flow.async_step_pair()
            result = await flow.async_step_owner({CONF_OWNER_USERNAME: "owner", CONF_OWNER_PASSWORD: "correct-horse"})

        self.assertEqual((result["step_id"], result["errors"]), ("owner", {"base": "invalid_owner"}))

    async def test_claimable_companion_skips_the_owner_form(self) -> None:
        shell, flow = make_shell(), make_flow()
        with self.patched(shell, make_client("claimable")):
            await self.run_install(flow)
            result = await flow.async_step_pair()

        self.assertEqual((result["type"], result["step_id"]), ("menu", "reboot"))
        shell.async_pair_local.assert_awaited_once_with(None)

    async def test_claimed_companion_recovers_with_the_repair_code(self) -> None:
        shell = make_shell(
            {"ok": True, "device_id": DEVICE_ID, "instance_id": INSTANCE_ID, "pairing_state": "claimed", "repair_code": "r-1"}
        )
        client, flow = make_client("claimed"), make_flow()
        with self.patched(shell, client):
            await self.run_install(flow)
            await flow.async_step_pair()

        client.async_recover_bearer.assert_awaited_once_with("r-1")

    async def test_different_device_over_ssh_aborts(self) -> None:
        shell = make_shell(
            {"ok": True, "device_id": "other", "instance_id": INSTANCE_ID, "pairing_state": "claimable", "claim_code": "c"}
        )
        flow = make_flow()
        with self.patched(shell, make_client()):
            await self.run_install(flow)
            result = await flow.async_step_pair()

        self.assertEqual((result["type"], result["reason"]), ("abort", "wrong_device"))

    async def test_failed_install_shows_the_fail_lines_and_retries(self) -> None:
        shell, flow = make_shell(), make_flow()
        shell.async_wait_for_installer.side_effect = [
            InstallerFailed(("FAIL: SHA256 mismatch for companion.tar.gz.",)),
            InstallerProgress(("INFO: Installed version: v3.1.0",), 0),
        ]
        with self.patched(shell, make_client()):
            done = await self.run_install(flow)
            self.assertEqual(done["step_id"], "install_failed")

            form = await flow.async_step_install_failed()
            self.assertEqual(form["step_id"], "install_failed")
            self.assertEqual(form["description_placeholders"]["failure"], "FAIL: SHA256 mismatch for companion.tar.gz.")

            retry = await flow.async_step_install_failed({})
            self.assertEqual(retry["type"], "progress")
            await flow._install_task
            done = await flow.async_step_install_run()

        self.assertEqual(done["step_id"], "pair")
        self.assertEqual(shell.async_start_installer.await_count, 2)

    async def test_reboot_later_creates_the_entry_without_rebooting(self) -> None:
        client, flow = make_client(), make_flow()
        with self.patched(make_shell(), client):
            await self.run_install(flow)
            await flow.async_step_pair()
            entry = await flow.async_step_reboot_later()

        client.async_reboot.assert_not_awaited()
        self.assertEqual(entry["type"], "create_entry")
