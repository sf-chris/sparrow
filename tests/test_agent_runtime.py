from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from backend.agents.models import AgentKind, AgentSession, Event, Job, SessionStatus
from backend.agents.runtime import AgentRuntime
from backend.agents.service import AgentService, STALL_AFTER
from backend.agents.store import AgentStore
from backend.models import Download, MediaType, SparrowConfig
from backend.storage import Storage


async def _broadcast(_: dict) -> None:
    return None


class AgentPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_session_roundtrip_and_interrupted_tool_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentStore(tmp)
            job = Job(tmdb_id=42, title="Fixture Show", wanted_episodes={"1": [1]})
            session = AgentSession(
                agent=AgentKind.FETCH,
                job_id=job.id,
                status=SessionStatus.RUNNING,
                messages=[{
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "tool-1", "name": "inventory_read", "input": {}}],
                }],
            )
            job.session_id = session.id
            store.save_job(job)
            store.save_session(session)

            reopened = AgentStore(tmp)
            loaded_job = reopened.get_job(job.id)
            loaded_session = reopened.get_session(session.id)
            self.assertEqual(loaded_job.title, "Fixture Show")
            self.assertEqual(loaded_session.status, SessionStatus.RUNNING)

            runtime = AgentRuntime(reopened, api_key_getter=lambda: "unused")
            runtime._repair_interrupted(loaded_session)
            result = loaded_session.messages[-1]["content"][0]
            self.assertEqual(result["type"], "tool_result")
            self.assertEqual(result["tool_use_id"], "tool-1")
            self.assertIn("interrupted", result["content"])

    async def test_media_report_routes_back_to_the_fetch_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            await storage.load_all()
            await storage.save_config(SparrowConfig())
            service = AgentService(storage, tmp, _broadcast)
            job = Job(tmdb_id=7, title="Closed Loop", wanted_episodes={"1": [1]})
            session = AgentSession(agent=AgentKind.FETCH, job_id=job.id)
            job.session_id = session.id
            service.store.save_session(session)
            service.store.save_job(job)
            service._wake_soon = MagicMock()

            event = Event(kind="media_report", job_id=job.id, download_id="dl-1",
                          payload={"description": "The landed file was verified."})
            await service._route(event)

            service._wake_soon.assert_called_once_with(session.id, event)

    async def test_stalled_download_emits_one_agent_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            await storage.load_all()
            service = AgentService(storage, tmp, _broadcast)
            service.emit = AsyncMock()
            download = Download(
                id="dl-stalled", name="Fixture", magnet_url="magnet:?xt=fixture",
                media_type=MediaType.TV, metadata={"job_id": "job-1"},
            )
            service._progress_seen[download.id] = (0.25, time.time() - STALL_AFTER - 1)

            service._detect_stall(download, 0.25)
            await __import__("asyncio").sleep(0)
            service._detect_stall(download, 0.25)
            await __import__("asyncio").sleep(0)

            service.emit.assert_called_once()
            event = service.emit.call_args.args[0]
            self.assertEqual(event.kind, "download_stalled")
            self.assertEqual(event.job_id, "job-1")
            service._detect_stall(download, 0.50)
            await __import__("asyncio").sleep(0)
            service.emit.assert_called_once()  # Logging recovery adds no model wake.

    async def test_a_crawling_download_is_reported_once(self) -> None:
        from backend.agents.service import CRAWL_AFTER

        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            await storage.load_all()
            service = AgentService(storage, tmp, _broadcast)
            service.emit = AsyncMock()
            download = Download(
                id="dl-slow", name="Fixture", magnet_url="magnet:?xt=fixture",
                media_type=MediaType.TV, metadata={"job_id": "job-1"},
            )
            # 1% in 15 minutes: about a day to finish.
            service._pace_seen[download.id] = (0.10, time.time() - STALL_AFTER - 1)
            service._detect_crawl(download, 0.11)
            await __import__("asyncio").sleep(0)
            service.emit.assert_called_once()
            self.assertIn("crawling", service.emit.call_args.args[0].payload["description"])
            service._pace_seen[download.id] = (0.11, time.time() - STALL_AFTER - 1)
            service._detect_crawl(download, 0.12)
            await __import__("asyncio").sleep(0)
            service.emit.assert_called_once()  # once per slow spell
            # A healthy pace (60% in 15 minutes) clears it; no event.
            service._pace_seen[download.id] = (0.12, time.time() - STALL_AFTER - 1)
            service._detect_crawl(download, 0.72)
            await __import__("asyncio").sleep(0)
            service.emit.assert_called_once()
            self.assertNotIn(download.id, service._crawl_flagged)
            self.assertGreater(CRAWL_AFTER, STALL_AFTER)

    async def test_closed_sessions_do_not_keep_stale_wake_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(tmp)
            await storage.load_all()
            service = AgentService(storage, tmp, _broadcast)
            session = AgentSession(
                agent=AgentKind.FETCH,
                status=SessionStatus.CLOSED,
                wake_at=time.time() + 600,
                wake_reason="Waiting for a download that already finished.",
            )
            service.store.save_session(session)

            service._normalize_closed_sessions()

            repaired = service.store.get_session(session.id)
            self.assertEqual(repaired.wake_at, 0.0)
            self.assertEqual(repaired.wake_reason, "")
