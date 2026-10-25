# Install from Home Assistant — Integration Side Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user install, pair and reinstall the companion from the integration's config flow, giving only the intercom's IP and SSH credentials.

**Architecture:** All SSH work lives in a new module `installer.py` (`IntercomShell`, progress parsing), which the config flow uses through a small interface and which the tests replace with mocks. The flow gains a menu, an install path (`install` → `install_run` progress → `install_failed` / `pair` → `owner` → `reboot`) and a `reconfigure` path that reuses the same steps. Pairing uses the companion's `POST /api/v3/pair/local` over SSH and then the existing claim/recover code.

**Tech Stack:** Python 3.13, Home Assistant ≥ 2025.1.1 (from `hacs.json`), `asyncssh`, `unittest` (`IsolatedAsyncioTestCase`, as the existing tests).

**Spec:** `BTicinoGO/docs/superpowers/specs/2026-10-22-ha-driven-install-design.md`, section 5. **Depends on** the companion plan (`BTicinoGO/docs/superpowers/plans/2026-10-22-ha-driven-install-companion.md`) only for the end-to-end check in Task 4; Tasks 1–3 test against mocks.

## Global Constraints

- Repository: `D:/Progetti/BTicinoGO-Integration`. Create branch `feat/ha-driven-install` from `origin/v3` (the fork's default branch; there is no `main` to merge into — "merge" means fast-forwarding `v3`).
- The SSH password is held only in memory for the length of the flow. It must never be written to the config entry, to logs or to translations.
- `config_flow.py` must not import `asyncssh`; only `installer.py` does.
- New user-visible strings go in `custom_components/bticino_companion/translations/en.json` only.
- Tests: `unittest`, in `tests/`, importing `bticino_companion.*` after putting `custom_components` on `sys.path`, and skipping with `unittest.SkipTest` when `homeassistant` is not installed — like `tests/test_config_flow.py`.
- Run tests with `.venv/Scripts/python -m unittest discover -s tests -v` (Task 1 creates the venv). If Home Assistant cannot be installed in the venv on this host, the HA-dependent tests skip: say so plainly in the report, do not claim they passed.
- Commit messages: conventional style (`feat(config-flow): …`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The companion's endpoints: `GET /api/v3/auth/status` (`device_id`, `instance_id`, `model`, `pairing_state`), `POST /api/v3/pair/local` (loopback only; body `{}` or `{"owner":{"username","password"}}`; 200 with `device_id`, `instance_id`, `pairing_state` and `claim_code`, or `repair_code` + `repair_expires_at`; 400 `invalid_owner`; 409 `pairing_error`).
- Owner rules (same as the companion): username not blank, password ≥ 8 characters, neither equal to `companion`.

---

### Task 1: `installer.py` — SSH shell, installer runner, progress parsing

Note on `async_connect`: the flow opens the session once to check the credentials, and `_async_install` (Task 2) reconnects at the start of every run, so a retry after a failure never reuses a dead session.

**Files:**
- Create: `custom_components/bticino_companion/installer.py`
- Create: `tests/test_installer.py`
- Modify: `custom_components/bticino_companion/manifest.json` (`requirements`)

**Interfaces:**
- Produces (used by Tasks 2–3):
  - `class ShellError(Exception)`; subclasses `ShellConnectError`, `ShellAuthError`, `ShellHostKeyMismatch`, `InstallerFailed(lines: tuple[str, ...])` (attribute `.lines`), `PairLocalError(status: int, code: str, message: str = "")` (attributes `.status`, `.code`).
  - `@dataclass(frozen=True) class InstallerProgress(lines: tuple[str, ...], exit_code: int | None)` with properties `finished: bool`, `fraction: float`, `failure_lines: tuple[str, ...]`, `installed_version: str | None`.
  - `def parse_progress(output: str) -> InstallerProgress`
  - `class IntercomShell(host: str, username: str, password: str, *, expected_host_key: str | None = None, connector=None, sleep=asyncio.sleep, clock=time.monotonic)` with attribute `host_key: str` and methods `async_connect() -> str`, `async_start_installer() -> None`, `async_read_progress() -> InstallerProgress`, `async_wait_for_installer(on_progress: Callable[[float], None]) -> InstallerProgress`, `async_pair_local(owner: tuple[str, str] | None) -> dict[str, Any]`, `async_close() -> None`.

- [ ] **Step 1: Create the branch and the test environment**

```bash
cd /d/Progetti/BTicinoGO-Integration
git fetch origin && git switch -c feat/ha-driven-install origin/v3
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/python -m pip install "homeassistant==2025.1.4" "asyncssh==2.19.0"
.venv/Scripts/python -c "import homeassistant.const as c, asyncssh; print(c.__version__, asyncssh.__version__)"
```

Expected: prints `2025.1.4 2.19.0`. If `homeassistant` fails to install on Windows, keep going with `asyncssh` alone (`pip install "asyncssh==2.19.0"`): `tests/test_installer.py` does not need Home Assistant for the parsing tests (see Step 2), and the flow tests will skip. `.venv/` is already in `.gitignore`.

Then confirm the progress API this plan relies on exists in the installed version:

```bash
.venv/Scripts/python -c "from homeassistant.data_entry_flow import FlowHandler as F; print(hasattr(F, 'async_update_progress'), hasattr(F, 'async_show_progress'))"
```

Expected: `True True`. If `async_update_progress` is missing, drop the `on_progress` callback in Task 2 (pass `lambda _: None`) and note it in the report. Also confirm where `async_show_progress_done` puts the next step — the Task 2 tests expect it in `step_id`:

```bash
.venv/Scripts/python -c "import inspect; from homeassistant.data_entry_flow import FlowHandler as F; print(inspect.getsource(F.async_show_progress_done))"
```

If it uses another key, change the three `done["step_id"]` assertions in Task 2 accordingly.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_installer.py`. The installer module itself does not import Home Assistant, but importing it through the package runs `bticino_companion/__init__.py`, which does; so it is loaded from its file directly:

```python
"""Tests for the SSH installer used by the install config flow."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

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
    def _shell(self, *runs: SimpleNamespace, connect_error: Exception | None = None):
        connection = MagicMock()
        connection.run = AsyncMock(side_effect=list(runs))
        connection.wait_closed = AsyncMock()
        connector = AsyncMock(return_value=(connection, "ssh-ed25519 AAAAkey"))
        if connect_error is not None:
            connector.side_effect = connect_error
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
        shell, connection, connector = self._shell(OSError("reset"), _completed(finished))
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m unittest tests.test_installer -v`
Expected: ERROR — `FileNotFoundError` for `installer.py`.

- [ ] **Step 4: Implement `installer.py`**

```python
"""Install the Companion on the intercom over SSH.

The config flow drives this module and never touches SSH itself. The SSH
password lives only in an IntercomShell instance, for the length of the flow.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import logging
import shlex
import time
from typing import Any

import asyncssh

_LOGGER = logging.getLogger(__name__)

INSTALL_SCRIPT_URL = (
    "https://github.com/abeilprincipino/BTicino-GO-Companion/raw/main/scripts/install.sh"
)
INSTALL_SCRIPT = "/tmp/companion-install.sh"
INSTALL_LOG = "/tmp/companion-install.log"
INSTALL_RC = "/tmp/companion-install.rc"
PAIR_LOCAL_URL = "http://127.0.0.1:8080/api/v3/pair/local"
RC_MARKER = "--companion-install-rc--"

CONNECT_TIMEOUT_SECONDS = 15
POLL_INTERVAL_SECONDS = 2
INSTALL_TIMEOUT_SECONDS = 600
MAX_RECONNECTS = 3

# Installer lines that mark how far it got, in the order it prints them.
_MILESTONES = (
    ("Downloading latest release bundle", 0.1),
    ("Installed binary", 0.4),
    ("Installed gst runtime", 0.5),
    ("Registered init service", 0.6),
    ("Provisioning SIP inbound", 0.75),
    ("Waiting for health endpoint", 0.9),
)


class ShellError(Exception):
    """An SSH operation on the intercom failed."""


class ShellConnectError(ShellError):
    """The intercom could not be reached over SSH."""


class ShellAuthError(ShellError):
    """The intercom refused the SSH credentials."""


class ShellHostKeyMismatch(ShellError):
    """The intercom presented a different SSH host key than expected."""


class InstallerFailed(ShellError):
    """The installer did not complete; lines say why."""

    def __init__(self, lines: tuple[str, ...]) -> None:
        super().__init__("\n".join(lines))
        self.lines = lines


class PairLocalError(ShellError):
    """The Companion refused or could not answer the local pairing request."""

    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(f"{status} {code} {message}".strip())
        self.status = status
        self.code = code


@dataclass(frozen=True)
class InstallerProgress:
    """What the installer printed so far, and its exit code once it ended."""

    lines: tuple[str, ...]
    exit_code: int | None

    @property
    def finished(self) -> bool:
        return self.exit_code is not None

    @property
    def fraction(self) -> float:
        if self.finished:
            return 1.0
        reached = [
            fraction for marker, fraction in _MILESTONES if any(marker in line for line in self.lines)
        ]
        return max(reached, default=0.0)

    @property
    def failure_lines(self) -> tuple[str, ...]:
        failures = tuple(line for line in self.lines if line.startswith("FAIL:"))
        return failures or self.lines[-10:]

    @property
    def installed_version(self) -> str | None:
        prefix = "INFO: Installed version: "
        for line in reversed(self.lines):
            if line.startswith(prefix):
                return line.removeprefix(prefix).strip()
        return None


def parse_progress(output: str) -> InstallerProgress:
    """Parse the log and exit-code files as read by IntercomShell."""
    log, marker, rc = output.partition(RC_MARKER)
    lines = tuple(line for line in log.splitlines() if line.strip())
    exit_code: int | None = None
    if marker and rc.strip():
        try:
            exit_code = int(rc.strip())
        except ValueError:
            exit_code = None
    return InstallerProgress(lines, exit_code)


Connector = Callable[[str, str, str, "str | None"], Awaitable[tuple[Any, str]]]


async def _asyncssh_connect(
    host: str, username: str, password: str, expected_host_key: str | None
) -> tuple[Any, str]:
    known_hosts: Any = None
    if expected_host_key:
        known_hosts = ([asyncssh.import_public_key(expected_host_key)], [], [])
    try:
        connection = await asyncssh.connect(
            host,
            username=username,
            password=password,
            known_hosts=known_hosts,
            client_keys=None,
            agent_path=None,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except asyncssh.HostKeyNotVerifiable as err:
        raise ShellHostKeyMismatch(str(err)) from err
    except asyncssh.PermissionDenied as err:
        raise ShellAuthError(str(err)) from err
    except (OSError, asyncssh.Error) as err:
        raise ShellConnectError(str(err)) from err
    key = connection.get_server_host_key()
    host_key = key.export_public_key("openssh").decode().strip() if key is not None else ""
    return connection, host_key


class IntercomShell:
    """One SSH session to the intercom, reopened as needed during a flow."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        expected_host_key: str | None = None,
        connector: Connector | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._expected_host_key = expected_host_key
        self._connector = connector or _asyncssh_connect
        self._sleep = sleep
        self._clock = clock
        self._connection: Any = None
        self.host_key = ""

    async def async_connect(self) -> str:
        """Open the session and return the intercom's host key."""
        await self.async_close()
        self._connection, host_key = await self._connector(
            self._host, self._username, self._password, self._expected_host_key
        )
        # A reconnection during the flow must reach the same intercom.
        self._expected_host_key = host_key
        self.host_key = host_key
        return host_key

    async def async_start_installer(self) -> None:
        """Download install.sh on the intercom and start it detached from the session."""
        command = (
            f"rm -f {INSTALL_LOG} {INSTALL_RC} && "
            f"wget -qO {INSTALL_SCRIPT} {shlex.quote(INSTALL_SCRIPT_URL)} && "
            f"(setsid sh -c 'sh {INSTALL_SCRIPT} >{INSTALL_LOG} 2>&1; echo $? >{INSTALL_RC}' "
            "</dev/null >/dev/null 2>&1 &)"
        )
        result = await self._run(command)
        if result.exit_status != 0:
            detail = str(result.stderr or "").strip()
            raise InstallerFailed(
                (f"FAIL: Could not download the installer from {INSTALL_SCRIPT_URL} "
                 f"(exit {result.exit_status}){': ' + detail if detail else ''}.",)
            )

    async def async_read_progress(self) -> InstallerProgress:
        result = await self._run(
            f"cat {INSTALL_LOG} 2>/dev/null; echo {RC_MARKER}; cat {INSTALL_RC} 2>/dev/null"
        )
        return parse_progress(str(result.stdout or ""))

    async def async_wait_for_installer(
        self, on_progress: Callable[[float], None]
    ) -> InstallerProgress:
        """Follow the installer until it ends; raise InstallerFailed unless it succeeded."""
        deadline = self._clock() + INSTALL_TIMEOUT_SECONDS
        failed_reads = 0
        while True:
            try:
                progress = await self.async_read_progress()
            except ShellError:
                failed_reads += 1
                if failed_reads > MAX_RECONNECTS:
                    raise InstallerFailed(
                        ("FAIL: Lost the SSH connection to the intercom while it was installing.",)
                    ) from None
                try:
                    await self.async_connect()
                except ShellError as err:
                    _LOGGER.debug("Reconnection to the intercom failed: %s", err)
                await self._sleep(POLL_INTERVAL_SECONDS)
                continue

            failed_reads = 0
            on_progress(progress.fraction)
            if progress.finished:
                if progress.exit_code != 0:
                    raise InstallerFailed(progress.failure_lines)
                return progress
            if self._clock() >= deadline:
                raise InstallerFailed(
                    ("FAIL: The installation did not finish within 10 minutes.", *progress.lines[-10:])
                )
            await self._sleep(POLL_INTERVAL_SECONDS)

    async def async_pair_local(self, owner: tuple[str, str] | None) -> dict[str, Any]:
        """Ask the Companion, from the intercom itself, for a claim or repair code."""
        body: dict[str, Any] = {}
        if owner is not None:
            body = {"owner": {"username": owner[0], "password": owner[1]}}
        # The body goes on stdin so that the password never shows in a process list.
        result = await self._run(
            "curl -sS -X POST -H 'Content-Type: application/json' --data-binary @- "
            f"-w '\\n%{{http_code}}' {PAIR_LOCAL_URL}",
            input=json.dumps(body),
        )
        payload_text, _, status_text = str(result.stdout or "").rstrip("\n").rpartition("\n")
        try:
            status = int(status_text.strip())
        except ValueError:
            raise PairLocalError(0, "invalid_response") from None
        if status == 0:
            raise PairLocalError(0, "cannot_connect")
        try:
            payload = json.loads(payload_text)
        except ValueError:
            raise PairLocalError(status, "invalid_response") from None
        if status != 200 or not payload.get("ok"):
            error = payload.get("error") or {}
            raise PairLocalError(status, str(error.get("code", "unknown")), str(error.get("message", "")))
        return payload

    async def async_close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        connection.close()
        try:
            await connection.wait_closed()
        except (OSError, asyncssh.Error):
            pass

    async def _run(self, command: str, *, input: str | None = None) -> Any:
        if self._connection is None:
            raise ShellConnectError("not connected")
        try:
            return await self._connection.run(command, input=input, check=False)
        except (OSError, asyncssh.Error) as err:
            raise ShellConnectError(str(err)) from err
```

Add the requirement to `custom_components/bticino_companion/manifest.json`:

```json
  "requirements": ["asyncssh==2.19.0"],
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m unittest tests.test_installer -v`
Expected: all tests PASS.
Run: `.venv/Scripts/python -m unittest discover -s tests -v`
Expected: no failures (HA-dependent suites may report `skipped` if Home Assistant is not installed).

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-10-22-ha-driven-install-integration.md
git commit -m "docs(plan): implementation plan for installing the Companion from Home Assistant" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git add custom_components/bticino_companion/installer.py custom_components/bticino_companion/manifest.json tests/test_installer.py
git commit -m "feat(installer): run the Companion installer and pair over SSH" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Config flow — menu, install path, pairing over SSH, reboot

**Files:**
- Modify: `custom_components/bticino_companion/const.py` (new `CONF_*` keys)
- Modify: `custom_components/bticino_companion/config_flow.py`
- Modify: `custom_components/bticino_companion/translations/en.json` (`config` section)
- Test: `tests/test_config_flow_install.py` (new)

**Interfaces:**
- Consumes: everything Task 1 produces; `CompanionApiClient.async_get_pairing_status`, `async_pair_challenge`, `async_pair_claim`, `async_recover_bearer`, `async_reboot`, `access_token` (existing, `api.py`).
- Produces (used by Task 3): flow attributes `_shell`, `_host`, `_ssh_username`, `_install_task`, `_install_failure`, `_owner`, `_auth_data`, `_is_reconfigure`; methods `_async_open_shell(host, username, password, expected_host_key) -> str | None` (returns an error key or `None`), `async_step_install_run`, `async_step_pair`, `async_step_reboot`, `_async_finish_install()`; constants `CONF_SSH_USERNAME = "ssh_username"`, `CONF_SSH_HOST_KEY = "ssh_host_key"`, `CONF_OWNER_USERNAME = "owner_username"`, `CONF_OWNER_PASSWORD = "owner_password"`, `CONF_ACCEPT_NEW_HOST_KEY = "accept_new_host_key"`, `DEFAULT_SSH_USERNAME = "root"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_flow_install.py`:

```python
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
    flow.async_update_progress = MagicMock()
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
        await flow._install_task
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m unittest tests.test_config_flow_install -v`
Expected: ERROR on import — `cannot import name 'CONF_OWNER_PASSWORD'` (or SKIP if Home Assistant is not installed; then say so and continue, relying on `python -m py_compile` in Step 5).

- [ ] **Step 3: Implement**

1. `const.py`, after `CONF_REPAIR_CODE = "repair_code"`:

```python
CONF_SSH_USERNAME = "ssh_username"
CONF_SSH_HOST_KEY = "ssh_host_key"
CONF_OWNER_USERNAME = "owner_username"
CONF_OWNER_PASSWORD = "owner_password"
CONF_ACCEPT_NEW_HOST_KEY = "accept_new_host_key"
DEFAULT_SSH_USERNAME = "root"
# The Companion web UI's bootstrap credentials, which an owner may not keep.
DEFAULT_OWNER_CREDENTIAL = "companion"
MIN_OWNER_PASSWORD_LENGTH = 8
```

2. `config_flow.py` imports — replace the import block with:

```python
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import ipaddress
import logging
import re
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import CompanionApiClient, CompanionApiError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLAIM_CODE,
    CONF_COMPANION_URL,
    CONF_DEVICE_ID,
    CONF_INSTANCE_ID,
    CONF_OWNER_PASSWORD,
    CONF_OWNER_USERNAME,
    CONF_REPAIR_CODE,
    CONF_SSH_HOST_KEY,
    CONF_SSH_USERNAME,
    DATA_PENDING_REAUTH_URLS,
    DEFAULT_OWNER_CREDENTIAL,
    DEFAULT_PORT,
    DEFAULT_SSH_USERNAME,
    DOMAIN,
    MIN_OWNER_PASSWORD_LENGTH,
)
from .installer import (
    InstallerFailed,
    IntercomShell,
    PairLocalError,
    ShellAuthError,
    ShellError,
    ShellHostKeyMismatch,
)
```

3. In `CompanionConfigFlow.__init__`, after `self._is_reauth = False`, add:

```python
        self._is_reconfigure = False
        self._shell: IntercomShell | None = None
        self._host = ""
        self._ssh_username = ""
        self._install_task: asyncio.Task[None] | None = None
        self._install_failure: tuple[str, ...] = ()
        self._owner: tuple[str, str] | None = None
        self._auth_data: dict[str, Any] = {}
```

4. Rename the current `async_step_user` to `async_step_connect` and change its `step_id="user"` to `step_id="connect"`. Add the new `async_step_user` before it:

```python
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer to connect to an installed Companion or to install one."""
        return self.async_show_menu(step_id="user", menu_options=["connect", "install"])
```

5. Add these methods after `async_step_recover` (before `_async_step_for_pairing_state`):

```python
    async def async_step_install(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the intercom's SSH access and open a session to it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input.get(CONF_HOST, "")).strip()
            username = str(user_input.get(CONF_USERNAME, "")).strip() or DEFAULT_SSH_USERNAME
            error = await self._async_open_shell(host, username, str(user_input.get(CONF_PASSWORD, "")), None)
            if error is None:
                return await self.async_step_install_run()
            errors["base"] = error
        return self.async_show_form(step_id="install", data_schema=_install_schema(user_input), errors=errors)

    async def async_step_install_run(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Run the installer on the intercom and follow it."""
        if self._install_task is None:
            self._install_task = self.hass.async_create_task(self._async_install())
        if not self._install_task.done():
            return self.async_show_progress(
                step_id="install_run", progress_action="install", progress_task=self._install_task
            )
        task, self._install_task = self._install_task, None
        try:
            task.result()
        except InstallerFailed as err:
            self._install_failure = err.lines
            return self.async_show_progress_done(next_step_id="install_failed")
        return self.async_show_progress_done(next_step_id="pair")

    async def async_step_install_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show why the installation failed and offer to run it again."""
        if user_input is not None:
            return await self.async_step_install_run()
        return self.async_show_form(
            step_id="install_failed",
            data_schema=vol.Schema({}),
            description_placeholders={"failure": "\n".join(self._install_failure)},
        )

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pair with the Companion that was just installed."""
        url = _normalize_url(self._host)
        try:
            await self._async_load_pairing_status(url)
        except CompanionApiError:
            self._install_failure = (f"FAIL: The Companion was installed but does not answer at {url}.",)
            return await self.async_step_install_failed()
        if self._is_reconfigure and self._device_id != self._get_reconfigure_entry().unique_id:
            return await self._async_abort_install("wrong_device")
        if self._pairing_state == "setup_required" and self._owner is None:
            return await self.async_step_owner()
        return await self._async_pair_over_ssh()

    async def async_step_owner(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the Companion web UI owner, which a new Companion does not have yet."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input.get(CONF_OWNER_USERNAME, "")).strip()
            password = str(user_input.get(CONF_OWNER_PASSWORD, ""))
            if _valid_owner(username, password):
                self._owner = (username, password)
                return await self._async_pair_over_ssh()
            errors["base"] = "invalid_owner"
        return self.async_show_form(step_id="owner", data_schema=_owner_schema(), errors=errors)

    async def async_step_reboot(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer the reboot the intercom needs before calls reach the Companion."""
        return self.async_show_menu(step_id="reboot", menu_options=["reboot_now", "reboot_later"])

    async def async_step_reboot_now(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        client = CompanionApiClient(
            async_get_clientsession(self.hass), self._url, str(self._auth_data[CONF_ACCESS_TOKEN])
        )
        try:
            await client.async_reboot()
        except CompanionApiError as err:
            _LOGGER.warning("Companion reboot request failed: %s", type(err).__name__)
        return await self._async_finish_install()

    async def async_step_reboot_later(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_finish_install()

    @callback
    def async_remove(self) -> None:
        """Stop following the installer and close SSH when the flow goes away."""
        if self._install_task is not None:
            self._install_task.cancel()
        if self._shell is not None:
            self.hass.async_create_task(self._shell.async_close())
            self._shell = None

    async def _async_open_shell(
        self, host: str, username: str, password: str, expected_host_key: str | None
    ) -> str | None:
        """Open the SSH session the install steps use; return an error key on failure."""
        if not host:
            return "cannot_connect"
        shell = IntercomShell(host, username, password, expected_host_key=expected_host_key)
        try:
            await shell.async_connect()
        except ShellAuthError:
            return "invalid_auth"
        except ShellHostKeyMismatch:
            return "host_key_changed"
        except ShellError as err:
            _LOGGER.warning("SSH connection to the intercom failed: %s", type(err).__name__)
            return "cannot_connect"
        await self._async_close_shell()
        self._shell, self._host, self._ssh_username = shell, host, username
        return None

    async def _async_install(self) -> None:
        if self._shell is None:
            raise InstallerFailed(("FAIL: No SSH session to the intercom.",))
        try:
            await self._shell.async_connect()
            await self._shell.async_start_installer()
            progress = await self._shell.async_wait_for_installer(self.async_update_progress)
        except InstallerFailed:
            raise
        except ShellError as err:
            raise InstallerFailed((f"FAIL: SSH to the intercom failed: {err}",)) from err
        _LOGGER.info("Companion %s installed on %s", progress.installed_version or "(unknown version)", self._host)

    async def _async_pair_over_ssh(self) -> ConfigFlowResult:
        if self._shell is None:
            return await self._async_abort_install("cannot_connect")
        try:
            local = await self._shell.async_pair_local(self._owner)
        except PairLocalError as err:
            if err.code == "invalid_owner":
                self._owner = None
                return self.async_show_form(
                    step_id="owner", data_schema=_owner_schema(), errors={"base": "invalid_owner"}
                )
            _LOGGER.warning("Companion local pairing failed: %s %s", err.status, err.code)
            return await self._async_abort_install("pairing_error")
        except ShellError:
            return await self._async_abort_install("cannot_connect")

        if (
            str(local.get(CONF_DEVICE_ID, "")) != self._device_id
            or str(local.get(CONF_INSTANCE_ID, "")) != self._instance_id
        ):
            return await self._async_abort_install("wrong_device")

        repair_code = str(local.get("repair_code", "")).strip()
        try:
            if repair_code:
                data = await self._async_authorize(repair_code, recovery=True)
            else:
                data = await self._async_authorize(str(local.get("claim_code", "")), recovery=False)
        except CompanionApiError as err:
            _LOGGER.warning("Companion authorization after install failed: %s", type(err).__name__)
            return await self._async_abort_install("pairing_error")

        self._auth_data = {
            **data,
            CONF_SSH_USERNAME: self._ssh_username,
            CONF_SSH_HOST_KEY: self._shell.host_key,
        }
        await self._async_close_shell()
        return await self.async_step_reboot()

    async def _async_finish_install(self) -> ConfigFlowResult:
        await self.async_set_unique_id(self._device_id)
        if self._is_reconfigure:
            self._abort_if_unique_id_mismatch(reason="wrong_device")
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates=self._auth_data
            )
        self._abort_if_unique_id_configured(updates=self._auth_data)
        return self.async_create_entry(title=self._model or self._device_id, data=self._auth_data)

    async def _async_abort_install(self, reason: str) -> ConfigFlowResult:
        await self._async_close_shell()
        return self.async_abort(reason=reason)

    async def _async_close_shell(self) -> None:
        shell, self._shell = self._shell, None
        if shell is not None:
            await shell.async_close()
```

6. Add the schema and owner helpers next to `_claim_schema`:

```python
def _install_schema(user_input: Mapping[str, Any] | None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=str(user_input.get(CONF_HOST, ""))): str,
            vol.Required(
                CONF_USERNAME, default=str(user_input.get(CONF_USERNAME, DEFAULT_SSH_USERNAME))
            ): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


def _owner_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_OWNER_USERNAME): str, vol.Required(CONF_OWNER_PASSWORD): str})


def _valid_owner(username: str, password: str) -> bool:
    """Mirror the Companion's owner rules so a mistake shows before anything is sent."""
    return (
        bool(username)
        and len(password) >= MIN_OWNER_PASSWORD_LENGTH
        and username != DEFAULT_OWNER_CREDENTIAL
        and password != DEFAULT_OWNER_CREDENTIAL
    )
```

7. `translations/en.json`, `config` section — replace `"step": { "user": {...}` with a menu and add the new steps, the progress text and the new errors and aborts. The full `config` object becomes:

```json
  "config": {
    "step": {
      "user": {
        "description": "Connect to a Companion that is already running on the intercom, or install it on a rooted intercom over SSH.",
        "menu_options": {
          "connect": "Connect to an installed Companion",
          "install": "Install the Companion on the intercom"
        }
      },
      "connect": {
        "data": {
          "companion_url": "Companion URL"
        }
      },
      "install": {
        "description": "The intercom must be rooted and reachable over SSH. The SSH password is used only during this setup and is never stored.",
        "data": {
          "host": "Intercom IP address or host name",
          "username": "SSH username",
          "password": "SSH password"
        }
      },
      "install_failed": {
        "description": "The installation did not complete:\n\n{failure}\n\nSubmit to run the installer again."
      },
      "owner": {
        "description": "Choose the username and password of the Companion web interface. `companion` is not allowed for either, and the password needs at least 8 characters.",
        "data": {
          "owner_username": "Web interface username",
          "owner_password": "Web interface password"
        }
      },
      "reboot": {
        "description": "The Companion is installed and paired. The intercom must be rebooted before calls reach the Companion.",
        "menu_options": {
          "reboot_now": "Reboot the intercom now",
          "reboot_later": "Reboot later"
        }
      },
      "claim": {
        "description": "Open the Companion Home Assistant page and enter its initial claim code.",
        "data": {
          "claim_code": "Claim code (xxxx-xxxx)"
        }
      },
      "recover": {
        "description": "This Companion is already claimed. Open the Companion WebUI, issue a temporary recovery code, and enter it here.",
        "data": {
          "repair_code": "Recovery code (xxxx-xxxx)"
        }
      }
    },
    "progress": {
      "install": "Installing the Companion on the intercom. This takes a few minutes."
    },
    "error": {
      "cannot_connect": "Cannot connect to the Companion.",
      "invalid_auth": "The intercom refused the SSH username or password.",
      "host_key_changed": "The intercom's SSH key changed since the Companion was installed. If you reflashed the intercom, tick the box to accept its new key.",
      "invalid_owner": "Choose a username and a password other than `companion`; the password needs at least 8 characters."
    },
    "abort": {
      "setup_required": "Finish the Companion WebUI setup before connecting Home Assistant.",
      "pairing_error": "Companion pairing needs attention. Open its Home Assistant page for details.",
      "wrong_device": "The Companion no longer matches this Home Assistant entry.",
      "reauth_started": "Companion credentials need to be updated.",
      "cannot_connect": "Cannot connect to the Companion.",
      "already_configured": "This Companion is already configured.",
      "reconfigure_successful": "The Companion was reinstalled."
    }
  },
```

- [ ] **Step 4: Update the existing tests that used the old `user` step**

Run: `grep -n "async_step_user\|step_id.*user" tests/*.py`
For every test that submits a Companion URL to `async_step_user`, call `async_step_connect` instead and expect `step_id` `connect`. (At the time of writing, `tests/test_config_flow.py` has none; if the grep prints nothing, there is nothing to change.)

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python -m unittest discover -s tests -v`
Expected: all PASS, including `tests.test_config_flow_install` and the existing `tests.test_config_flow`. If Home Assistant is not installed, those suites report `skipped`; then also run `.venv/Scripts/python -m py_compile custom_components/bticino_companion/config_flow.py` (expected: no output) and `python -c "import json; json.load(open('custom_components/bticino_companion/translations/en.json', encoding='utf-8'))"` (expected: no output), and report that the flow tests did not run.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bticino_companion/const.py custom_components/bticino_companion/config_flow.py custom_components/bticino_companion/translations/en.json tests/test_config_flow_install.py tests/test_config_flow.py
git commit -m "feat(config-flow): install and pair the Companion over SSH" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Reinstall through `reconfigure`

**Files:**
- Modify: `custom_components/bticino_companion/config_flow.py` (new `async_step_reconfigure`, `_reconfigure_schema`)
- Modify: `custom_components/bticino_companion/translations/en.json` (`config.step.reconfigure`)
- Test: `tests/test_config_flow_install.py` (new test class)

**Interfaces:**
- Consumes: `_async_open_shell`, `async_step_install_run`, `async_step_pair`, `_async_finish_install`, `_is_reconfigure`, `CONF_ACCEPT_NEW_HOST_KEY`, `CONF_SSH_HOST_KEY`, `CONF_SSH_USERNAME` (Task 2).
- Produces: nothing used by later tasks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_flow_install.py`:

```python
class ReconfigureFlowTest(FlowTestCase):
    def reconfigure_flow(self, host_key: str | None = HOST_KEY) -> CompanionConfigFlow:
        flow = make_flow()
        flow.context = {"source": "reconfigure", "entry_id": "entry-id"}
        data = {
            CONF_COMPANION_URL: "http://192.0.2.10:8080",
            CONF_ACCESS_TOKEN: "old-token",
            CONF_SSH_USERNAME: "root",
        }
        if host_key is not None:
            data[CONF_SSH_HOST_KEY] = host_key
        self.entry = MagicMock(unique_id=DEVICE_ID, data=data)
        flow._get_reconfigure_entry = MagicMock(return_value=self.entry)
        flow.async_update_reload_and_abort = MagicMock(
            side_effect=lambda entry, **kwargs: {"type": "abort", "reason": "reconfigure_successful", **kwargs}
        )
        return flow

    async def test_form_is_prefilled_from_the_entry(self) -> None:
        result = await self.reconfigure_flow().async_step_reconfigure()

        self.assertEqual((result["type"], result["step_id"]), ("form", "reconfigure"))
        defaults = {str(key): key.default() for key in result["data_schema"].schema if callable(getattr(key, "default", None))}
        self.assertEqual(defaults[CONF_HOST], "192.0.2.10")
        self.assertEqual(defaults[CONF_USERNAME], "root")

    async def test_reinstall_requires_the_stored_key_and_recovers_the_token(self) -> None:
        shell = make_shell(
            {"ok": True, "device_id": DEVICE_ID, "instance_id": INSTANCE_ID, "pairing_state": "claimed", "repair_code": "r-1"}
        )
        client, flow = make_client("claimed"), self.reconfigure_flow()
        shell_class = MagicMock(return_value=shell)
        with self.patched(shell, client), patch.object(config_flow, "IntercomShell", shell_class):
            started = await flow.async_step_reconfigure(dict(INSTALL_INPUT))
            self.assertEqual(started["type"], "progress")
            await flow._install_task
            await flow.async_step_install_run()
            menu = await flow.async_step_pair()
            self.assertEqual(menu["step_id"], "reboot")
            result = await flow.async_step_reboot_later()

        self.assertEqual(shell_class.call_args.kwargs["expected_host_key"], HOST_KEY)
        client.async_recover_bearer.assert_awaited_once_with("r-1")
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(result["data_updates"][CONF_ACCESS_TOKEN], "new-token")

    async def test_changed_host_key_is_refused(self) -> None:
        shell = make_shell()
        shell.async_connect.side_effect = config_flow.ShellHostKeyMismatch("changed")
        with self.patched(shell, make_client()):
            result = await self.reconfigure_flow().async_step_reconfigure(dict(INSTALL_INPUT))

        self.assertEqual(result["errors"], {"base": "host_key_changed"})

    async def test_accepting_a_new_key_connects_without_the_stored_one(self) -> None:
        shell_class = MagicMock(return_value=make_shell())
        flow = self.reconfigure_flow()
        with self.patched(make_shell(), make_client()), patch.object(config_flow, "IntercomShell", shell_class):
            await flow.async_step_reconfigure({**INSTALL_INPUT, "accept_new_host_key": True})
            flow._install_task.cancel()

        self.assertIsNone(shell_class.call_args.kwargs["expected_host_key"])

    async def test_entry_without_a_stored_key_trusts_the_first_one(self) -> None:
        shell_class = MagicMock(return_value=make_shell())
        flow = self.reconfigure_flow(host_key=None)
        with self.patched(make_shell(), make_client()), patch.object(config_flow, "IntercomShell", shell_class):
            await flow.async_step_reconfigure(dict(INSTALL_INPUT))
            flow._install_task.cancel()

        self.assertIsNone(shell_class.call_args.kwargs["expected_host_key"])

    async def test_a_different_companion_aborts(self) -> None:
        client, flow = make_client(), self.reconfigure_flow()
        client.async_get_pairing_status.return_value = {
            "device_id": "other-device", "instance_id": INSTANCE_ID, "model": "C300X", "pairing_state": "claimed"
        }
        with self.patched(make_shell(), client):
            await self.run_reconfigure(flow)
            result = await flow.async_step_pair()

        self.assertEqual((result["type"], result["reason"]), ("abort", "wrong_device"))

    async def run_reconfigure(self, flow: CompanionConfigFlow) -> dict:
        await flow.async_step_reconfigure(dict(INSTALL_INPUT))
        await flow._install_task
        return await flow.async_step_install_run()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m unittest tests.test_config_flow_install.ReconfigureFlowTest -v`
Expected: FAIL/ERROR — `CompanionConfigFlow` has no `async_step_reconfigure`.

- [ ] **Step 3: Implement**

In `config_flow.py`, import `CONF_ACCEPT_NEW_HOST_KEY` from `.const`, then add after `async_step_install`:

```python
    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Reinstall the Companion over SSH; this also replaces a lost token."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input.get(CONF_HOST, "")).strip()
            username = str(user_input.get(CONF_USERNAME, "")).strip() or DEFAULT_SSH_USERNAME
            expected_host_key = None
            if not user_input.get(CONF_ACCEPT_NEW_HOST_KEY):
                expected_host_key = str(entry.data.get(CONF_SSH_HOST_KEY, "")).strip() or None
            error = await self._async_open_shell(
                host, username, str(user_input.get(CONF_PASSWORD, "")), expected_host_key
            )
            if error is None:
                self._is_reconfigure = True
                return await self.async_step_install_run()
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure", data_schema=_reconfigure_schema(entry.data, user_input), errors=errors
        )
```

and next to `_install_schema`:

```python
def _reconfigure_schema(entry_data: Mapping[str, Any], user_input: Mapping[str, Any] | None) -> vol.Schema:
    user_input = user_input or {}
    host = urlsplit(str(entry_data.get(CONF_COMPANION_URL, ""))).hostname or ""
    username = str(entry_data.get(CONF_SSH_USERNAME, "")) or DEFAULT_SSH_USERNAME
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=str(user_input.get(CONF_HOST, host))): str,
            vol.Required(CONF_USERNAME, default=str(user_input.get(CONF_USERNAME, username))): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_ACCEPT_NEW_HOST_KEY, default=False): bool,
        }
    )
```

In `translations/en.json`, add under `config.step`:

```json
      "reconfigure": {
        "description": "Reinstall the Companion on the intercom over SSH. Its pairing with Home Assistant is kept, and the SSH password is never stored.",
        "data": {
          "host": "Intercom IP address or host name",
          "username": "SSH username",
          "password": "SSH password",
          "accept_new_host_key": "The intercom was reflashed: accept its new SSH key"
        }
      },
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m unittest discover -s tests -v`
Expected: all PASS (or HA suites skipped, reported as such, with `py_compile` and the JSON check from Task 2 Step 5 passing).

- [ ] **Step 5: Commit**

```bash
git add custom_components/bticino_companion/config_flow.py custom_components/bticino_companion/translations/en.json tests/test_config_flow_install.py
git commit -m "feat(config-flow): reinstall the Companion from the entry's reconfigure flow" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: README and the end-to-end check on the intercom

**Files:**
- Modify: `README.md` (installation section)

**Interfaces:** none.

- [ ] **Step 1: Document the install path**

In `README.md`, in the section that explains how to add the integration, add before the manual/zeroconf instructions:

```markdown
### Installing the Companion from Home Assistant

On a rooted intercom reachable over SSH you do not need a terminal:

1. **Settings → Devices & services → Add integration → BTicino Companion → Install the Companion on the intercom.**
2. Enter the intercom's IP address and its SSH username (`root`) and password. The password is used
   only during this setup and is never stored.
3. Home Assistant installs the latest Companion release and shows its progress. If the installer
   fails, its `FAIL:` lines are shown and you can run it again.
4. On a new Companion, choose the username and password of its web interface.
5. Reboot the intercom when asked: incoming calls reach the Companion only after a reboot.

To reinstall later — for example to repair an installation — open the integration entry and choose
**Reconfigure**. The SSH key of the intercom is checked against the one seen at installation; tick
*The intercom was reflashed* only if you really reflashed it.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: install and reinstall the Companion from Home Assistant" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 3: End-to-end check on the real intercom (manual, with the user)**

Prerequisites: the companion plan merged and released (the installer downloads the latest release and `install.sh` from `main`), this branch installed in a Home Assistant instance.

Check, and write down the outcome of each:

1. The SSH server's algorithms are accepted by `asyncssh` (spec §8). If the connection fails with a key-exchange or host-key algorithm error, note the offered algorithms (`ssh -vv root@<ip>`) — enabling legacy algorithms is a follow-up change.
2. `setsid` exists on the intercom (`ssh root@<ip> 'command -v setsid'`).
3. Fresh install: menu → install → progress → owner → reboot now → entry created, entities available after the reboot, web UI login works with the chosen owner.
4. Reinstall over a paired companion through **Reconfigure**: no owner form, token replaced, entities keep working.
5. Home Assistant restarted during `install_run`: the installation still completes (`/tmp/companion-install.rc` is `0`), and running the install flow again pairs.
6. Wrong SSH password: `invalid_auth` on the form.
7. `sh /tmp/uninstall.sh` from the companion plan removes everything; the integration entry then becomes unavailable.

---

## Self-review notes

- Spec §5.1 menu → Task 2; §5.2 steps → Task 2; §5.3 SSH and host key → Task 1 + Task 3; §5.4 runner and progress → Task 1; §5.5 pairing → Task 2; §5.6 reconfigure → Task 3; §5.7 strings → Tasks 2–3; §6 errors → Tasks 1–3; §7 tests → Tasks 1–3; §8 device checks → Task 4.
- Error mapping detail: "Companion not reachable over HTTP after install" shows `install_failed` with its own `FAIL:` line (Task 2, `async_step_pair`).
