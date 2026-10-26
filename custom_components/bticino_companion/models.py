"""Typed Companion API and WebSocket payload models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


def mapping_at(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return a nested mapping or an empty mapping."""
    value = payload.get(key)
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class Capability:
    """Features configured for an entrypoint."""

    stream: bool = False
    unlock: bool = False
    ring: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Capability":
        return cls(
            stream=bool(payload.get("stream", False)),
            unlock=bool(payload.get("unlock", False)),
            ring=bool(payload.get("ring", False)),
        )


@dataclass(frozen=True, slots=True)
class Availability:
    """Live availability for an entrypoint capability."""

    unlock: bool = False
    stream: bool = False
    snapshot: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Availability":
        return cls(
            unlock=bool(payload.get("unlock", False)),
            stream=bool(payload.get("stream", False)),
            snapshot=bool(payload.get("snapshot", False)),
        )


@dataclass(frozen=True, slots=True)
class Entrypoint:
    """A configured intercom entrypoint."""

    id: str
    label: str
    devaddr: str
    capabilities: Capability
    availability: Availability
    snapshot_updated_at: datetime | None = None
    ring_snapshot_updated_at: datetime | None = None
    ring_clip_recorded_at: datetime | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Entrypoint":
        return cls(
            id=str(payload.get("id", "")).strip(),
            label=str(payload.get("label", "")).strip(),
            devaddr=str(payload.get("devaddr", "")).strip(),
            capabilities=Capability.from_dict(mapping_at(payload, "capabilities")),
            availability=Availability.from_dict(mapping_at(payload, "availability")),
            snapshot_updated_at=_optional_datetime(payload.get("snapshot_updated_at")),
            ring_snapshot_updated_at=_optional_datetime(payload.get("ring_snapshot_updated_at")),
            ring_clip_recorded_at=_optional_datetime(payload.get("ring_clip_recorded_at")),
        )


@dataclass(frozen=True, slots=True)
class SystemService:
    """A Companion service optionally exposed to Home Assistant."""

    name: str
    enabled: bool = False
    exposed: bool = False


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    """Companion update state supplied by the v3 server."""

    enabled: bool = False
    exposed: bool = False
    installed_version: str | None = None
    latest_version: str | None = None
    staged_version: str | None = None
    restart_required: bool = False
    stage: str | None = None
    last_error: str | None = None
    in_progress: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    """Local integration transport status."""

    connected: bool = False
    reconnect_attempts: int = 0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """Cached device diagnostics pushed by Companion."""

    ip_address: str | None = None
    netmask: str | None = None
    mac_address: str | None = None
    firmware: str | None = None
    hardware: str | None = None
    kernel: str | None = None
    distribution: str | None = None
    wifi_strength: int | None = None
    refreshed_at: str | None = None
    refresh_error: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Diagnostics":
        openwebnet = mapping_at(payload, "openwebnet")
        local = mapping_at(payload, "local")
        wifi = local.get("wifi_strength")
        return cls(
            ip_address=_optional_string(openwebnet.get("ip")),
            netmask=_optional_string(openwebnet.get("netmask")),
            mac_address=_optional_string(openwebnet.get("mac")),
            firmware=_optional_string(openwebnet.get("firmware")),
            hardware=_optional_string(openwebnet.get("hardware")),
            kernel=_optional_string(openwebnet.get("kernel")),
            distribution=_optional_string(openwebnet.get("distribution")),
            wifi_strength=wifi if isinstance(wifi, int) else None,
            refreshed_at=_optional_string(payload.get("refreshed_at")),
            refresh_error=_optional_string(payload.get("refresh_error")),
        )


@dataclass(frozen=True, slots=True)
class TraceFrame:
    """An OpenWebNet frame multiplexed over the Companion WebSocket."""

    direction: str
    frame: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TraceFrame":
        return cls(
            direction=str(payload.get("direction", "")).strip(),
            frame=str(payload.get("frame", "")).strip(),
        )


@dataclass(frozen=True, slots=True)
class CompanionState:
    """Current Companion state projected by the server."""

    revision: int = 0
    call_state: str = "idle"
    active_entrypoint_id: str | None = None
    incoming_dialog_id: str | None = None
    active_dialog_id: str | None = None
    last_call_end_reason: str | None = None
    preview_stream_id: str | None = None
    entrypoints: tuple[Entrypoint, ...] = ()
    muted: bool = False
    voicemail_enabled: bool | None = None
    reboot_enabled: bool = False
    services: tuple[SystemService, ...] = ()
    update: UpdateInfo = UpdateInfo()
    model: str | None = None
    firmware: str | None = None
    hardware: str | None = None
    diagnostics: Diagnostics = Diagnostics()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CompanionState":
        physical_ring = mapping_at(payload, "physical_ring")
        incoming_call = mapping_at(payload, "incoming_call")
        active_call = mapping_at(payload, "active_call")
        last_call_end = mapping_at(payload, "last_incoming_call_end")
        preview_stream = mapping_at(payload, "preview_stream")
        entrypoint_id = next(
            (
                str(source.get("entrypoint_id", "")).strip()
                for source in (active_call, incoming_call, physical_ring, preview_stream)
                if str(source.get("entrypoint_id", "")).strip()
            ),
            None,
        )
        revision = payload.get("revision", 0)
        audio = mapping_at(payload, "audio")
        voicemail = mapping_at(payload, "voicemail")
        system = mapping_at(payload, "system")
        system_control = mapping_at(payload, "system_control")
        device = mapping_at(payload, "device")
        diagnostics = Diagnostics.from_dict(mapping_at(payload, "diagnostics"))
        update_payload = mapping_at(system_control, "update") or mapping_at(system, "update")
        services_payload = mapping_at(system_control, "services") or mapping_at(system, "services")
        entrypoints = _entrypoints(payload)
        return cls(
            revision=revision if isinstance(revision, int) else 0,
            call_state=str(payload.get("call_state", "idle")).strip().lower() or "idle",
            active_entrypoint_id=entrypoint_id,
            incoming_dialog_id=_optional_string(incoming_call.get("dialog_id")),
            active_dialog_id=_optional_string(active_call.get("dialog_id")),
            last_call_end_reason=_optional_string(last_call_end.get("reason")),
            preview_stream_id=_optional_string(preview_stream.get("stream_id")),
            entrypoints=entrypoints,
            muted=bool(audio.get("muted", payload.get("muted", False))),
            voicemail_enabled=_optional_bool(voicemail.get("enabled", payload.get("voicemail_enabled"))),
            reboot_enabled=bool(system_control.get("reboot_enabled", system.get("reboot_enabled", False))),
            services=_services(services_payload),
            update=UpdateInfo(
                enabled=bool(update_payload.get("enabled", system.get("update_enabled", False))),
                exposed=bool(update_payload.get("exposed", system.get("update_exposed", False))),
                installed_version=_optional_string(update_payload.get("current_version", device.get("firmware"))),
                latest_version=_optional_string(update_payload.get("latest_version")),
                staged_version=_optional_string(update_payload.get("staged_version")),
                restart_required=bool(update_payload.get("restart_required", False)),
                stage=_optional_string(update_payload.get("stage")),
                last_error=_optional_string(update_payload.get("error")),
                in_progress=str(update_payload.get("stage", "")).lower() in {"checking", "staging"},
            ),
            model=_optional_string(device.get("model", payload.get("model"))),
            firmware=diagnostics.firmware or _optional_string(device.get("firmware", payload.get("firmware"))),
            hardware=diagnostics.hardware or _optional_string(device.get("hardware", payload.get("hardware"))),
            diagnostics=diagnostics,
        )


def _optional_string(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None


def _optional_datetime(value: Any) -> datetime | None:
    """Parse an RFC 3339 time from the Companion, or return None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _optional_bool(value: Any) -> bool | None:
    """Return a boolean only when the server supplied one."""
    return value if isinstance(value, bool) else None


def _entrypoints(payload: Mapping[str, Any]) -> tuple[Entrypoint, ...]:
    raw = payload.get("entrypoints")
    if isinstance(raw, Mapping):
        raw = raw.get("entrypoints")
    if not isinstance(raw, list):
        return ()
    return tuple(entrypoint for item in raw if isinstance(item, Mapping) and (entrypoint := Entrypoint.from_dict(item)).id)


def _services(payload: Mapping[str, Any]) -> tuple[SystemService, ...]:
    return tuple(
        SystemService(name=str(name).strip(), enabled=bool(value.get("enabled")), exposed=bool(value.get("exposed")))
        for name, value in payload.items()
        if isinstance(value, Mapping) and str(name).strip()
    )
