"""
Deterministic authority tests: the tool layer must make forbidden
acquisitions impossible regardless of what any model concludes.

The centrepiece is a sanitized replay of the real alpha incident: the user
requested season 1 only, monitoring off, season 1 landed on disk — and the
standing Librarian then decided to "backfill" seasons 2–4. With mandates in
place, that spawn must be refused at the tool layer.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from backend.agents.models import (AgentKind, AgentSession, Job, JobStatus,
                                   Mandate, MonitoringMode)
from backend.agents.runtime import ToolCtx, ToolError
from backend.agents.store import AgentStore
from backend.agents.tools import Toolbox, fetch_tools, librarian_tools, media_tools
from backend.models import (Download, DownloadStatus, LibraryItem, MediaType,
                            SparrowConfig, TorrentClientConfig, TorrentClientType)
from backend.storage import Storage


class _Runtime:
    pass


class _FakeTorrentManager:
    def __init__(self):
        self.stopped: list[str] = []
        self.started: list[str] = []
        self.deleted: list[tuple[str, bool]] = []

    async def add_magnet(self, magnet: str, destination: str) -> str:
        self.last_destination = destination
        return "a" * 40

    async def stop_torrent(self, torrent_hash: str) -> bool:
        self.stopped.append(torrent_hash)
        return True

    async def start_torrent(self, torrent_hash: str) -> bool:
        self.started.append(torrent_hash)
        return True

    async def delete_torrent(self, torrent_hash: str, delete_files: bool = False) -> bool:
        self.deleted.append((torrent_hash, delete_files))
        return True


class ScopeGuardBase(unittest.IsolatedAsyncioTestCase):
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
            tmdb_api_key="fixture",
            anthropic_api_key="fixture",
            max_active_transfers=2,
            torrent_client=TorrentClientConfig(type=TorrentClientType.TRANSMISSION),
        ))
        self.store = AgentStore(str(self.root / "state"))
        self.events: list = []
        self.broadcasts: list = []

        async def emit(event):
            self.events.append(event)

        async def broadcast(event):
            self.broadcasts.append(event)

        self.toolbox = Toolbox(self.storage, self.store, emit, broadcast)
        self.tmdb_calls: list[str] = []
        self.tmdb_fixtures: dict = {}

        async def tmdb_get(path: str, **_):
            self.tmdb_calls.append(path)
            if path not in self.tmdb_fixtures:
                raise AssertionError(f"unexpected TMDB call: {path}")
            return self.tmdb_fixtures[path]

        self.toolbox.tmdb_get = tmdb_get

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _tool(tools, name):
        return next(tool for tool in tools if tool.name == name)

    def _librarian_spawn(self, created: list):
        async def create_job(tmdb_id, wanted_episodes, origin="librarian", **knobs):
            # Mirrors AgentService.create_job for agent origins: enforcement
            # happens before any job exists.
            await self.toolbox.enforce_mandate(tmdb_id, "tv", wanted_episodes, origin)
            job = Job(tmdb_id=tmdb_id, media_type="tv", title="Fixture Show",
                      wanted_episodes=wanted_episodes, origin=origin)
            self.store.save_job(job)
            created.append(job)
            return job
        tools = librarian_tools(self.toolbox, create_job)
        session = AgentSession(agent=AgentKind.LIBRARIAN)
        return self._tool(tools, "spawn_job"), ToolCtx(session=session, runtime=_Runtime())


class MandateScopeTests(ScopeGuardBase):
    async def test_no_mandate_means_no_acquisition_at_all(self) -> None:
        spawn, ctx = self._librarian_spawn(created := [])
        with self.assertRaisesRegex(ToolError, "never requested this title"):
            await spawn.handler(ctx, {"tmdb_id": 500, "wanted_episodes": {"1": [1]}})
        self.assertEqual(created, [])
        self.assertEqual(self.store.get_jobs(), [])

    async def test_owned_but_unrequested_show_is_off_limits(self) -> None:
        # Files on disk (e.g. a scanned folder) are not a mandate.
        await self.storage.add_library_item(LibraryItem(
            id="tv-500", title="Scanned Show", media_type=MediaType.TV,
            path=str(self.library / "Scanned Show"), tmdb_id=500,
            episodes={"1": {"1": {"path": "x", "verified": True}}},
        ))
        spawn, ctx = self._librarian_spawn(created := [])
        with self.assertRaisesRegex(ToolError, "never requested"):
            await spawn.handler(ctx, {"tmdb_id": 500, "wanted_episodes": {"1": [2]}})
        self.assertEqual(created, [])

    async def test_season_one_incident_replay_is_refused(self) -> None:
        """Sanitized replay of the real multi-season incident.

        Season 1 requested and owned, monitoring off. The Librarian tries to
        backfill seasons 2–4 because the show 'is in the library'. The tool
        must refuse, and no job may exist afterwards.
        """
        mandate = Mandate(tmdb_id=1396, media_type="tv",
                          mode=MonitoringMode.EXACT,
                          requested_episodes={"1": [1, 2, 3, 4, 5, 6, 7, 8]})
        self.store.save_mandate(mandate)
        await self.storage.add_library_item(LibraryItem(
            id="tv-1396", title="Fixture Show", media_type=MediaType.TV,
            path=str(self.library / "Fixture Show"), tmdb_id=1396,
            episodes={"1": {str(e): {"path": "x", "verified": True}
                            for e in range(1, 9)}},
        ))
        spawn, ctx = self._librarian_spawn(created := [])
        with self.assertRaisesRegex(ToolError, "exceed the user's mandate"):
            await spawn.handler(ctx, {
                "tmdb_id": 1396,
                "wanted_episodes": {"2": list(range(1, 13)),
                                    "3": list(range(1, 14)),
                                    "4": list(range(1, 14))},
            })
        self.assertEqual(created, [])
        self.assertEqual(self.store.get_jobs(), [])

    async def test_exact_mandate_still_allows_redownload_of_requested(self) -> None:
        mandate = Mandate(tmdb_id=1396, media_type="tv",
                          mode=MonitoringMode.EXACT,
                          requested_episodes={"1": [1, 2]})
        self.store.save_mandate(mandate)
        spawn, ctx = self._librarian_spawn(created := [])
        result = await spawn.handler(ctx, {
            "tmdb_id": 1396, "wanted_episodes": {"1": [2]}, "origin": "upgrade"})
        self.assertEqual(len(created), 1)
        self.assertIn("job_id", result)

    async def test_selected_seasons_mandate_bounds_acquisition(self) -> None:
        mandate = Mandate(tmdb_id=1396, media_type="tv",
                          mode=MonitoringMode.SEASONS,
                          requested_episodes={"1": [1]}, seasons=[1, 3])
        self.store.save_mandate(mandate)
        spawn, ctx = self._librarian_spawn(created := [])
        await spawn.handler(ctx, {"tmdb_id": 1396, "wanted_episodes": {"3": [1, 2]}})
        self.assertEqual(len(created), 1)
        created[0].status = JobStatus.COMPLETE
        self.store.save_job(created[0])
        with self.assertRaisesRegex(ToolError, "S02E01"):
            await spawn.handler(ctx, {"tmdb_id": 1396, "wanted_episodes": {"2": [1]}})

    async def test_keep_current_allows_new_airings_only(self) -> None:
        grant = time.time() - 3600
        mandate = Mandate(tmdb_id=1396, media_type="tv",
                          mode=MonitoringMode.KEEP_CURRENT,
                          requested_episodes={"4": [1, 2, 3]}, granted_at=grant)
        self.store.save_mandate(mandate)
        new_day = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
        self.tmdb_fixtures["/tv/1396/season/4"] = {"episodes": [
            {"episode_number": 4, "air_date": new_day},
        ]}
        self.tmdb_fixtures["/tv/1396/season/2"] = {"episodes": [
            {"episode_number": 1, "air_date": "2020-01-05"},
        ]}
        spawn, ctx = self._librarian_spawn(created := [])
        await spawn.handler(ctx, {"tmdb_id": 1396, "wanted_episodes": {"4": [4]}})
        self.assertEqual(len(created), 1)
        created[0].status = JobStatus.COMPLETE
        self.store.save_job(created[0])
        # Historical episodes did not become fair game.
        with self.assertRaisesRegex(ToolError, "S02E01"):
            await spawn.handler(ctx, {"tmdb_id": 1396, "wanted_episodes": {"2": [1]}})

    async def test_backfill_mandate_allows_everything(self) -> None:
        mandate = Mandate(tmdb_id=1396, media_type="tv",
                          mode=MonitoringMode.BACKFILL,
                          requested_episodes={"1": [1]})
        self.store.save_mandate(mandate)
        spawn, ctx = self._librarian_spawn(created := [])
        await spawn.handler(ctx, {
            "tmdb_id": 1396,
            "wanted_episodes": {"2": list(range(1, 13))}})
        self.assertEqual(len(created), 1)

    async def test_duplicate_active_job_is_refused_before_scope(self) -> None:
        self.store.save_mandate(Mandate(tmdb_id=1396, media_type="tv",
                                        mode=MonitoringMode.BACKFILL,
                                        requested_episodes={"1": [1]}))
        self.store.save_job(Job(tmdb_id=1396, media_type="tv",
                                title="Fixture Show", status=JobStatus.ACTIVE))
        spawn, ctx = self._librarian_spawn(created := [])
        with self.assertRaisesRegex(ToolError, "already an active job"):
            await spawn.handler(ctx, {"tmdb_id": 1396, "wanted_episodes": {"1": [1]}})
        self.assertEqual(created, [])

    async def test_blanket_spawn_without_episodes_is_refused(self) -> None:
        self.store.save_mandate(Mandate(tmdb_id=1396, media_type="tv",
                                        mode=MonitoringMode.BACKFILL,
                                        requested_episodes={"1": [1]}))
        spawn, ctx = self._librarian_spawn(created := [])
        with self.assertRaisesRegex(ToolError, "exact episodes"):
            await spawn.handler(ctx, {"tmdb_id": 1396, "wanted_episodes": {}})
        self.assertEqual(created, [])


class ConcurrencyGuardTests(ScopeGuardBase):
    async def test_one_wake_cannot_flood_the_client(self) -> None:
        fake = _FakeTorrentManager()

        async def connect():
            return fake, True, "ready"

        self.toolbox.connect_torrents = connect
        job = Job(tmdb_id=99, title="Fixture", wanted_episodes={"1": [1, 2, 3]})
        session = AgentSession(agent=AgentKind.FETCH, job_id=job.id)
        job.session_id = session.id
        self.store.save_job(job)
        self.store.save_session(session)
        add = self._tool(fetch_tools(self.toolbox), "client_add")
        ctx = ToolCtx(session=session, runtime=_Runtime())

        await add.handler(ctx, {"info_hash": "a" * 40, "name": "one"})
        await add.handler(ctx, {"info_hash": "b" * 40, "name": "two"})
        with self.assertRaisesRegex(ToolError, "Transfer limit reached"):
            await add.handler(ctx, {"info_hash": "c" * 40, "name": "three"})
        active = [d for d in self.storage.get_all_downloads()
                  if d.status == DownloadStatus.DOWNLOADING]
        self.assertEqual(len(active), 2)


class StagingIsolationTests(ScopeGuardBase):
    async def _make_download(self, dl_id: str, job_id: str) -> Download:
        own = self.staging / dl_id
        own.mkdir()
        (own / "episode.mkv").write_bytes(b"payload")
        dl = Download(id=dl_id, name=dl_id, magnet_url="magnet:?xt=x",
                      media_type=MediaType.TV, status=DownloadStatus.COMPLETED,
                      staging_path=str(own),
                      metadata={"agent_managed": True, "job_id": job_id})
        await self.storage.add_download(dl)
        return dl

    async def test_media_agent_cannot_see_another_downloads_staging(self) -> None:
        await self._make_download("dl-aaa", "job-a")
        await self._make_download("dl-bbb", "job-b")
        session_a = AgentSession(agent=AgentKind.MEDIA, job_id="job-a",
                                 download_id="dl-aaa")
        fs_list = self._tool(media_tools(self.toolbox), "fs_list")
        ctx = ToolCtx(session=session_a, runtime=_Runtime())

        # Default listing shows only its own files.
        own_files = await fs_list.handler(ctx, {})
        self.assertTrue(all("dl-aaa" in f["path"] for f in own_files))

        # The other download's staging folder is outside the jail.
        with self.assertRaisesRegex(ToolError, "outside your allowed folders"):
            await fs_list.handler(ctx, {"path": str(self.staging / "dl-bbb")})
        # And so is the shared staging root itself.
        with self.assertRaisesRegex(ToolError, "outside your allowed folders"):
            await fs_list.handler(ctx, {"path": str(self.staging)})

    async def test_media_agent_cannot_delete_across_downloads(self) -> None:
        await self._make_download("dl-aaa", "job-a")
        other = await self._make_download("dl-bbb", "job-b")
        session_a = AgentSession(agent=AgentKind.MEDIA, job_id="job-a",
                                 download_id="dl-aaa")
        fs_delete = self._tool(media_tools(self.toolbox), "fs_delete")
        ctx = ToolCtx(session=session_a, runtime=_Runtime())
        with self.assertRaisesRegex(ToolError, "outside your allowed folders"):
            await fs_delete.handler(ctx, {"path": str(Path(other.staging_path) / "episode.mkv")})
        self.assertTrue((Path(other.staging_path) / "episode.mkv").exists())


if __name__ == "__main__":
    unittest.main()
