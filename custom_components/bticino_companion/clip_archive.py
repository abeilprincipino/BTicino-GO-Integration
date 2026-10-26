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
        self._root = self._media_root / CLIP_ARCHIVE_FOLDER / slugify(device_id) if self._media_root else None
        # The clip time each entrypoint was last found archived, so a state push
        # that changes nothing costs nothing.
        self._checked: dict[str, datetime] = {}
        self._in_flight: set[tuple[str, datetime]] = set()
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
        self._latest = await self._hass.async_add_executor_job(_scan_latest, self._root)
        await self._async_purge()
        self._unsubscribers.append(self._coordinator.async_add_listener(self._async_state_changed))
        self._unsubscribers.append(
            async_track_time_interval(
                self._hass, self._async_purge_interval, timedelta(seconds=CLIP_PURGE_INTERVAL_SECONDS)
            )
        )
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
        if self._root is None:
            return []
        return await self._hass.async_add_executor_job(_list_clips, self._folder(entrypoint_id), limit)

    def url_path(self, path: Path) -> str:
        """The path Home Assistant serves an archived file at, before signing."""
        return "/media/local/" + quote(path.relative_to(self._media_root).as_posix())

    def _folder(self, entrypoint_id: str) -> Path:
        return self._root / slugify(entrypoint_id)

    @callback
    def _async_state_changed(self) -> None:
        state = self._coordinator.data
        if self._root is None or not state:
            return
        for entrypoint in state.entrypoints:
            recorded = entrypoint.ring_clip_recorded_at
            key = (entrypoint.id, recorded)
            if recorded is None or self._checked.get(entrypoint.id) == recorded or key in self._in_flight:
                continue
            self._in_flight.add(key)
            task = self._hass.async_create_background_task(
                self._async_archive(entrypoint, recorded), name=f"bticino_companion archive ring clip {entrypoint.id}"
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _async_archive(self, entrypoint: Entrypoint, recorded: datetime) -> None:
        try:
            clip_path = self._folder(entrypoint.id) / f"{dt_util.as_local(recorded).strftime(NAME_FORMAT)}{CLIP_SUFFIX}"
            if await self._hass.async_add_executor_job(clip_path.exists):
                self._checked[entrypoint.id] = recorded
                return
            try:
                clip = await self._client.async_entrypoint_clip_ring(entrypoint.id)
            except CompanionApiError as err:
                # Still missing, so the next state push tries again.
                _LOGGER.warning("Unable to download the ring clip of %s: %s", entrypoint.id, err)
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
            folder = slugify(entrypoint.id)
            if recorded > self._latest.get(folder, datetime.min.replace(tzinfo=timezone.utc)):
                self._latest[folder] = recorded
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

    async def _async_purge(self) -> None:
        if self._root is None or self._retention_days <= 0:
            return
        cutoff = dt_util.utcnow() - timedelta(days=self._retention_days)
        await self._hass.async_add_executor_job(_purge, self._root, cutoff.timestamp())

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
    os.utime(path, (recorded.timestamp(), recorded.timestamp()))


def _clips_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [path for path in folder.iterdir() if path.suffix == CLIP_SUFFIX and not path.name.startswith(".")]


def _scan_latest(root: Path) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    if not root.is_dir():
        return latest
    for folder in root.iterdir():
        clips = _clips_in(folder)
        if clips:
            newest = max(path.stat().st_mtime for path in clips)
            latest[folder.name] = datetime.fromtimestamp(newest, timezone.utc)
    return latest


def _list_clips(folder: Path, limit: int) -> list[ArchivedClip]:
    clips = sorted(_clips_in(folder), key=lambda path: path.name, reverse=True)[:limit]
    result = []
    for path in clips:
        poster = path.with_suffix(POSTER_SUFFIX)
        result.append(
            ArchivedClip(
                recorded_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                path=path,
                poster=poster if poster.exists() else None,
            )
        )
    return result


def _purge(root: Path, cutoff: float) -> None:
    if not root.is_dir():
        return
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.suffix in (CLIP_SUFFIX, POSTER_SUFFIX) and path.stat().st_mtime < cutoff:
                with contextlib.suppress(OSError):
                    path.unlink()
        with contextlib.suppress(OSError):
            if not any(folder.iterdir()):
                folder.rmdir()
