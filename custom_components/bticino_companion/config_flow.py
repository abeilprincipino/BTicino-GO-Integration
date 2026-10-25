"""Configuration and reauthentication flow for BTicino Companion v3."""

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
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import CompanionApiClient, CompanionApiError, CompanionConnectionError
from .const import (
    CONF_ACCEPT_NEW_HOST_KEY,
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
    MAX_OWNER_PASSWORD_BYTES,
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

_LOGGER = logging.getLogger(__name__)

_HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
_UNEXPECTED_INSTALL_FAILURE = (
    "FAIL: The installation stopped unexpectedly. See the Home Assistant log for details.",
)
_PAIRING_STATES = frozenset({"setup_required", "claimable", "claimed", "error"})


class CompanionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a Companion using its physical device ID and installation ID."""

    VERSION = 1

    def __init__(self) -> None:
        self._device_id = ""
        self._url = ""
        self._model = ""
        self._instance_id = ""
        self._pairing_state = "error"
        self._is_reauth = False
        self._is_reconfigure = False
        self._shell: IntercomShell | None = None
        self._host = ""
        self._ssh_username = ""
        self._install_task: asyncio.Task[None] | None = None
        self._install_failure: tuple[str, ...] = ()
        self._owner: tuple[str, str] | None = None
        self._removed = False
        self._auth_data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer to connect to an installed Companion or to install one."""
        return self.async_show_menu(step_id="user", menu_options=["connect", "install"])

    async def async_step_connect(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start manual setup by looking up the Companion's public state."""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = _normalize_url(str(user_input.get(CONF_COMPANION_URL, "")))
            try:
                await self._async_load_pairing_status(base_url)
            except CompanionApiError as err:
                _LOGGER.warning("Companion pairing status lookup failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(self._device_id)
                self._abort_if_unique_id_configured(updates={CONF_COMPANION_URL: self._url})
                return await self._async_step_for_pairing_state()
        return self.async_show_form(step_id="connect", data_schema=_user_schema(user_input), errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Use Companion mDNS data to select the initial authorization flow."""
        device_id = _txt(discovery_info.properties, "device_id")
        discovered_url = _zeroconf_url(
            getattr(discovery_info, "host", None), getattr(discovery_info, "port", None)
        )
        pairing_state = _txt(discovery_info.properties, "pairing_state")
        if not device_id or not discovered_url or pairing_state not in _PAIRING_STATES:
            return self.async_abort(reason="cannot_connect")

        try:
            self._set_pairing_status(
                {
                    CONF_DEVICE_ID: device_id,
                    "model": _txt(discovery_info.properties, "model"),
                    CONF_INSTANCE_ID: _txt(discovery_info.properties, "instance_id"),
                    "pairing_state": pairing_state,
                },
                discovered_url,
            )
        except CompanionApiError:
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(self._device_id)
        existing_entries = self._async_current_entries()
        if existing_entries:
            return await self._async_handle_existing_discovery(existing_entries[0])
        self.context["title_placeholders"] = {"name": self._model or self._device_id}
        return await self._async_step_for_pairing_state()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Select claim or recovery after Home Assistant loses authorization."""
        self._is_reauth = True
        entry = self._get_reauth_entry()
        pending_urls = self.hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PENDING_REAUTH_URLS, {}
        )
        base_url = pending_urls.pop(
            entry.entry_id, _normalize_url(str(entry_data.get(CONF_COMPANION_URL, "")))
        )
        try:
            await self._async_load_pairing_status(base_url)
        except CompanionApiError as err:
            _LOGGER.warning("Companion reauthentication status lookup failed: %s", type(err).__name__)
            return self.async_abort(reason="cannot_connect")

        if self._device_id != str(entry_data.get(CONF_DEVICE_ID, "")).strip():
            return self.async_abort(reason="wrong_device")
        await self.async_set_unique_id(self._device_id)
        return await self._async_step_for_pairing_state()

    async def _async_handle_existing_discovery(self, entry: Any) -> ConfigFlowResult:
        stored_instance_id = str(entry.data.get(CONF_INSTANCE_ID, "")).strip()
        if stored_instance_id == self._instance_id and self._pairing_state == "claimed":
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_COMPANION_URL: self._url}
            )
            runtime = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if runtime is not None:
                await runtime.async_update_base_url(self._url)
            return self.async_abort(reason="already_configured")

        pending_urls = self.hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PENDING_REAUTH_URLS, {}
        )
        pending_urls[entry.entry_id] = self._url
        entry.async_start_reauth(self.hass)
        return self.async_abort(reason="reauth_started")

    async def async_step_claim(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Claim an unpaired Companion with its owner-visible initial code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await self._async_authorize(str(user_input.get(CONF_CLAIM_CODE, "")), recovery=False)
            except CompanionApiError as err:
                _LOGGER.warning("Companion initial claim failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish_authorization(data)
        return self.async_show_form(step_id="claim", data_schema=_claim_schema(), errors=errors)

    async def async_step_recover(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Recover an already-claimed Companion with an owner-issued code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await self._async_authorize(str(user_input.get(CONF_REPAIR_CODE, "")), recovery=True)
            except CompanionApiError as err:
                _LOGGER.warning("Companion credential recovery failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish_authorization(data)
        return self.async_show_form(step_id="recover", data_schema=_repair_schema(), errors=errors)

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

    async def async_step_install_run(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Run the installer on the intercom and follow it."""
        if self._install_task is None:
            # A background task: it may legitimately outlive a Home Assistant stop.
            self._install_task = self.hass.async_create_background_task(
                self._async_install(), "bticino_companion_install"
            )
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
        except asyncio.CancelledError:
            if self._removed:
                raise
            _LOGGER.error("The Companion installation was cancelled unexpectedly")
            self._install_failure = _UNEXPECTED_INSTALL_FAILURE
            return self.async_show_progress_done(next_step_id="install_failed")
        except Exception:  # noqa: BLE001 - any failure must end on the retry form, not a 500
            _LOGGER.exception("The Companion installation failed unexpectedly")
            self._install_failure = _UNEXPECTED_INSTALL_FAILURE
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
        if self._is_reconfigure:
            if self._device_id != self._get_reconfigure_entry().unique_id:
                return await self._async_abort_install("wrong_device")
        else:
            # Stop before pair/local issues codes and the user goes through the reboot menu.
            await self.async_set_unique_id(self._device_id, raise_on_progress=False)
            try:
                # Not "already_configured": that text is shared with discovery.
                self._abort_if_unique_id_configured(error="already_configured_install")
            except AbortFlow:
                await self._async_close_shell()
                raise
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
        """Ask the Companion to reboot the intercom."""
        client = CompanionApiClient(
            async_get_clientsession(self.hass), self._url, str(self._auth_data[CONF_ACCESS_TOKEN])
        )
        try:
            await client.async_reboot()
        except CompanionConnectionError:
            # An accepted reboot may cut the connection before the reply arrives.
            _LOGGER.debug("Connection dropped after the Companion reboot request")
        except CompanionApiError as err:
            _LOGGER.warning("Companion reboot request was refused: %s", err.code)
            return await self.async_step_reboot_failed()
        return await self._async_finish_install()

    async def async_step_reboot_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Tell the user to reboot the intercom by hand, then finish."""
        if user_input is not None:
            return await self._async_finish_install()
        return self.async_show_form(step_id="reboot_failed", data_schema=vol.Schema({}))

    async def async_step_reboot_later(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_finish_install()

    @callback
    def async_remove(self) -> None:
        """Stop following the installer and close SSH when the flow goes away."""
        self._removed = True
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
            return "ssh_cannot_connect"
        shell = IntercomShell(host, username, password, expected_host_key=expected_host_key)
        try:
            await shell.async_connect()
        except ShellAuthError:
            return "invalid_auth"
        except ShellHostKeyMismatch:
            return "host_key_changed"
        except ShellError as err:
            _LOGGER.warning("SSH connection to the intercom failed: %s", type(err).__name__)
            return "ssh_cannot_connect"
        await self._async_close_shell()
        self._shell, self._host, self._ssh_username = shell, host, username
        return None

    async def _async_install(self) -> None:
        if self._shell is None:
            raise InstallerFailed(("FAIL: No SSH session to the intercom.",))
        try:
            await self._shell.async_connect()
            await self._shell.async_start_installer()
            progress = await self._shell.async_wait_for_installer(lambda _fraction: None)
        except InstallerFailed:
            raise
        except ShellError as err:
            raise InstallerFailed((f"FAIL: SSH to the intercom failed: {err}",)) from err
        _LOGGER.info("Companion %s installed on %s", progress.installed_version or "(unknown version)", self._host)

    async def _async_pair_over_ssh(self) -> ConfigFlowResult:
        if self._shell is None:
            return await self._async_abort_install("ssh_cannot_connect")
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
        except ShellError as err:
            _LOGGER.warning("SSH session to the intercom failed while pairing: %s", type(err).__name__)
            return await self._async_abort_install("ssh_cannot_connect")

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
        # The installed Companion advertises over mDNS, so a zeroconf flow may hold this id.
        await self.async_set_unique_id(self._device_id, raise_on_progress=False)
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

    async def _async_step_for_pairing_state(self) -> ConfigFlowResult:
        if self._pairing_state == "claimable":
            return await self.async_step_claim()
        if self._pairing_state == "claimed":
            return await self.async_step_recover()
        if self._pairing_state == "setup_required":
            return self.async_abort(reason="setup_required")
        return self.async_abort(reason="pairing_error")

    async def _async_load_pairing_status(self, base_url: str) -> None:
        if not base_url:
            raise CompanionApiError("Companion URL is required")
        client = CompanionApiClient(async_get_clientsession(self.hass), base_url)
        status = await client.async_get_pairing_status()
        self._set_pairing_status(status, base_url)

    def _set_pairing_status(self, status: Mapping[str, Any], base_url: str) -> None:
        device_id = str(status.get(CONF_DEVICE_ID, "")).strip()
        instance_id = str(status.get(CONF_INSTANCE_ID, "")).strip()
        pairing_state = str(status.get("pairing_state", "")).strip()
        if not device_id or not instance_id or pairing_state not in _PAIRING_STATES:
            raise CompanionApiError("Companion returned an invalid pairing status")
        self._device_id = device_id
        self._url = base_url
        self._model = str(status.get("model", "")).strip()
        self._instance_id = instance_id
        self._pairing_state = pairing_state

    async def _async_authorize(self, code: str, *, recovery: bool) -> dict[str, Any]:
        code = code.strip()
        if not self._device_id or not self._url or not code:
            raise CompanionApiError("Companion authorization details are required")
        client = CompanionApiClient(
            async_get_clientsession(self.hass), self._url
        )
        if recovery:
            await client.async_recover_bearer(code)
        else:
            challenge = await client.async_pair_challenge()
            await client.async_pair_claim(
                challenge_id=str(challenge.get("challenge_id", "")), claim_code=code
            )
        return {
            CONF_DEVICE_ID: self._device_id,
            CONF_COMPANION_URL: self._url,
            CONF_ACCESS_TOKEN: client.access_token,
            CONF_INSTANCE_ID: self._instance_id,
        }

    async def _async_finish_authorization(self, data: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(self._device_id)
        if self._is_reauth:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        self._abort_if_unique_id_configured(updates={CONF_COMPANION_URL: self._url})
        return self.async_create_entry(title=self._model or self._device_id, data=data)


def _user_schema(user_input: Mapping[str, Any] | None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_COMPANION_URL, default=str(user_input.get(CONF_COMPANION_URL, ""))
            ): str,
        }
    )


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


def _owner_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_OWNER_USERNAME): str, vol.Required(CONF_OWNER_PASSWORD): str})


def _valid_owner(username: str, password: str) -> bool:
    """Mirror the Companion's owner rules so a mistake shows before anything is sent."""
    return (
        bool(username)
        and len(password) >= MIN_OWNER_PASSWORD_LENGTH
        and len(password.encode()) <= MAX_OWNER_PASSWORD_BYTES
        and username != DEFAULT_OWNER_CREDENTIAL
        and password != DEFAULT_OWNER_CREDENTIAL
    )


def _claim_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_CLAIM_CODE, default=""): str})


def _repair_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_REPAIR_CODE, default=""): str})


def _normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        try:
            ipaddress.IPv6Address(value)
        except ValueError:
            pass
        else:
            value = f"[{value}]"
        value = f"http://{value}"
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(segment in {".", ".."} for segment in unquote(path).split("/"))
    ):
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None:
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    return urlunsplit((parsed.scheme, f"{host}:{DEFAULT_PORT}", path, "", ""))


def _zeroconf_url(host: Any, port: Any) -> str | None:
    """Return a safe direct HTTP URL from Zeroconf service data."""
    if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool):
        return None
    if not 1 <= port <= 65535:
        return None

    host = host.strip()
    if host.endswith("."):
        host = host[:-1]
    if not host:
        return None

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME_RE.fullmatch(host):
            return None
        return f"http://{host.lower()}:{port}"

    if address.version == 6:
        return f"http://[{address.compressed}]:{port}"
    return f"http://{address.compressed}:{port}"


def _txt(properties: Mapping[Any, Any], key: str) -> str:
    value = properties.get(key, properties.get(key.encode()))
    if isinstance(value, bytes):
        return value.decode(errors="ignore").strip()
    return str(value).strip() if value is not None else ""
