"""Archive of the doorbell ring clips in Home Assistant's media folder.

The Companion keeps one clip per entrypoint; this archive keeps the history.
See docs/superpowers/specs/2026-10-26-ring-clip-design.md in the Companion
repository.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import tempfile
from urllib.parse import quote

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util, slugify

from .api import CompanionApiError
from .const import (
    CLIP_ARCHIVE_FOLDER,
    CLIP_POSTER_WINDOW_SECONDS,
    CLIP_PURGE_INTERVAL_SECONDS,
    CONF_CLIP_RETENTION_DAYS,
    DEFAULT_CLIP_RETENTION_DAYS,
    EVENT_RING_CLIP_SAVED,
)
from .models import Entrypoint

_LOGGER = logging.getLogger(__name__)

CLIP_SUFFIX = ".mp4"
POSTER_SUFFIX = ".jpg"
NAME_FORMAT = "%Y-%m-%d_%H-%M-%S"
FALLBACK_DEVICE_FOLDER = "companion"
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class ArchivedClip:
    """One clip in the archive."""

    recorded_at: datetime
    path: Path
    poster: Path | None


class RingClipArchiver:
    """Download every new ring clip of one Companion and purge old ones."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator, client, device_id: str) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._client = client
        self._retention_days = int(entry.options.get(CONF_CLIP_RETENTION_DAYS, DEFAULT_CLIP_RETENTION_DAYS))
        media_root = hass.config.media_dirs.get("local")
        self._media_root = Path(media_root) if media_root else None
        device_folder = slugify(device_id) or FALLBACK_DEVICE_FOLDER
        self._root = self._media_root / CLIP_ARCHIVE_FOLDER / device_folder if self._media_root else None
        # The clip time each entrypoint was last found archived, so a state push
        # that changes nothing costs nothing.
        self._checked: dict[str, datetime] = {}
        self._in_flight: set[tuple[str, datetime]] = set()
        # The clip time of the last failed download that was logged as a warning;
        # retries of the same clip log at debug level.
        self._warned: dict[str, datetime] = {}
        self._latest: dict[str, datetime] = {}
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        """Whether a local media folder exists to archive into."""
        return self._root is not None

    async def async_start(self) -> None:
        """Purge, then follow the Companion state for new clips."""
        if self._root is None:
            _LOGGER.warning("No 'local' media folder is configured; doorbell ring clips are not archived")
            return
        try:
            self._latest = await self._hass.async_add_executor_job(_scan_latest, self._root)
        except OSError as err:
            _LOGGER.warning("Unable to read the ring clip archive: %s", err)
            self._latest = {}
        await self._async_purge(notify=False)
        self._unsubscribers.append(self._coordinator.async_add_listener(self._async_state_changed))
        self._unsubscribers.append(
            async_track_time_interval(
                self._hass, self._async_purge_interval, timedelta(seconds=CLIP_PURGE_INTERVAL_SECONDS)
            )
        )
        if self._latest:
            # Let the cameras publish the newest clip time right after a restart.
            self._coordinator.async_update_listeners()
        self._async_state_changed()

    async def async_stop(self) -> None:
        """Stop following the state and cancel downloads in flight."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        for task in tuple(self._tasks):
            task.cancel()

    def latest(self, entrypoint_id: str) -> datetime | None:
        """The time of the newest archived clip of an entrypoint."""
        return self._latest.get(slugify(entrypoint_id))

    async def async_list(self, entrypoint_id: str, limit: int) -> list[ArchivedClip]:
        """The newest clips of an entrypoint, newest first."""
        folder = self._folder(entrypoint_id)
        if folder is None:
            return []
        try:
            return await self._hass.async_add_executor_job(_list_clips, folder, limit)
        except OSError as err:
            _LOGGER.debug("Unable to list the ring clips of %s: %s", entrypoint_id, err)
            return []

    def url_path(self, path: Path) -> str:
        """The path Home Assistant serves an archived file at, before signing."""
        return "/media/local/" + quote(path.relative_to(self._media_root).as_posix())

    def _folder(self, entrypoint_id: str) -> Path | None:
        """The folder of an entrypoint, never the device folder itself."""
        if self._root is None:
            return None
        name = slugify(entrypoint_id)
        return self._root / name if name else None

    @callback
    def _async_state_changed(self) -> None:
        state = self._coordinator.data
        if self._root is None or not state:
            return
        for entrypoint in state.entrypoints:
            recorded = entrypoint.ring_clip_recorded_at
            if recorded is None:
                continue
            if self._folder(entrypoint.id) is None:
                _LOGGER.debug("Entrypoint id %r has no usable folder name; its ring clips are not archived", entrypoint.id)
                continue
            key = (entrypoint.id, recorded)
            if self._checked.get(entrypoint.id) == recorded or key in self._in_flight:
                continue
            self._in_flight.add(key)
            task = self._hass.async_create_background_task(
                self._async_archive(entrypoint, recorded), name=f"bticino_companion archive ring clip {entrypoint.id}"
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _async_archive(self, entrypoint: Entrypoint, recorded: datetime) -> None:
        try:
            folder = self._folder(entrypoint.id)
            if folder is None:
                return
            clip_path = folder / f"{dt_util.as_local(recorded).strftime(NAME_FORMAT)}{CLIP_SUFFIX}"
            try:
                exists = await self._hass.async_add_executor_job(clip_path.exists)
            except OSError as err:
                _LOGGER.warning("Unable to check the ring clip archive of %s: %s", entrypoint.id, err)
                return
            if exists:
                self._checked[entrypoint.id] = recorded
                return
            try:
                clip = await self._client.async_entrypoint_clip_ring(entrypoint.id)
            except CompanionApiError as err:
                # Still missing, so the next state push tries again. Say so once
                # per clip: a bad token would otherwise log on every push.
                level = logging.DEBUG if self._warned.get(entrypoint.id) == recorded else logging.WARNING
                self._warned[entrypoint.id] = recorded
                _LOGGER.log(level, "Unable to download the ring clip of %s: %s", entrypoint.id, err)
                return
            if not clip:
                return
            poster = await self._async_poster(entrypoint, recorded)
            poster_path = clip_path.with_suffix(POSTER_SUFFIX) if poster else None
            try:
                await self._hass.async_add_executor_job(_write_atomic, clip_path, clip, recorded)
                if poster and poster_path:
                    await self._hass.async_add_executor_job(_write_atomic, poster_path, poster, recorded)
            except OSError as err:
                _LOGGER.warning("Unable to archive the ring clip of %s: %s", entrypoint.id, err)
                return
            self._checked[entrypoint.id] = recorded
            self._warned.pop(entrypoint.id, None)
            name = folder.name
            if recorded > self._latest.get(name, _EPOCH):
                self._latest[name] = recorded
            self._hass.bus.async_fire(
                EVENT_RING_CLIP_SAVED,
                {
                    "entrypoint_id": entrypoint.id,
                    "recorded_at": recorded.isoformat(),
                    "path": str(clip_path),
                    "poster_path": str(poster_path) if poster_path else None,
                    "media_content_id": "media-source://media_source/local/"
                    + clip_path.relative_to(self._media_root).as_posix(),
                },
            )
            self._coordinator.async_update_listeners()
            await self._async_purge()
        finally:
            self._in_flight.discard((entrypoint.id, recorded))

    async def _async_poster(self, entrypoint: Entrypoint, recorded: datetime) -> bytes | None:
        # The ring picture comes from the same stream, a moment after the clip
        # starts; a picture further away belongs to another ring.
        snapshot_at = entrypoint.ring_snapshot_updated_at
        if snapshot_at is None or abs((snapshot_at - recorded).total_seconds()) > CLIP_POSTER_WINDOW_SECONDS:
            return None
        try:
            return await self._client.async_entrypoint_snapshot_ring(entrypoint.id)
        except CompanionApiError as err:
            _LOGGER.debug("No poster for the ring clip of %s: %s", entrypoint.id, err)
            return None

    async def _async_purge(self, notify: bool = True) -> None:
        """Delete expired files, then refresh the newest clip times."""
        if self._root is None or self._retention_days <= 0:
            return
        cutoff = dt_util.utcnow() - timedelta(days=self._retention_days)
        before = dict(self._latest)
        try:
            await self._hass.async_add_executor_job(_purge, self._root, cutoff.timestamp())
            latest = await self._hass.async_add_executor_job(_scan_latest, self._root)
        except OSError as err:
            _LOGGER.warning("Unable to purge the ring clip archive: %s", err)
            return
        # A clip saved while the scan ran may be missing from it: keep the newer
        # time for any entrypoint whose time moved meanwhile. The others follow
        # the scan, so an expired clip stops being advertised.
        merged = dict(latest)
        for key, recorded in self._latest.items():
            if before.get(key) != recorded and recorded > merged.get(key, _EPOCH):
                merged[key] = recorded
        if merged != self._latest:
            self._latest = merged
            if notify:
                self._coordinator.async_update_listeners()

    async def _async_purge_interval(self, _now: datetime) -> None:
        await self._async_purge()


def _write_atomic(path: Path, data: bytes, recorded: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=path.suffix, dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    try:
        os.utime(path, (recorded.timestamp(), recorded.timestamp()))
    except (OSError, OverflowError, ValueError) as err:
        # The file is complete; only its modification time is off.
        _LOGGER.debug("Unable to set the modification time of %s: %s", path, err)


def _entries(folder: Path) -> list[Path]:
    """The entries of a folder, or none when it is missing or unreadable."""
    try:
        return list(folder.iterdir())
    except OSError:
        return []


def _mtime(path: Path) -> float | None:
    """The modification time of a file, or None when it vanished, cannot be read or is absurd."""
    try:
        modified = path.stat().st_mtime
        datetime.fromtimestamp(modified, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    return modified


def _clips_in(folder: Path) -> list[tuple[float, Path]]:
    """The clips of a folder with their modification time, skipping unreadable ones."""
    clips = []
    for path in _entries(folder):
        if path.suffix != CLIP_SUFFIX or path.name.startswith("."):
            continue
        modified = _mtime(path)
        if modified is not None:
            clips.append((modified, path))
    return clips


def _scan_latest(root: Path) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for folder in _entries(root):
        clips = _clips_in(folder)
        if clips:
            latest[folder.name] = datetime.fromtimestamp(max(modified for modified, _ in clips), timezone.utc)
    return latest


def _list_clips(folder: Path, limit: int) -> list[ArchivedClip]:
    # Newest first by recorded time (the modification time); the name only breaks ties.
    clips = sorted(_clips_in(folder), key=lambda item: (item[0], item[1].name), reverse=True)[:limit]
    result = []
    for modified, path in clips:
        poster = path.with_suffix(POSTER_SUFFIX)
        result.append(
            ArchivedClip(
                recorded_at=datetime.fromtimestamp(modified, timezone.utc),
                path=path,
                poster=poster if _mtime(poster) is not None else None,
            )
        )
    return result


def _purge(root: Path, cutoff: float) -> None:
    for folder in _entries(root):
        for path in _entries(folder):
            if path.suffix not in (CLIP_SUFFIX, POSTER_SUFFIX):
                continue
            modified = _mtime(path)
            if modified is not None and modified < cutoff:
                with contextlib.suppress(OSError):
                    path.unlink()
        with contextlib.suppress(OSError):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
