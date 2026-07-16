"""
Pause/resume/cancel must control the torrent client, not just flip job
status — and the Library projection must show requested work immediately,
advancing through plain-language states until it is ready.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.agents.models import (AgentKind, AgentSession, Job, JobStatus,
                                   SessionStatus)
from backend.agents.service import AgentService
from backend.models import (Download, DownloadStatus, LibraryItem, MediaType,
                            SparrowConfig, TorrentClientConfig, TorrentClientType)
from backend.services.library_view import build_library_view
from backend.storage import Storage


class _FakeManager:
    def __init__(self):
        self.stopped: list[str] = []
        self.started: list[str] = []
        self.deleted: list[tuple[str, bool]] = []

    async def stop_torrent(self, torrent_hash: str) -> bool:
        self.stopped.append(torrent_hash)
        return True

    async def start_torrent(self, torrent_hash: str) -> bool:
        self.started.append(torrent_hash)
        return True

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        self.deleted.append((torrent_hash, delete_files))
        return True


class LifecycleBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.staging = self.root / "Temp"
        self.library = self.root / "Library"
        self.staging.mkdir()
        self.library.mkdir()
        self.storage = Storage(str(self.root / "state"))
        await self.storage.load_all()
        await self.storage.save_config(SparrowConfig(
            staging_dir=str(self.staging),
            library_dir=str(self.library),
            tmdb_api_key="fixture", anthropic_api_key="fixture",
            torrent_client=TorrentClientConfig(type=TorrentClientType.TRANSMISSION),
        ))
        self.broadcasts: list = []

        async def broadcast(event):
            self.broadcasts.append(event)

        self.service = AgentService(self.storage, str(self.root / "state"), broadcast)
        self.manager = _FakeManager()

        async def connected_manager():
            return self.manager

        self.service._connected_manager = connected_manager

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def _download(self, dl_id: str, job_id: str, status: DownloadStatus,
                        isolated: bool = True, **extra) -> Download:
        staging = self.staging / dl_id if isolated else self.staging
        staging.mkdir(exist_ok=True)
        dl = Download(id=dl_id, name=dl_id, magnet_url="magnet:?xt=x",
                      media_type=MediaType.TV, status=status,
                      torrent_hash=f"hash-{dl_id}", staging_path=str(staging),
                      metadata={"agent_managed": True, "job_id": job_id}, **extra)
        await self.storage.add_download(dl)
        return dl


class PauseResumeCancelTests(LifecycleBase):
    async def test_pause_stops_client_transfers_without_deleting(self) -> None:
        job = self.service.store.save_job(Job(tmdb_id=1, title="Fixture"))
        dl = await self._download("dl-active", job.id, DownloadStatus.DOWNLOADING)
        paused = await self.service.pause_job(job.id)
        self.assertEqual(paused.status, JobStatus.PAUSED)
        self.assertEqual(self.manager.stopped, [dl.torrent_hash])
        self.assertEqual(self.manager.deleted, [])
        self.assertEqual(self.storage.get_download(dl.id).status,
                         DownloadStatus.PAUSED)
        self.assertTrue(Path(dl.staging_path).exists())

    async def test_resume_restarts_paused_transfers(self) -> None:
        job = self.service.store.save_job(Job(tmdb_id=1, title="Fixture"))
        dl = await self._download("dl-paused", job.id, DownloadStatus.PAUSED)
        resumed = await self.service.resume_job(job.id)
        self.assertEqual(resumed.status, JobStatus.ACTIVE)
        self.assertEqual(self.manager.started, [dl.torrent_hash])
        self.assertEqual(self.storage.get_download(dl.id).status,
                         DownloadStatus.DOWNLOADING)

    async def test_cancel_removes_pending_work_but_keeps_organized(self) -> None:
        job = Job(tmdb_id=1, title="Fixture")
        session = AgentSession(agent=AgentKind.FETCH, job_id=job.id)
        job.session_id = session.id
        self.service.store.save_job(job)
        self.service.store.save_session(session)

        pending = await self._download("dl-pending", job.id, DownloadStatus.DOWNLOADING)
        (Path(pending.staging_path) / "partial.mkv").write_bytes(b"partial")
        organized = await self._download(
            "dl-organized", job.id, DownloadStatus.ORGANIZED,
            library_path=str(self.library / "done.mkv"))

        cancelled = await self.service.cancel_job(job.id)
        self.assertEqual(cancelled.status, JobStatus.ABANDONED)
        # The unfinished transfer and its partial files are gone…
        self.assertIn((pending.torrent_hash, True), self.manager.deleted)
        self.assertFalse(Path(pending.staging_path).exists())
        self.assertEqual(self.storage.get_download(pending.id).status,
                         DownloadStatus.ERROR)
        # …the finished, organized episode is untouched.
        self.assertNotIn((organized.torrent_hash, True), self.manager.deleted)
        self.assertEqual(self.storage.get_download(organized.id).status,
                         DownloadStatus.ORGANIZED)
        # And the owning session is closed with no stale wake state.
        refreshed = self.service.store.get_session(session.id)
        self.assertEqual(refreshed.status, SessionStatus.CLOSED)
        self.assertEqual(refreshed.wake_at, 0.0)

    async def test_cancel_never_touches_the_shared_staging_root(self) -> None:
        job = self.service.store.save_job(Job(tmdb_id=1, title="Fixture"))
        legacy = await self._download("dl-legacy", job.id,
                                      DownloadStatus.DOWNLOADING, isolated=False)
        (self.staging / "unrelated.mkv").write_bytes(b"someone else's file")
        await self.service.cancel_job(job.id)
        self.assertTrue(self.staging.exists())
        self.assertTrue((self.staging / "unrelated.mkv").exists())
        self.assertIn((legacy.torrent_hash, True), self.manager.deleted)


class LibraryProjectionTests(LifecycleBase):
    async def test_requested_episode_appears_immediately(self) -> None:
        job = Job(tmdb_id=7, title="Fresh Request", media_type="tv",
                  wanted_episodes={"1": [1, 2]})
        self.service.store.save_job(job)
        view = build_library_view(self.storage, self.service.store)
        entry = next(e for e in view if e["tmdb_id"] == 7)
        self.assertEqual(entry["state"], "requested")
        self.assertEqual(entry["pending_count"], 2)
        self.assertFalse(entry["in_library"])
        self.assertEqual(entry["episodes"]["1"]["1"]["state"], "requested")

    async def test_states_advance_with_transfers_and_inventory(self) -> None:
        job = Job(tmdb_id=7, title="Fixture", media_type="tv",
                  wanted_episodes={"1": [1, 2, 3]})
        self.service.store.save_job(job)
        await self._download("dl-x", job.id, DownloadStatus.DOWNLOADING,
                             progress=0.4, size_bytes=1000, download_speed=100,
                             eta_seconds=600, stats_updated_at=__import__("time").time())
        await self.storage.add_library_item(LibraryItem(
            id="tv-7", title="Fixture", media_type=MediaType.TV,
            path=str(self.library / "Fixture"), tmdb_id=7,
            episodes={"1": {"1": {"path": "x", "verified": True,
                                  "quality": "1080p"}}}))
        view = build_library_view(self.storage, self.service.store)
        entry = next(e for e in view if e["tmdb_id"] == 7)
        self.assertEqual(entry["episodes"]["1"]["1"]["state"], "ready")
        self.assertEqual(entry["episodes"]["1"]["2"]["state"], "downloading")
        self.assertEqual(entry["ready_count"], 1)
        self.assertEqual(entry["pending_count"], 2)
        self.assertEqual(entry["state"], "downloading")
        self.assertAlmostEqual(entry["transfers"]["progress"], 0.4)
        self.assertFalse(entry["transfers"]["stale"])

    async def test_landed_download_shows_verifying(self) -> None:
        job = Job(tmdb_id=7, title="Fixture", media_type="tv",
                  wanted_episodes={"1": [1]})
        self.service.store.save_job(job)
        await self._download("dl-x", job.id, DownloadStatus.COMPLETED, progress=1.0)
        view = build_library_view(self.storage, self.service.store)
        entry = next(e for e in view if e["tmdb_id"] == 7)
        self.assertEqual(entry["state"], "verifying")

    async def test_paused_job_needs_attention(self) -> None:
        job = Job(tmdb_id=7, title="Fixture", media_type="tv",
                  wanted_episodes={"1": [1]}, status=JobStatus.PAUSED)
        self.service.store.save_job(job)
        view = build_library_view(self.storage, self.service.store)
        entry = next(e for e in view if e["tmdb_id"] == 7)
        self.assertEqual(entry["state"], "paused")
        self.assertTrue(entry["needs_attention"])

    async def test_stale_transfer_stats_are_flagged(self) -> None:
        job = Job(tmdb_id=7, title="Fixture", media_type="tv",
                  wanted_episodes={"1": [1]})
        self.service.store.save_job(job)
        await self._download("dl-x", job.id, DownloadStatus.DOWNLOADING,
                             progress=0.2, stats_updated_at=1.0)  # long ago
        view = build_library_view(self.storage, self.service.store)
        entry = next(e for e in view if e["tmdb_id"] == 7)
        self.assertTrue(entry["transfers"]["stale"])


if __name__ == "__main__":
    unittest.main()
