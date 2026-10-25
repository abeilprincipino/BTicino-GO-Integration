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
KEEPALIVE_INTERVAL_SECONDS = 15
KEEPALIVE_COUNT_MAX = 3
# Every command here is short (the installer itself runs detached); a longer
# wait means the session is dead even if TCP has not noticed yet.
RUN_TIMEOUT_SECONDS = 60
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
    try:
        if expected_host_key:
            known_hosts = ([asyncssh.import_public_key(expected_host_key)], [], [])
        connection = await asyncssh.connect(
            host,
            username=username,
            password=password,
            known_hosts=known_hosts,
            client_keys=None,
            agent_path=None,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            keepalive_interval=KEEPALIVE_INTERVAL_SECONDS,
            keepalive_count_max=KEEPALIVE_COUNT_MAX,
        )
    except (asyncssh.HostKeyNotVerifiable, asyncssh.KeyImportError) as err:
        raise ShellHostKeyMismatch(str(err)) from err
    except asyncssh.PermissionDenied as err:
        raise ShellAuthError(str(err)) from err
    except (OSError, asyncssh.Error) as err:
        raise ShellConnectError(str(err)) from err
    key = connection.get_server_host_key()
    if key is None:
        connection.close()
        raise ShellConnectError("the intercom did not present an SSH host key")
    host_key = key.export_public_key("openssh").decode().strip()
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
        # Spec section 8: setsid when the BusyBox has it, nohup otherwise.
        # curl first: the intercom's BusyBox wget fails the TLS handshake with GitHub.
        url = shlex.quote(INSTALL_SCRIPT_URL)
        command = (
            f"rm -f {INSTALL_LOG} {INSTALL_RC} && "
            "if command -v curl >/dev/null 2>&1; "
            f"then curl -fsSL -o {INSTALL_SCRIPT} {url}; "
            f"else wget -qO {INSTALL_SCRIPT} {url}; fi && "
            "detach=$(command -v setsid >/dev/null 2>&1 && echo setsid || echo nohup) && "
            f"($detach sh -c 'sh {INSTALL_SCRIPT} >{INSTALL_LOG} 2>&1; echo $? >{INSTALL_RC}' "
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
                except ShellHostKeyMismatch:
                    raise InstallerFailed(
                        ("FAIL: The intercom presented a different SSH host key while installing; "
                         "stopped following the installation.",)
                    ) from None
                except ShellAuthError:
                    raise InstallerFailed(
                        ("FAIL: The intercom refused the SSH credentials when reconnecting.",)
                    ) from None
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
        command = (
            "curl -sS --max-time 30 -X POST -H 'Content-Type: application/json' --data-binary @- "
            f"-w '\\n%{{http_code}}' {PAIR_LOCAL_URL}"
        )
        try:
            result = await self._run(command, input=json.dumps(body))
        except ShellConnectError:
            # The session may have idled out while the user filled the form.
            # The reconnection stays pinned to the host key seen first.
            await self.async_connect()
            result = await self._run(command, input=json.dumps(body))
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
        if not isinstance(payload, dict):
            raise PairLocalError(status, "invalid_response")
        if status != 200 or not payload.get("ok"):
            error = payload.get("error") or {}
            if not isinstance(error, dict):
                raise PairLocalError(status, "invalid_response")
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
            async with asyncio.timeout(RUN_TIMEOUT_SECONDS):
                return await self._connection.run(command, input=input, check=False)
        except TimeoutError as err:
            raise ShellConnectError("the intercom did not answer in time") from err
        except (OSError, asyncssh.Error) as err:
            raise ShellConnectError(str(err)) from err
