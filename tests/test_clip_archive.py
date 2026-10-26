"""Tests for the doorbell ring clip archive."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion.api import CompanionConnectionError
    from bticino_companion.clip_archive import RingClipArchiver
    from bticino_companion.const import CONF_CLIP_RETENTION_DAYS, EVENT_RING_CLIP_SAVED
    from bticino_companion.models import CompanionState, Diagnostics, Entrypoint, UpdateInfo
    from homeassistant.util import dt as dt_util
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err

RECORDED = datetime(2026, 10, 26, 9, 30, 0, tzinfo=timezone.utc)
CLIP_NAME = dt_util.as_local(RECORDED).strftime("%Y-%m-%d_%H-%M-%S")


class _FakeHass:
    def __init__(self, media: Path | None) -> None:
        self.config = MagicMock()
        self.config.media_dirs = {"local": str(media)} if media else {}
        self.bus = MagicMock()
        self.tasks: list[asyncio.Task] = []

    async def async_add_executor_job(self, target, *args):
        return target(*args)

    def async_create_background_task(self, coro, name, eager_start=True):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    async def async_settle(self) -> None:
        while self.tasks:
            await self.tasks.pop(0)


def _state(**entrypoint) -> CompanionState:
    return CompanionState(
        revision=1,
        entrypoints=(Entrypoint.from_dict({"id": "main", "capabilities": {"stream": True}, **entrypoint}),),
        muted=False,
        voicemail_enabled=None,
        update=UpdateInfo(),
        diagnostics=Diagnostics(),
    )


class RingClipArchiverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.media = Path(self._tmp.name)
        self.folder = self.media / "bticino_go" / "device_1" / "main"
        self.hass = _FakeHass(self.media)
        self.client = MagicMock()
        self.client.async_entrypoint_clip_ring = AsyncMock(return_value=b"mp4")
        self.client.async_entrypoint_snapshot_ring = AsyncMock(return_value=b"jpg")
        self.coordinator = MagicMock()
        self.coordinator.data = None
        self.coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        # Keep every clip: RECORDED is a fixed date and would age out of a real retention window.
        self.entry = MagicMock(options={CONF_CLIP_RETENTION_DAYS: 0})
        self._interval = patch("bticino_companion.clip_archive.async_track_time_interval", return_value=lambda: None)
        self._interval.start()

    def tearDown(self) -> None:
        self._interval.stop()
        self._tmp.cleanup()

    def _archiver(self) -> RingClipArchiver:
        return RingClipArchiver(self.hass, self.entry, self.coordinator, self.client, "device_1")

    async def _ring(self, archiver: RingClipArchiver, **entrypoint) -> None:
        self.coordinator.data = _state(**entrypoint)
        archiver._async_state_changed()
        await self.hass.async_settle()

    async def test_archives_a_new_clip_with_its_poster(self) -> None:
        archiver = self._archiver()
        await archiver.async_start()

        await self._ring(
            archiver,
            ring_clip_recorded_at=RECORDED.isoformat(),
            ring_snapshot_updated_at=(RECORDED + timedelta(seconds=1)).isoformat(),
        )

        clip = self.folder / f"{CLIP_NAME}.mp4"
        poster = self.folder / f"{CLIP_NAME}.jpg"
        self.assertEqual(clip.read_bytes(), b"mp4")
        self.assertEqual(poster.read_bytes(), b"jpg")
        self.assertEqual(clip.stat().st_mtime, RECORDED.timestamp())
        self.assertEqual(archiver.latest("main"), RECORDED)
        self.hass.bus.async_fire.assert_called_once_with(
            EVENT_RING_CLIP_SAVED,
            {
                "entrypoint_id": "main",
                "recorded_at": RECORDED.isoformat(),
                "path": str(clip),
                "poster_path": str(poster),
                "media_content_id": f"media-source://media_source/local/bticino_go/device_1/main/{CLIP_NAME}.mp4",
            },
        )
        self.coordinator.async_update_listeners.assert_called()

    async def test_does_not_download_a_clip_twice(self) -> None:
        archiver = self._archiver()
        await archiver.async_start()

        for _ in range(3):
            await self._ring(archiver, ring_clip_recorded_at=RECORDED.isoformat())

        self.client.async_entrypoint_clip_ring.assert_awaited_once_with("main")

    async def test_retries_after_a_failed_download(self) -> None:
        self.client.async_entrypoint_clip_ring.side_effect = [CompanionConnectionError("down"), b"mp4"]
        archiver = self._archiver()
        await archiver.async_start()

        await self._ring(archiver, ring_clip_recorded_at=RECORDED.isoformat())
        await self._ring(archiver, ring_clip_recorded_at=RECORDED.isoformat())

        self.assertTrue((self.folder / f"{CLIP_NAME}.mp4").exists())

    async def test_skips_a_poster_from_another_ring(self) -> None:
        archiver = self._archiver()
        await archiver.async_start()

        await self._ring(
            archiver,
            ring_clip_recorded_at=RECORDED.isoformat(),
            ring_snapshot_updated_at=(RECORDED - timedelta(minutes=2)).isoformat(),
        )

        self.assertFalse((self.folder / f"{CLIP_NAME}.jpg").exists())
        self.client.async_entrypoint_snapshot_ring.assert_not_awaited()
        self.assertIsNone(self.hass.bus.async_fire.call_args.args[1]["poster_path"])

    async def test_finds_the_newest_archived_clip_at_start(self) -> None:
        self.folder.mkdir(parents=True)
        for name, recorded in (("a.mp4", RECORDED - timedelta(days=1)), ("b.mp4", RECORDED)):
            path = self.folder / name
            path.write_bytes(b"x")
            os.utime(path, (recorded.timestamp(), recorded.timestamp()))

        archiver = self._archiver()
        await archiver.async_start()

        self.assertEqual(archiver.latest("main"), RECORDED)

    async def test_purge_removes_only_old_files_inside_its_folder(self) -> None:
        self.entry.options = {CONF_CLIP_RETENTION_DAYS: 7}
        old = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
        self.folder.mkdir(parents=True)
        stale_folder = self.media / "bticino_go" / "device_1" / "gate"
        stale_folder.mkdir()
        outside = self.media / "holiday.mp4"
        files = {
            "old_clip": self.folder / "old.mp4",
            "old_poster": self.folder / "old.jpg",
            "new_clip": self.folder / "new.mp4",
            "stale": stale_folder / "old.mp4",
            "outside": outside,
        }
        for name, path in files.items():
            path.write_bytes(b"x")
            if name != "new_clip":
                os.utime(path, (old, old))

        await self._archiver().async_start()

        self.assertFalse(files["old_clip"].exists())
        self.assertFalse(files["old_poster"].exists())
        self.assertTrue(files["new_clip"].exists())
        self.assertFalse(stale_folder.exists())
        self.assertTrue(outside.exists())

    async def test_retention_zero_keeps_everything(self) -> None:
        self.entry.options = {CONF_CLIP_RETENTION_DAYS: 0}
        self.folder.mkdir(parents=True)
        path = self.folder / "old.mp4"
        path.write_bytes(b"x")
        os.utime(path, (0, 0))

        await self._archiver().async_start()

        self.assertTrue(path.exists())

    async def test_without_a_local_media_folder_it_stays_off(self) -> None:
        self.hass = _FakeHass(None)
        archiver = self._archiver()

        await archiver.async_start()

        self.assertFalse(archiver.enabled)
        self.coordinator.async_add_listener.assert_not_called()
        self.assertEqual(await archiver.async_list("main", 10), [])

    async def test_lists_the_newest_clips_first(self) -> None:
        self.folder.mkdir(parents=True)
        for index in range(3):
            recorded = RECORDED + timedelta(minutes=index)
            path = self.folder / f"2026-10-26_09-3{index}-00.mp4"
            path.write_bytes(b"x")
            os.utime(path, (recorded.timestamp(), recorded.timestamp()))
        (self.folder / "2026-10-26_09-32-00.jpg").write_bytes(b"x")
        (self.folder / ".tmp-partial.mp4").write_bytes(b"x")

        archiver = self._archiver()
        clips = await archiver.async_list("main", 2)

        self.assertEqual([clip.path.name for clip in clips], ["2026-10-26_09-32-00.mp4", "2026-10-26_09-31-00.mp4"])
        self.assertEqual(clips[0].recorded_at, RECORDED + timedelta(minutes=2))
        self.assertEqual(clips[0].poster.name, "2026-10-26_09-32-00.jpg")
        self.assertIsNone(clips[1].poster)
        self.assertEqual(
            archiver.url_path(clips[0].path), "/media/local/bticino_go/device_1/main/2026-10-26_09-32-00.mp4"
        )

    async def test_publishes_the_newest_clip_at_start(self) -> None:
        self.folder.mkdir(parents=True)
        path = self.folder / "a.mp4"
        path.write_bytes(b"x")
        os.utime(path, (RECORDED.timestamp(), RECORDED.timestamp()))

        await self._archiver().async_start()

        self.coordinator.async_update_listeners.assert_called_once_with()

    async def test_stays_quiet_at_start_without_clips(self) -> None:
        await self._archiver().async_start()

        self.coordinator.async_update_listeners.assert_not_called()

    async def test_purge_refreshes_the_newest_clip(self) -> None:
        self.entry.options = {CONF_CLIP_RETENTION_DAYS: 7}
        self.folder.mkdir(parents=True)
        path = self.folder / "a.mp4"
        path.write_bytes(b"x")
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
        os.utime(path, (recent, recent))
        archiver = self._archiver()
        await archiver.async_start()
        self.assertIsNotNone(archiver.latest("main"))
        self.coordinator.async_update_listeners.reset_mock()

        expired = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
        os.utime(path, (expired, expired))
        await archiver._async_purge()

        self.assertFalse(path.exists())
        self.assertIsNone(archiver.latest("main"))
        self.coordinator.async_update_listeners.assert_called_once_with()

    async def test_filesystem_errors_do_not_escape(self) -> None:
        self.entry.options = {CONF_CLIP_RETENTION_DAYS: 7}
        archiver = self._archiver()
        with patch("bticino_companion.clip_archive._scan_latest", side_effect=OSError("denied")), patch(
            "bticino_companion.clip_archive._purge", side_effect=OSError("denied")
        ):
            await archiver.async_start()
            await archiver._async_purge_interval(datetime.now(timezone.utc))
        with patch("bticino_companion.clip_archive._list_clips", side_effect=OSError("denied")):
            self.assertEqual(await archiver.async_list("main", 10), [])

        self.assertIsNone(archiver.latest("main"))
        self.coordinator.async_add_listener.assert_called_once()

    async def test_skips_files_that_cannot_be_read(self) -> None:
        self.folder.mkdir(parents=True)
        good = self.folder / "good.mp4"
        good.write_bytes(b"x")
        os.utime(good, (RECORDED.timestamp(), RECORDED.timestamp()))
        (self.folder / "gone.mp4").write_bytes(b"x")
        real_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path.name == "gone.mp4":
                raise FileNotFoundError(path)
            return real_stat(path, *args, **kwargs)

        archiver = self._archiver()
        with patch.object(Path, "stat", stat):
            await archiver.async_start()
            clips = await archiver.async_list("main", 10)

        self.assertEqual(archiver.latest("main"), RECORDED)
        self.assertEqual([clip.path.name for clip in clips], ["good.mp4"])

    async def test_lists_by_recorded_time_not_by_name(self) -> None:
        self.folder.mkdir(parents=True)
        for name, recorded in (("b.mp4", RECORDED), ("a.mp4", RECORDED + timedelta(hours=1))):
            path = self.folder / name
            path.write_bytes(b"x")
            os.utime(path, (recorded.timestamp(), recorded.timestamp()))

        clips = await self._archiver().async_list("main", 10)

        self.assertEqual([clip.path.name for clip in clips], ["a.mp4", "b.mp4"])

    async def test_an_empty_device_id_uses_a_fallback_folder(self) -> None:
        archiver = RingClipArchiver(self.hass, self.entry, self.coordinator, self.client, "!!!")
        await archiver.async_start()

        await self._ring(archiver, ring_clip_recorded_at=RECORDED.isoformat())

        self.assertTrue((self.media / "bticino_go" / "companion" / "main" / f"{CLIP_NAME}.mp4").exists())

    async def test_an_entrypoint_without_a_folder_name_is_skipped(self) -> None:
        archiver = self._archiver()
        await archiver.async_start()
        self.coordinator.data = CompanionState(
            revision=1,
            entrypoints=(Entrypoint.from_dict({"id": "!!!", "capabilities": {"stream": True}, "ring_clip_recorded_at": RECORDED.isoformat()}),),
            muted=False,
            voicemail_enabled=None,
            update=UpdateInfo(),
            diagnostics=Diagnostics(),
        )

        archiver._async_state_changed()
        await self.hass.async_settle()

        self.client.async_entrypoint_clip_ring.assert_not_awaited()
        self.assertEqual(await archiver.async_list("!!!", 10), [])

    async def test_logs_a_repeated_download_failure_once(self) -> None:
        self.client.async_entrypoint_clip_ring.side_effect = CompanionConnectionError("down")
        archiver = self._archiver()
        await archiver.async_start()

        with self.assertLogs("bticino_companion.clip_archive", level="DEBUG") as logs:
            for _ in range(3):
                await self._ring(archiver, ring_clip_recorded_at=RECORDED.isoformat())

        levels = [record.levelname for record in logs.records if "download the ring clip" in record.getMessage()]
        self.assertEqual(levels, ["WARNING", "DEBUG", "DEBUG"])

    async def test_a_save_during_the_purge_scan_is_not_lost(self) -> None:
        self.entry.options = {CONF_CLIP_RETENTION_DAYS: 7}
        archiver = self._archiver()
        await archiver.async_start()
        self.coordinator.async_update_listeners.reset_mock()

        def scan(root):
            archiver._latest["main"] = RECORDED
            return {}

        with patch("bticino_companion.clip_archive._scan_latest", side_effect=scan):
            await archiver._async_purge()

        self.assertEqual(archiver.latest("main"), RECORDED)
        # The save itself already notified; the purge adds nothing new.
        self.coordinator.async_update_listeners.assert_not_called()

    async def test_skips_a_file_with_an_absurd_modification_time(self) -> None:
        self.folder.mkdir(parents=True)
        good = self.folder / "good.mp4"
        good.write_bytes(b"x")
        os.utime(good, (RECORDED.timestamp(), RECORDED.timestamp()))
        (self.folder / "huge.mp4").write_bytes(b"x")
        real_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path.name == "huge.mp4":
                return MagicMock(st_mtime=1e20)
            return real_stat(path, *args, **kwargs)

        archiver = self._archiver()
        with patch.object(Path, "stat", stat):
            await archiver.async_start()
            clips = await archiver.async_list("main", 10)

        self.assertEqual(archiver.latest("main"), RECORDED)
        self.assertEqual([clip.path.name for clip in clips], ["good.mp4"])

    async def test_a_failed_utime_does_not_abort_the_archive(self) -> None:
        archiver = self._archiver()
        await archiver.async_start()

        with patch("bticino_companion.clip_archive.os.utime", side_effect=OSError("denied")):
            await self._ring(archiver, ring_clip_recorded_at=RECORDED.isoformat())

        self.assertEqual((self.folder / f"{CLIP_NAME}.mp4").read_bytes(), b"mp4")
        self.assertEqual(archiver.latest("main"), RECORDED)
        self.hass.bus.async_fire.assert_called_once()
