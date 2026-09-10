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
