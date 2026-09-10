"""Regression checks for the accepted product review, using isolated storage."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.agents.media_state import media_state, file_version, audio_satisfies
from backend.agents.models import (
    AgentSession,
    AgentKind,
    Job,
    JobStatus,
    Mandate,
    SessionStatus,
    Event,
)
from backend.agents.runtime import ToolCtx, ToolError, AgentSpec, ToolDef
from backend.agents.service import AgentService
from backend.agents.tools import fetch_tools
from backend.models import SparrowConfig, LibraryItem, MediaType
from backend.services.library_view import build_library_view
from backend.services.file_organizer import scan_show_episodes
from backend.storage import Storage


class ProductCorrectnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = Storage(str(self.root / "data"))
        await self.storage.load_all()
        await self.storage.save_config(
            SparrowConfig(
                staging_dir=str(self.root / "staging"),
                library_dir=str(self.root / "library"),
                max_active_transfers=1,
            )
        )
        self.service = AgentService(self.storage, str(self.root / "data"), AsyncMock())
        self.service.emit = AsyncMock()
        self.job = self.service.store.save_job(
            Job(tmdb_id=1, media_type="movie", title="Fixture")
        )
        self.session = self.service.store.save_session(AgentSession(job_id=self.job.id))
        self.ctx = ToolCtx(self.session, self.service.runtime)
        self.add = next(
            t for t in fetch_tools(self.service.toolbox) if t.name == "client_add"
        )
        self.manager = SimpleNamespace(
            add_magnet=AsyncMock(return_value="a" * 40),
            stop_torrent=AsyncMock(return_value=True),
            delete_torrent=AsyncMock(return_value=True),
        )
        self.service.toolbox.connect_torrents = AsyncMock(
            return_value=(self.manager, True, "ready")
        )
        self.service._connected_manager = AsyncMock(return_value=self.manager)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_missing_unverified_and_changed_files_are_not_ready(self):
        path = self.root / "movie.mp4"
        self.assertEqual(media_state({"verified": True}, str(path)), "unavailable")
        path.write_bytes(b"original")
        self.assertEqual(media_state({}, str(path)), "verifying")
        record = {"verified": True, "file_version": file_version(path)}
        self.assertEqual(media_state(record, str(path)), "ready")
        path.write_bytes(b"replaced with different bytes")
        self.assertEqual(media_state(record, str(path)), "verifying")
        await self.storage.add_library_item(
            LibraryItem(
                id="a",
                title="Fixture",
                media_type=MediaType.MOVIE,
                path=str(path),
                tmdb_id=1,
                metadata=record,
            )
        )
        entry = build_library_view(self.storage, self.service.store)[0]
        self.assertEqual(entry["ready_count"], 0)

    def test_audio_requires_evidence_for_the_requested_language(self):
        self.assertFalse(audio_satisfies({"audio_languages": ["fra"]}, "english"))
        self.assertTrue(audio_satisfies({"audio_languages": ["eng"]}, "english"))
        self.assertTrue(audio_satisfies({"audio_languages": ["jpn"]}, "original", "ja"))
        self.assertFalse(audio_satisfies({}, "original"))

    async def test_completion_refuses_wrong_audio_even_when_the_file_exists(self):
        path = self.root / "movie.mp4"
        path.write_bytes(b"fixture")
        self.job.audio_pref = "english"
        self.service.store.save_job(self.job)
        await self.storage.add_library_item(
            LibraryItem(
                id="a",
                title="Fixture",
                media_type=MediaType.MOVIE,
                path=str(path),
                tmdb_id=1,
                metadata={
                    "verified": True,
                    "quality": "1080p",
                    "audio_languages": ["fra"],
                },
            )
        )
        close = next(
            t for t in fetch_tools(self.service.toolbox) if t.name == "job_close"
        )
        with self.assertRaisesRegex(ToolError, "audio"):
            await close.handler(self.ctx, {"outcome": "complete"})
        self.assertEqual(
            self.service.store.get_job(self.job.id).status, JobStatus.ACTIVE
        )

    async def test_concurrent_calls_cannot_exceed_transfer_cap(self):
        async def slow_add(*args):
            await asyncio.sleep(0.02)
            return "a" * 40

        self.manager.add_magnet.side_effect = slow_add
        results = await asyncio.gather(
            *(
                self.add.handler(self.ctx, {"info_hash": char * 40})
                for char in ("a", "b")
            ),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, ToolError) for r in results), 1)
        self.assertEqual(self.manager.add_magnet.call_count, 1)
        self.assertEqual(len(self.storage.get_all_downloads()), 1)

    async def test_retry_uses_existing_reservation(self):
        await self.add.handler(self.ctx, {"info_hash": "a" * 40})
        await self.add.handler(self.ctx, {"info_hash": "a" * 40})
        self.assertEqual(self.manager.add_magnet.call_count, 1)

    async def test_paused_cancelled_and_resumed_jobs_reject_old_context(self):
        await self.service.pause_job(self.job.id)
        with self.assertRaises(ToolError):
            await self.add.handler(self.ctx, {"info_hash": "a" * 40})
        await self.service.resume_job(self.job.id)
        with self.assertRaises(ToolError):
            await self.add.handler(self.ctx, {"info_hash": "a" * 40})
        await self.service.cancel_job(self.job.id)
        with self.assertRaises(ToolError):
            await self.add.handler(self.ctx, {"info_hash": "a" * 40})
        self.manager.add_magnet.assert_not_called()

    async def test_cancel_waits_for_inflight_add_then_removes_it(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked_add(*args):
            started.set()
            await release.wait()
            return "a" * 40

        self.manager.add_magnet.side_effect = blocked_add
        task = asyncio.create_task(self.add.handler(self.ctx, {"info_hash": "a" * 40}))
        await started.wait()
        cancelled = asyncio.create_task(self.service.cancel_job(self.job.id))
        await asyncio.sleep(0.01)
        self.assertEqual(
            self.service.store.get_job(self.job.id).status, JobStatus.ABANDONED
        )
        release.set()
        await asyncio.gather(task, cancelled)
        self.manager.delete_torrent.assert_awaited_once_with(
            "a" * 40, delete_files=False
        )
        self.assertEqual(self.storage.get_all_downloads()[0].status.value, "error")

    async def test_cancel_during_connection_prevents_external_add(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def connect():
            started.set()
            await release.wait()
            return self.manager, True, "ready"

        self.service.toolbox.connect_torrents = connect
        task = asyncio.create_task(self.add.handler(self.ctx, {"info_hash": "a" * 40}))
        await started.wait()
        cancel = asyncio.create_task(self.service.cancel_job(self.job.id))
        await asyncio.sleep(0.01)
        release.set()
        with self.assertRaises(ToolError):
            await task
        await cancel
        self.manager.add_magnet.assert_not_called()

    async def test_unmatched_titles_and_failed_history_remain_distinct(self):
        for identity in ("one", "two"):
            await self.storage.add_library_item(
                LibraryItem(
                    id=identity,
                    title=identity,
                    media_type=MediaType.MOVIE,
                    path=str(self.root / identity),
                )
            )
        self.job.status = JobStatus.ABANDONED
        self.service.store.save_job(self.job)
        view = build_library_view(self.storage, self.service.store)
        self.assertEqual(len(view), 3)
        failed = next(e for e in view if e["tmdb_id"] == 1)
        self.assertTrue(failed["needs_attention"])

    def test_filename_scan_is_never_verification(self):
        (self.root / "Fixture S01E01.mkv").write_text("this is not video")
        self.assertFalse(scan_show_episodes(self.root)["1"]["1"]["verified"])

    async def test_tv_requests_require_scope_without_creating_a_mandate(self):
        self.service.toolbox.tmdb_get = AsyncMock(return_value={"name": "Fixture"})
        with self.assertRaisesRegex(ValueError, "Choose the exact"):
            await self.service.create_job(7, media_type="tv")
        self.assertIsNone(self.service.store.get_mandate(7, "tv"))
        self.assertIn("episode 2", Mandate(requested_episodes={"1": [2]}).describe())

    async def test_model_cannot_ignore_hard_step_limit(self):
        session = AgentSession(agent=AgentKind.LIBRARIAN)
        self.service.store.save_session(session)

        class Block:
            type = "tool_use"
            id = "test"
            name = "noop"
            input = {}

            def to_dict(self):
                return {
                    "type": self.type,
                    "id": self.id,
                    "name": self.name,
                    "input": {},
                }

        response = SimpleNamespace(content=[Block()])
        runtime = self.service.runtime
        runtime._call_api = AsyncMock(return_value=response)
        runtime._track_spend = lambda *_: None
        runtime.register(
            AgentSpec(
                "librarian",
                lambda: "fixture",
                AsyncMock(return_value="test"),
                lambda s: [ToolDef("noop", "test", {}, AsyncMock(return_value="ok"))],
            )
        )
        with patch("backend.agents.runtime.MAX_STEPS_PER_WAKE", 2):
            await runtime.wake(session.id, Event(kind="test"))
        self.assertEqual(runtime._call_api.call_count, 2)
        self.assertEqual(self.service.store.get_session(session.id).wake_at, 0)
