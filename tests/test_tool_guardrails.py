from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from backend.agents.models import AgentKind, AgentSession, Job
from backend.agents.runtime import ToolCtx, ToolError
from backend.agents.store import AgentStore
from backend.agents.tools import (
    Toolbox, _apibay_indexed_value, fetch_tools, media_tools,
)
from backend.models import (
    Download, DownloadStatus, LibraryItem, MediaType, SparrowConfig,
    TorrentClientConfig, TorrentClientType,
)
from backend.storage import Storage


class _Runtime:
    pass


class _FakeTorrentManager:
    async def add_magnet(self, magnet: str, destination: str) -> str:
        return "a" * 40


class ToolGuardrailTests(unittest.IsolatedAsyncioTestCase):
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
        self.job = Job(tmdb_id=99, title="Fixture", wanted_episodes={"1": [1]})
        self.session = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id)
        self.job.session_id = self.session.id
        self.store.save_job(self.job)
        self.store.save_session(self.session)

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _tool(tools, name):
        return next(tool for tool in tools if tool.name == name)

    def test_apibay_file_fields_accept_observed_response_shapes(self) -> None:
        self.assertEqual(_apibay_indexed_value({"0": "movie.mkv"}), "movie.mkv")
        self.assertEqual(_apibay_indexed_value(["movie.mkv"]), "movie.mkv")
        self.assertEqual(_apibay_indexed_value("movie.mkv"), "movie.mkv")
        self.assertEqual(_apibay_indexed_value([], "fallback"), "fallback")

    async def test_search_and_torrent_listing_do_not_discard_tail_rows(self):
        ctx = ToolCtx(session=self.session, runtime=_Runtime())
        search = self._tool(fetch_tools(self.toolbox), "tpb_search")
        candidates = [{"id": str(i), "name": f"Fixture candidate {i}"} for i in range(85)]
        with patch("backend.agents.tools.apibay_query", new=AsyncMock(return_value=candidates)):
            results = await search.handler(ctx, {"query": "Fixture"})
        self.assertEqual(len(results), 85)
        self.assertEqual(results[-1]["apibay_id"], "84")

        peek = self._tool(fetch_tools(self.toolbox), "torrent_peek")
        listing = [{"name": f"S01E{i:03d}.mkv", "size": 1_000_000} for i in range(1, 251)]
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = Mock(json=lambda: listing)
        with patch("backend.agents.tools.httpx.AsyncClient", return_value=client):
            files = await peek.handler(ctx, {"apibay_id": "84"})
        self.assertEqual(len(files), 250)
        self.assertEqual(files[-1]["file"], "S01E250.mkv")

    async def test_mocked_acquisition_marks_download_agent_managed(self) -> None:
        async def connect():
            return _FakeTorrentManager(), True, "ready"

        self.toolbox.connect_torrents = connect
        tool = self._tool(fetch_tools(self.toolbox), "client_add")
        ctx = ToolCtx(session=self.session, runtime=_Runtime())
        result = await tool.handler(ctx, {"info_hash": "a" * 40, "name": "Fixture release"})

        download = self.storage.get_download(result["download_id"])
        self.assertTrue(download.metadata["agent_managed"])
        self.assertEqual(download.metadata["job_id"], self.job.id)
        # Every download stages in its own isolated folder under the root.
        own = Path(download.staging_path)
        self.assertEqual(own.name, download.id)
        self.assertEqual(own.parent, self.staging.resolve())
        self.assertTrue(own.is_dir())

    async def test_job_cannot_close_before_verified_inventory_exists(self) -> None:
        self.job.media_type = "movie"
        self.job.min_quality = "720p"
        self.store.save_job(self.job)
        tool = self._tool(fetch_tools(self.toolbox), "job_close")
        with self.assertRaisesRegex(ToolError, "no library inventory"):
            await tool.handler(ToolCtx(session=self.session, runtime=_Runtime()), {
                "outcome": "complete", "note": "ready"
            })

    async def test_tmdb_movie_and_tv_id_namespaces_do_not_collide(self) -> None:
        await self.storage.add_library_item(LibraryItem(
            id="tv-99", title="TV Fixture", media_type=MediaType.TV,
            path=str(self.library / "TV Fixture"), tmdb_id=99,
        ))
        await self.storage.add_library_item(LibraryItem(
            id="movie-99", title="Movie Fixture", media_type=MediaType.MOVIE,
            path=str(self.library / "Movie Fixture.mp4"), tmdb_id=99,
        ))

        self.assertEqual(self.toolbox.library_item_for(99, "tv").id, "tv-99")
        self.assertEqual(self.toolbox.library_item_for(99, "movie").id, "movie-99")

    async def test_movie_inventory_can_be_verified_then_close_the_job(self) -> None:
        self.job.media_type = "movie"
        self.job.min_quality = "720p"
        self.store.save_job(self.job)
        media_session = AgentSession(
            agent=AgentKind.MEDIA, job_id=self.job.id, download_id="dl-movie")
        await self.storage.add_download(Download(
            id=media_session.download_id,
            name="Fixture movie",
            magnet_url="magnet:?xt=fixture",
            media_type=MediaType.MOVIE,
            status=DownloadStatus.COMPLETED,
            metadata={"agent_managed": True, "job_id": self.job.id},
        ))
        movie = self.library / "Movies" / "Fixture (2026)" / "Fixture (2026).mp4"
        movie.parent.mkdir(parents=True)
        movie.write_bytes(b"fixture media")

        async def tmdb_get(path: str, **_):
            self.assertEqual(path, "/movie/99")
            return {"title": "Fixture", "release_date": "2026-01-01",
                    "runtime": 10, "overview": ""}

        self.toolbox.tmdb_get = tmdb_get
        inventory = self._tool(media_tools(self.toolbox), "inventory_write")
        with patch("backend.agents.tools._probe_media_facts", new=AsyncMock(return_value={
            "duration_seconds": 600.0, "width": 1920, "height": 1080,
            "quality": "1080p",
        })):
            await inventory.handler(
                ToolCtx(session=media_session, runtime=_Runtime()),
                {"tmdb_id": 99, "media_type": "movie", "path": str(movie),
                 "quality": "720p", "verified": True},
            )
        item = self.toolbox.library_item_for(99)
        self.assertEqual(item.metadata["quality"], "1080p")

        done = self._tool(media_tools(self.toolbox), "session_done")
        media_ctx = ToolCtx(session=media_session, runtime=_Runtime())
        await done.handler(media_ctx, {"summary": "Verified and placed."})
        download = self.storage.get_download(media_session.download_id)
        self.assertEqual(download.status, DownloadStatus.ORGANIZED)
        self.assertEqual(Path(download.library_path), movie.resolve())
        self.assertTrue(media_ctx.close)

        close = self._tool(fetch_tools(self.toolbox), "job_close")
        ctx = ToolCtx(session=self.session, runtime=_Runtime())
        await close.handler(ctx, {"outcome": "complete", "note": "Ready to watch."})
        self.assertTrue(ctx.close)
        self.assertEqual(self.store.get_job(self.job.id).status.value, "complete")

    async def test_tv_episode_inventory_can_satisfy_first_episode_job(self) -> None:
        self.job.media_type = "tv"
        self.job.min_quality = "720p"
        self.store.save_job(self.job)
        media_session = AgentSession(
            agent=AgentKind.MEDIA, job_id=self.job.id, download_id="dl-episode")
        episode = self.library / "Fixture" / "Season 01" / "Fixture - S01E01.mkv"
        episode.parent.mkdir(parents=True)
        episode.write_bytes(b"fixture episode")

        async def tmdb_get(path: str, **_):
            if path == "/tv/99":
                return {"name": "Fixture", "first_air_date": "2026-01-01",
                        "episode_run_time": [24], "number_of_seasons": 1,
                        "number_of_episodes": 1}
            self.assertEqual(path, "/tv/99/season/1")
            return {"episodes": [{"episode_number": 1, "runtime": 24}]}

        self.toolbox.tmdb_get = tmdb_get
        inventory = self._tool(media_tools(self.toolbox), "inventory_write")
        with patch("backend.agents.tools._probe_media_facts", new=AsyncMock(return_value={
            "duration_seconds": 1440.0, "width": 1920, "height": 1080,
            "quality": "1080p",
        })):
            await inventory.handler(
                ToolCtx(session=media_session, runtime=_Runtime()),
                {"tmdb_id": 99, "media_type": "tv", "season": 1, "episode": 1,
                 "path": str(episode), "show_path": str(episode.parents[1]),
                 "quality": "1080p", "verified": True},
            )

        close = self._tool(fetch_tools(self.toolbox), "job_close")
        ctx = ToolCtx(session=self.session, runtime=_Runtime())
        await close.handler(ctx, {"outcome": "complete", "note": "Episode ready."})
        self.assertTrue(ctx.close)
        self.assertEqual(self.store.get_job(self.job.id).status.value, "complete")

    async def test_media_report_closes_the_fetch_feedback_loop(self) -> None:
        media_session = AgentSession(
            agent=AgentKind.MEDIA, job_id=self.job.id, download_id="dl-fixture")
        tool = self._tool(media_tools(self.toolbox), "report_to_fetch")
        await tool.handler(ToolCtx(session=media_session, runtime=_Runtime()), {
            "text": "Episode one was probed, placed, and verified."
        })

        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].kind, "media_report")
        self.assertEqual(self.events[0].job_id, self.job.id)

    async def test_normal_move_cannot_remove_existing_library_media(self) -> None:
        old = self.library / "episode.mkv"
        old.write_bytes(b"existing")
        tool = self._tool(media_tools(self.toolbox), "fs_move")
        with self.assertRaisesRegex(ToolError, "Existing library files"):
            await tool.handler(ToolCtx(session=self.session, runtime=_Runtime()), {
                "src": str(old), "dst": str(self.staging / "episode.mkv")
            })
        self.assertTrue(old.exists())

    async def test_upgrade_swap_is_verified_and_recoverable(self) -> None:
        old = self.library / "episode-old.mkv"
        new = self.staging / "episode-new.mkv"
        old.write_bytes(b"existing")
        new.write_bytes(b"replacement")
        tool = self._tool(media_tools(self.toolbox), "upgrade_swap")
        ctx = ToolCtx(session=self.session, runtime=_Runtime())

        with patch("backend.agents.tools._probe_duration_seconds",
                   new=AsyncMock(side_effect=[1200.0, 1500.0])):
            with self.assertRaisesRegex(ToolError, "durations differ"):
                await tool.handler(ctx, {"old_path": str(old), "new_path": str(new)})
        self.assertTrue(old.exists())
        self.assertTrue(new.exists())

        with patch("backend.agents.tools._probe_duration_seconds",
                   new=AsyncMock(side_effect=[1200.0, 1201.0])):
            await tool.handler(ctx, {"old_path": str(old), "new_path": str(new)})
        self.assertFalse(old.exists())
        self.assertFalse(new.exists())
        self.assertEqual((self.library / "episode-new.mkv").read_bytes(), b"replacement")


class PackSelectionTests(unittest.IsolatedAsyncioTestCase):
    """Taking one episode from a pack downloads only that episode's file."""

    asyncSetUp = ToolGuardrailTests.asyncSetUp
    asyncTearDown = ToolGuardrailTests.asyncTearDown

    def pack(self):
        return [
            {"name": "Show/Show - 01 [1080p].mkv", "size": 300},
            {"name": "Show/Show - 02 [1080p].mkv", "size": 310},
            {"name": "Show/Extras/Show - NCOP.mkv", "size": 50},
        ]

    async def test_only_the_chosen_file_downloads(self):
        from backend.agents.tools import match_files

        self.assertEqual(match_files(self.pack(), ["Show - 02 [1080p].mkv"]), [1])
        self.assertEqual(match_files(self.pack(), ["show/show - 01 [1080p].MKV"]), [0])
        manager = Mock(get_files=AsyncMock(return_value=self.pack()), skip_files=AsyncMock(return_value=True))
        download = Download(id="dl-x", name="Show pack", magnet_url="magnet:?", torrent_hash="b" * 40,
                            metadata={"job_id": self.job.id, "wanted_files": ["Show - 02 [1080p].mkv"]})
        await self.storage.add_download(download)
        with patch.object(self.toolbox, "torrents", return_value=manager):
            selection = await self.toolbox.apply_file_selection(download)
        manager.skip_files.assert_awaited_once_with("b" * 40, [0, 2])
        self.assertEqual(selection["files"], ["Show/Show - 02 [1080p].mkv"])
        self.assertEqual(self.storage.get_download("dl-x").metadata["selection"]["bytes"], 310)

    async def test_waits_for_the_file_list_and_stops_when_nothing_matches(self):
        waiting = Mock(get_files=AsyncMock(return_value=[]), skip_files=AsyncMock())
        download = Download(id="dl-y", name="Other pack", magnet_url="magnet:?", torrent_hash="c" * 40,
                            metadata={"job_id": self.job.id, "wanted_files": ["Show - 05.mkv"]})
        await self.storage.add_download(download)
        with patch.object(self.toolbox, "torrents", return_value=waiting):
            self.assertIsNone(await self.toolbox.apply_file_selection(download))
        wrong = Mock(get_files=AsyncMock(return_value=self.pack()), skip_files=AsyncMock(), stop_torrent=AsyncMock(return_value=True))
        with patch.object(self.toolbox, "torrents", return_value=wrong):
            selection = await self.toolbox.apply_file_selection(download)
        self.assertIn("error", selection)
        wrong.stop_torrent.assert_awaited_once()
        wrong.skip_files.assert_not_awaited()
        self.assertEqual(self.storage.get_download("dl-y").status, DownloadStatus.ERROR)
        self.assertEqual(self.events[-1].kind, "download_stalled")
