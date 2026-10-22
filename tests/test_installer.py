"""Tests for the SSH installer used by the install config flow."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import asyncssh  # noqa: F401
except ImportError as err:
    raise unittest.SkipTest("asyncssh is not installed") from err

_PATH = Path(__file__).parents[1] / "custom_components" / "bticino_companion" / "installer.py"
_SPEC = importlib.util.spec_from_file_location("bticino_companion_installer", _PATH)
installer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = installer
_SPEC.loader.exec_module(installer)

LOG = """INFO: Starting companion installation
INFO: Downloading latest release bundle...
INFO: Installed binary v3.1.0 to /home/bticino/cfg/extra/companion/companion
INFO: Installed gst runtime to /home/bticino/cfg/extra/companion/gst
"""


def _completed(stdout: str = "", exit_status: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_status=exit_status)


class ParseProgressTest(unittest.TestCase):
    def test_running_installer_reports_the_last_milestone(self) -> None:
        progress = installer.parse_progress(LOG + installer.RC_MARKER + "\n")

        self.assertFalse(progress.finished)
        self.assertEqual(progress.fraction, 0.5)
        self.assertEqual(progress.lines[0], "INFO: Starting companion installation")

    def test_no_log_yet_is_zero(self) -> None:
        progress = installer.parse_progress(installer.RC_MARKER + "\n")

        self.assertEqual(progress.fraction, 0.0)
        self.assertEqual(progress.lines, ())

    def test_finished_installer_reports_version_and_exit_code(self) -> None:
        log = LOG + "INFO: Installed version: v3.1.0\nINFO: Installation complete.\n"
        progress = installer.parse_progress(log + installer.RC_MARKER + "\n0\n")

        self.assertTrue(progress.finished)
        self.assertEqual(progress.exit_code, 0)
        self.assertEqual(progress.fraction, 1.0)
        self.assertEqual(progress.installed_version, "v3.1.0")

    def test_failure_lines_prefer_fail_lines(self) -> None:
        log = LOG + "FAIL: Health endpoint not reachable\nINFO: Installation finished with 1 failed check(s).\n"
        progress = installer.parse_progress(log + installer.RC_MARKER + "\n1\n")

        self.assertEqual(progress.failure_lines, ("FAIL: Health endpoint not reachable",))

    def test_failure_lines_fall_back_to_the_last_ten_lines(self) -> None:
        log = "".join(f"INFO: line {n}\n" for n in range(15))
        progress = installer.parse_progress(log + installer.RC_MARKER + "\n2\n")

        self.assertEqual(len(progress.failure_lines), 10)
        self.assertEqual(progress.failure_lines[-1], "INFO: line 14")


class IntercomShellTest(unittest.IsolatedAsyncioTestCase):
    def _shell(self, *runs: SimpleNamespace):
        connection = MagicMock()
        connection.run = AsyncMock(side_effect=list(runs))
        connection.wait_closed = AsyncMock()
        connector = AsyncMock(return_value=(connection, "ssh-ed25519 AAAAkey"))
        shell = installer.IntercomShell(
            "192.0.2.10", "root", "secret", connector=connector, sleep=AsyncMock(), clock=lambda: 0.0
        )
        return shell, connection, connector

    async def test_connect_returns_and_keeps_the_host_key(self) -> None:
        shell, _, connector = self._shell()

        self.assertEqual(await shell.async_connect(), "ssh-ed25519 AAAAkey")
        self.assertEqual(shell.host_key, "ssh-ed25519 AAAAkey")
        connector.assert_awaited_once_with("192.0.2.10", "root", "secret", None)

    async def test_reconnection_requires_the_key_seen_first(self) -> None:
        shell, _, connector = self._shell()
        await shell.async_connect()
        await shell.async_connect()

        self.assertEqual(connector.await_args_list[1].args[3], "ssh-ed25519 AAAAkey")

    async def test_start_installer_detaches_it(self) -> None:
        shell, connection, _ = self._shell(_completed())
        await shell.async_connect()

        await shell.async_start_installer()

        command = connection.run.await_args.args[0]
        self.assertIn("wget -qO /tmp/companion-install.sh", command)
        self.assertIn("setsid sh -c", command)
        self.assertIn("echo $? >/tmp/companion-install.rc", command)

    async def test_start_installer_reports_a_failed_download(self) -> None:
        shell, _, _ = self._shell(_completed(exit_status=1))
        await shell.async_connect()

        with self.assertRaises(installer.InstallerFailed) as caught:
            await shell.async_start_installer()

        self.assertIn("Could not download", caught.exception.lines[0])

    async def test_wait_reports_progress_and_returns_on_success(self) -> None:
        finished = LOG + "INFO: Installed version: v3.1.0\n" + installer.RC_MARKER + "\n0\n"
        shell, _, _ = self._shell(
            _completed(LOG + installer.RC_MARKER + "\n"), _completed(finished)
        )
        await shell.async_connect()
        seen: list[float] = []

        progress = await shell.async_wait_for_installer(seen.append)

        self.assertEqual(seen, [0.5, 1.0])
        self.assertEqual(progress.installed_version, "v3.1.0")

    async def test_wait_raises_the_fail_lines_on_a_failed_run(self) -> None:
        failed = "FAIL: SHA256 mismatch for companion.tar.gz.\n" + installer.RC_MARKER + "\n1\n"
        shell, _, _ = self._shell(_completed(failed))
        await shell.async_connect()

        with self.assertRaises(installer.InstallerFailed) as caught:
            await shell.async_wait_for_installer(lambda _: None)

        self.assertEqual(caught.exception.lines, ("FAIL: SHA256 mismatch for companion.tar.gz.",))

    async def test_wait_reconnects_after_a_dropped_connection(self) -> None:
        finished = LOG + installer.RC_MARKER + "\n0\n"
        shell, _, connector = self._shell(OSError("reset"), _completed(finished))
        await shell.async_connect()

        await shell.async_wait_for_installer(lambda _: None)

        self.assertEqual(connector.await_count, 2)

    async def test_wait_gives_up_after_repeated_disconnections(self) -> None:
        shell, _, _ = self._shell(*(OSError("reset") for _ in range(5)))
        await shell.async_connect()

        with self.assertRaises(installer.InstallerFailed) as caught:
            await shell.async_wait_for_installer(lambda _: None)

        self.assertIn("Lost the SSH connection", caught.exception.lines[0])

    async def test_wait_times_out(self) -> None:
        shell, _, _ = self._shell(_completed(LOG + installer.RC_MARKER + "\n"))
        shell._clock = iter([0.0, installer.INSTALL_TIMEOUT_SECONDS]).__next__
        await shell.async_connect()

        with self.assertRaises(installer.InstallerFailed) as caught:
            await shell.async_wait_for_installer(lambda _: None)

        self.assertIn("did not finish", caught.exception.lines[0])

    async def test_pair_local_sends_the_owner_on_stdin(self) -> None:
        body = {"ok": True, "device_id": "d", "instance_id": "i", "pairing_state": "claimable", "claim_code": "abcd-1234"}
        shell, connection, _ = self._shell(_completed(json.dumps(body) + "\n200"))
        await shell.async_connect()

        payload = await shell.async_pair_local(("owner", "correct-horse"))

        self.assertEqual(payload["claim_code"], "abcd-1234")
        command = connection.run.await_args.args[0]
        self.assertNotIn("correct-horse", command)
        self.assertEqual(
            json.loads(connection.run.await_args.kwargs["input"]),
            {"owner": {"username": "owner", "password": "correct-horse"}},
        )

    async def test_pair_local_without_owner_sends_an_empty_object(self) -> None:
        body = {"ok": True, "pairing_state": "claimed", "repair_code": "r"}
        shell, connection, _ = self._shell(_completed(json.dumps(body) + "\n200"))
        await shell.async_connect()

        await shell.async_pair_local(None)

        self.assertEqual(connection.run.await_args.kwargs["input"], "{}")

    async def test_pair_local_raises_the_companion_error_code(self) -> None:
        body = {"ok": False, "error": {"code": "invalid_owner", "message": "invalid owner credentials"}}
        shell, _, _ = self._shell(_completed(json.dumps(body) + "\n400"))
        await shell.async_connect()

        with self.assertRaises(installer.PairLocalError) as caught:
            await shell.async_pair_local(("owner", "short"))

        self.assertEqual((caught.exception.status, caught.exception.code), (400, "invalid_owner"))

    async def test_pair_local_reports_an_unreachable_companion(self) -> None:
        shell, _, _ = self._shell(_completed("\n000"))
        await shell.async_connect()

        with self.assertRaises(installer.PairLocalError) as caught:
            await shell.async_pair_local(None)

        self.assertEqual(caught.exception.code, "cannot_connect")

    async def test_wait_stops_at_once_when_the_host_key_changes(self) -> None:
        shell, _, connector = self._shell(OSError("reset"))
        await shell.async_connect()
        connector.side_effect = installer.ShellHostKeyMismatch("changed")

        with self.assertRaises(installer.InstallerFailed) as caught:
            await shell.async_wait_for_installer(lambda _: None)

        self.assertIn("different SSH host key", caught.exception.lines[0])
        self.assertEqual(connector.await_count, 2)

    async def test_wait_stops_when_the_credentials_are_refused_on_reconnect(self) -> None:
        shell, _, connector = self._shell(OSError("reset"))
        await shell.async_connect()
        connector.side_effect = installer.ShellAuthError("denied")

        with self.assertRaises(installer.InstallerFailed) as caught:
            await shell.async_wait_for_installer(lambda _: None)

        self.assertIn("refused the SSH credentials", caught.exception.lines[0])
        self.assertEqual(connector.await_count, 2)

    async def test_pair_local_rejects_a_body_that_is_not_an_object(self) -> None:
        shell, _, _ = self._shell(_completed("[]\n200"))
        await shell.async_connect()

        with self.assertRaises(installer.PairLocalError) as caught:
            await shell.async_pair_local(None)

        self.assertEqual(caught.exception.code, "invalid_response")

    async def test_pair_local_rejects_an_error_that_is_not_an_object(self) -> None:
        shell, _, _ = self._shell(_completed('{"ok": false, "error": "x"}\n400'))
        await shell.async_connect()

        with self.assertRaises(installer.PairLocalError) as caught:
            await shell.async_pair_local(None)

        self.assertEqual((caught.exception.status, caught.exception.code), (400, "invalid_response"))


class AsyncsshConnectTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_missing_host_key_is_an_error(self) -> None:
        connection = MagicMock()
        connection.get_server_host_key.return_value = None
        with patch.object(installer.asyncssh, "connect", AsyncMock(return_value=connection)):
            with self.assertRaises(installer.ShellConnectError):
                await installer._asyncssh_connect("192.0.2.10", "root", "secret", None)

    async def test_an_unverifiable_host_key_is_a_mismatch(self) -> None:
        error = installer.asyncssh.HostKeyNotVerifiable("no")
        with patch.object(installer.asyncssh, "connect", AsyncMock(side_effect=error)):
            with self.assertRaises(installer.ShellHostKeyMismatch):
                await installer._asyncssh_connect("192.0.2.10", "root", "secret", None)

    async def test_refused_credentials_are_an_auth_error(self) -> None:
        error = installer.asyncssh.PermissionDenied("no")
        with patch.object(installer.asyncssh, "connect", AsyncMock(side_effect=error)):
            with self.assertRaises(installer.ShellAuthError):
                await installer._asyncssh_connect("192.0.2.10", "root", "secret", None)

    async def test_a_malformed_pinned_key_is_a_mismatch(self) -> None:
        with patch.object(installer.asyncssh, "connect", AsyncMock()) as connect:
            with self.assertRaises(installer.ShellHostKeyMismatch):
                await installer._asyncssh_connect("192.0.2.10", "root", "secret", "not a key")
        connect.assert_not_awaited()
