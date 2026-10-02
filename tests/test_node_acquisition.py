import asyncio
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from backend.storage import Storage
from backend.models import SparrowConfig, DownloadStatus
from backend.agents.service import AgentService
from backend.agents.models import Job, AgentSession, AgentKind, JobStatus
from backend.agents.runtime import ToolCtx, ToolError
from backend.agents.node_tools import acquisition_tools, storage_tools, components
from backend.agents.node_executor import Executor, executable
from tests.test_playback import make_video


class NodeAcquisitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not executable("ffmpeg") or not executable("ffprobe"):
            self.skipTest("Packaged media tools required")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = Storage(str(self.root / "server"))
        await self.storage.load_all()
        await self.storage.save_config(SparrowConfig())
        self.service = AgentService(
            self.storage, str(self.root / "server"), AsyncMock()
        )
        self.service.emit = AsyncMock()
        self.owner = self.service.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )
        self.nodes, self.catalogue = components(self.service.toolbox)
        self.library = self.root / "remote-library"
        self.staging = self.root / "remote-staging"
        self.library.mkdir()
        self.staging.mkdir()
        self.executor = Executor(
            self.root / "node",
            {"library": str(self.library), "staging": str(self.staging)},
            downloader={"type": "qbittorrent"},
        )
        enrollment = self.nodes.enroll("Remote storage fixture")
        self.node_id = self.nodes.pair(
            enrollment["code"], secrets.token_urlsafe(32), self.executor.capabilities()
        )["node_id"]
        self.job = Job(
            tmdb_id=42,
            media_type="movie",
            title="Fixture",
            user_id=self.owner["id"],
            library_id=self.node_id,
            node_id=self.node_id,
            min_quality="any",
            audio_pref="en",
            preferences=self.service.accounts.resolve(
                self.owner["id"], {"min_quality": "any"}
            ),
        )
        self.service.store.save_job(self.job)
        self.session = AgentSession(
            job_id=self.job.id, job_revision=self.job.revision, user_id=self.owner["id"]
        )
        self.service.store.save_session(self.session)
        self.ctx = ToolCtx(self.session, self.service.runtime)
        self.client_state = {}
        self.add_count = 0
        self.removed = []
        self.stopped = []

        async def add(magnet, path):
            self.add_count += 1
            self.client_state["a" * 40] = {
                "progress": 0,
                "status": DownloadStatus.DOWNLOADING,
                "save_path": path,
            }
            return "a" * 40

        async def remove(identity, delete_files=False):
            self.removed.append((identity, delete_files))
            self.client_state.pop(identity, None)

        self.manager = SimpleNamespace(
            connect=AsyncMock(return_value=True),
            get_torrent_status=AsyncMock(
                side_effect=lambda h: self.client_state.get(h)
            ),
            add_magnet=AsyncMock(side_effect=add),
            delete_torrent=AsyncMock(side_effect=remove),
            stop_torrent=AsyncMock(side_effect=lambda h: self.stopped.append(h)),
            start_torrent=AsyncMock(return_value=True),
        )
        self.patcher = patch(
            "backend.services.torrent_client.TorrentManager", return_value=self.manager
        )
        self.patcher.start()

        async def worker():
            while True:
                command = self.nodes.next_command(self.node_id)
                if command:
                    result = await self.executor.execute(command)
                    self.nodes.finish(self.node_id, command["id"], result)
                    self.executor.delivered(command["id"])
                else:
                    await asyncio.sleep(0.005)

        self.worker = asyncio.create_task(worker())
        self.service.toolbox.tmdb_get = AsyncMock(
            return_value={
                "id": 42,
                "title": "Fixture",
                "runtime": 0.4,
                "original_language": "en",
            }
        )

    async def asyncTearDown(self):
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        self.patcher.stop()
        self.temp.cleanup()

    async def call(self, name, args, ctx=None, media=False):
        tools = (
            storage_tools(self.service.toolbox)
            if media
            else acquisition_tools(self.service.toolbox)
        )
        return await next(t for t in tools if t.name == name).handler(
            ctx or self.ctx, args
        )

    async def test_request_reaches_chosen_node_and_verified_copy_survives_retry(self):
        result = await self.call(
            "client_add", {"info_hash": "a" * 40, "name": "Fixture"}
        )
        again = await self.call(
            "client_add", {"info_hash": "a" * 40, "name": "Fixture"}
        )
        self.assertEqual(result["download_id"], again["download_id"])
        self.assertEqual(self.add_count, 1)
        dl = self.storage.get_download(result["download_id"])
        self.assertEqual(dl.metadata["node_id"], self.node_id)
        folder = self.staging / dl.id
        folder.mkdir(exist_ok=True)
        source = await make_video(folder)
        self.client_state["a" * 40]["progress"] = 1
        await self.service.reconcile_transfers()
        self.assertEqual(
            self.storage.get_download(dl.id).status, DownloadStatus.COMPLETED
        )
        self.assertTrue(
            any(
                c.args[0].kind == "files_landed"
                for c in self.service.emit.call_args_list
            )
        )
        media = AgentSession(
            agent=AgentKind.MEDIA,
            job_id=self.job.id,
            download_id=dl.id,
            job_revision=self.job.revision,
        )
        self.service.store.save_session(media)
        ctx = ToolCtx(media, self.service.runtime)
        listed = await self.call("fs_list", {}, ctx, True)
        self.assertTrue(listed[0]["path"].startswith("staging/" + dl.id + "/"))
        publication = {
            "src": f"staging/{dl.id}/fixture.mp4",
            "dst": "library/Fixture/movie.mp4",
        }
        await self.call("fs_move", publication, ctx, True)
        await self.call("fs_move", publication, ctx, True)
        recorded = await self.call(
            "inventory_write",
            {"tmdb_id": 42, "path": "library/Fixture/movie.mp4", "verified": True},
            ctx,
            True,
        )
        self.assertTrue(recorded["verified"])
        self.assertTrue(source.exists())
        self.assertEqual(
            source.read_bytes(), (self.library / "Fixture/movie.mp4").read_bytes()
        )
        await self.call(
            "session_done",
            {"summary": "Verified the movie and retained its source."},
            ctx,
            True,
        )
        self.assertEqual(
            self.storage.get_download(dl.id).status, DownloadStatus.ORGANIZED
        )
        current = self.service.store.get_job(self.job.id)
        current.preferences["values"]["require_subtitles"] = True
        self.service.store.save_job(current)
        with self.assertRaisesRegex(ToolError, "required subtitles"):
            await self.call("job_close", {"outcome": "complete"})
        self.assertEqual(
            self.service.store.get_job(self.job.id).status, JobStatus.ACTIVE
        )
        current.preferences["values"]["require_subtitles"] = False
        self.service.store.save_job(current)
        await self.call("job_close", {"outcome": "complete"})
        self.assertEqual(
            self.service.store.get_job(self.job.id).status, JobStatus.COMPLETE
        )
        self.assertEqual(
            self.catalogue.asset(self.owner, recorded["asset_id"])["state"], "ready"
        )

    async def test_a_cheap_pick_downloads_only_after_review(self):
        from backend.agents import acquisition_review, scout

        row = dict(seeders=9, size=10**9, files=1, quality="1080p", source="WEB-DL", dual=False, subs=True, episode_size=10**9)
        rows = [
            {**row, "rid": "r1", "info_hash": "a" * 40, "name": "Fixture 2020 1080p", "coverage": "single"},
            {**row, "rid": "r2", "info_hash": "b" * 40, "name": "Fixture collection", "coverage": "pack", "files": 5, "chosen": ["Fixture/Fixture.mkv"]},
        ]
        with patch.object(scout, "scout", AsyncMock(return_value=(rows, ["Fixture 2020"], 3))), patch.object(
            scout, "titles_for", AsyncMock(return_value=(["Fixture"], []))
        ):
            listing = await self.call("find_releases", {})
        self.assertIn("r1 · 1.00 GB · 9 seeds", listing)
        usage = {"input_tokens": 500, "output_tokens": 40}
        decisions = [
            ({"approve": False, "reason": "The collection has the better copy.", "instead": "r2", "queries": []}, 0.003, "smart", usage),
            ({"approve": True, "reason": "Right film.", "instead": "", "queries": []}, 0.003, "smart", usage),
        ]
        self.service.toolbox.select_soon = AsyncMock()
        with patch.object(acquisition_review, "review", AsyncMock(side_effect=decisions)):
            vetoed = await self.call("propose_release", {"release": "r1", "reason": "single file"})
            self.assertIn("Reviewer suggests r2", vetoed)
            self.assertEqual(self.add_count, 0)
            approved = await self.call("propose_release", {"release": "r2", "reason": "the reviewer's choice"})
        self.assertEqual(self.add_count, 1)
        download = self.storage.get_download(approved["download_id"])
        self.assertEqual(download.metadata["wanted_files"], ["Fixture/Fixture.mkv"])
        self.assertAlmostEqual(self.session.spend.dollars, 0.006)
        with self.assertRaises(ToolError):
            await self.call("propose_release", {"release": "r9", "reason": "not listed"})

    async def test_only_the_smart_model_searches_and_adds_directly(self):
        cheap = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id, model=self.service.cheap_model())
        smart = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id, model=self.service.smart_model())
        names = lambda session: {t.name for t in self.service.runtime.tools_for(session)}
        self.assertTrue({"find_releases", "propose_release", "escalate_model"} <= names(cheap))
        self.assertFalse({"tpb_search", "client_add", "torrent_peek"} & names(cheap))
        self.assertTrue({"tpb_search", "client_add", "find_releases"} <= names(smart))
        self.assertFalse({"propose_release"} & names(smart))
        self.assertEqual(self.service.fetch_model(), self.service.cheap_model())

    async def test_durable_runtime_replay_does_not_add_a_second_download(self):
        from backend.agents.runtime import AgentRuntime
        from backend.agents.store import AgentStore
        from test_discovery import Block

        tools = {t.name: t for t in acquisition_tools(self.service.toolbox)}
        block = Block("client_add", {"info_hash": "a" * 40, "name": "Fixture"})
        first = await self.service.runtime._run_tool(self.ctx, tools, block)
        self.assertNotIn("is_error", first)
        reopened = AgentRuntime(AgentStore(str(self.root / "server")), lambda: "unused")
        loaded = reopened.store.get_session(self.session.id)
        replay = await reopened._run_tool(ToolCtx(loaded, reopened), tools, block)
        self.assertEqual(first, replay)
        self.assertEqual(self.add_count, 1)
        self.assertEqual(len(self.storage.get_all_downloads()), 1)

    async def test_offline_pause_persists_intent_and_reconciles_when_node_returns(self):
        result = await self.call(
            "client_add", {"info_hash": "a" * 40, "name": "Fixture"}
        )
        dl = self.storage.get_download(result["download_id"])
        with self.nodes.accounts.connect() as db:
            db.execute("UPDATE nodes SET last_seen=0 WHERE id=?", (self.node_id,))
        paused = await self.service.pause_job(self.job.id)
        self.assertIn("Waiting", paused.state_line)
        self.assertEqual(
            self.storage.get_download(dl.id).status, DownloadStatus.DOWNLOADING
        )
        self.assertEqual(
            self.storage.get_download(dl.id).metadata["desired_control"], "stop"
        )
        self.nodes.heartbeat(self.node_id, self.executor.capabilities())
        await self.service.reconcile_transfers()
        self.assertEqual(self.storage.get_download(dl.id).status, DownloadStatus.PAUSED)
        self.assertEqual(self.stopped, ["a" * 40])
        with self.assertRaises(ToolError):
            await self.call("client_add", {"info_hash": "b" * 40})
        await self.service.resume_job(self.job.id)
        self.assertEqual(
            self.storage.get_download(dl.id).status, DownloadStatus.DOWNLOADING
        )
        await self.service.cancel_job(self.job.id)
        self.assertIn(("a" * 40, False), self.removed)

    async def test_inventory_cannot_claim_other_title_or_unpublished_file(self):
        result = await self.call(
            "client_add", {"info_hash": "a" * 40, "name": "Fixture"}
        )
        media = AgentSession(
            agent=AgentKind.MEDIA, job_id=self.job.id, download_id=result["download_id"]
        )
        self.service.store.save_session(media)
        ctx = ToolCtx(media, self.service.runtime)
        for args in (
            {"tmdb_id": 99, "path": "library/movie.mp4"},
            {"tmdb_id": 42, "path": "library/movie.mp4"},
        ):
            with self.subTest(args=args), self.assertRaises(ToolError):
                await self.call("inventory_write", args, ctx, True)


class ScoutJudgementTests(unittest.TestCase):
    def test_wrong_seasons_remakes_dubs_and_dead_swarms_are_dropped(self):
        from backend.agents import scout

        job = SimpleNamespace(media_type="tv", year=1998, min_quality="720p", preferred_quality="1080p", audio_pref="original")
        titles = ["Cowboy Bebop"]
        judge = lambda name, seeds=5, files=1: scout.judge(
            {"id": 1, "name": name, "info_hash": "c" * 40, "seeders": seeds, "size": 10**9, "num_files": files}, job, [(1, 5)], titles
        )
        self.assertEqual(judge("Cowboy Bebop S01E05 1080p BluRay")["coverage"], "single")
        self.assertEqual(judge("Cowboy Bebop - 05 (1080p) [Dual Audio]")["coverage"], "single")
        self.assertEqual(judge("Cowboy Bebop S01 1080p BluRay", files=26)["coverage"], "pack")
        for name in ("Cowboy Bebop 2021 S01E05 1080p NF WEB-DL", "Cowboy Bebop S02E05 1080p", "Cowboy Bebop II - 05", "Cowboy Bebop S01E05 English Dubbed 1080p", "Cowboy Bebop S01E05 480p"):
            self.assertIsNone(judge(name), name)
        self.assertIsNone(judge("Cowboy Bebop S01E05 1080p", seeds=0))

    def test_the_standard_cut_is_chosen_from_a_pack(self):
        from backend.agents import scout

        listing = [
            {"name": {"0": "Show - 1x03 DC - Title [1080p].mkv"}, "size": {"0": "333"}},
            {"name": {"0": "Show - 1x03 - Title [1080p].mkv"}, "size": {"0": "269"}},
            {"name": {"0": "Show - 1x04 - Next [1080p].mkv"}, "size": {"0": "270"}},
            {"name": {"0": "Show - 1x03 - Title sample.mkv"}, "size": {"0": "9"}},
        ]
        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                return SimpleNamespace(json=lambda: listing)

        with patch("httpx.AsyncClient", return_value=Client()):
            chosen = asyncio.run(scout.peek("1", [(1, 3)], "tv"))
        self.assertEqual(chosen[(1, 3)]["name"], "Show - 1x03 - Title [1080p].mkv")
