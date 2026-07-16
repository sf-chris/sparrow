"""
Behavioural evaluations: run the REAL Librarian (live LLM, real tool loop)
against fixed library/TMDB states and assert the operations it attempts and
the resulting job state. Journal prose is never a test target.

These cost real API money and are opt-in:

    SPARROW_RUN_AGENT_EVALS=1 ANTHROPIC_API_KEY=... python3 -m pytest tests/evals -q

SPARROW_EVAL_RUNS (default 2) repeats each scenario, since a model that
reaches an unexpected conclusion once is exactly what these protect against.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from backend.agents.models import (AgentKind, AgentSession, Event, JobStatus,
                                   Mandate, MonitoringMode)
from backend.agents.service import AgentService
from backend.models import (LibraryItem, MediaType, SparrowConfig,
                            TorrentClientConfig, TorrentClientType)
from backend.storage import Storage

RUN_EVALS = (os.getenv("SPARROW_RUN_AGENT_EVALS") == "1"
             and bool(os.getenv("ANTHROPIC_API_KEY")))
EVAL_RUNS = int(os.getenv("SPARROW_EVAL_RUNS", "2"))

FIXTURE_SHOW = {
    "tmdb_id": 1396, "name": "Fixture Crime Drama",
    "first_air_date": "2020-01-05", "status": "Ended", "in_production": False,
    "episode_run_time": [47], "number_of_seasons": 4, "number_of_episodes": 46,
    "seasons": [
        {"season": 1, "episodes": 8, "air_date": "2020-01-05", "name": "Season 1"},
        {"season": 2, "episodes": 12, "air_date": "2021-03-01", "name": "Season 2"},
        {"season": 3, "episodes": 13, "air_date": "2022-04-10", "name": "Season 3"},
        {"season": 4, "episodes": 13, "air_date": "2023-05-14", "name": "Season 4"},
    ],
    "next_episode_to_air": None,
    "last_episode_to_air": {"season_number": 4, "episode_number": 13},
}


@unittest.skipUnless(RUN_EVALS, "agent evals are opt-in (SPARROW_RUN_AGENT_EVALS=1)")
class LibrarianBehaviourEvals(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "Temp").mkdir()
        (self.root / "Library").mkdir()
        self.storage = Storage(str(self.root / "state"))
        await self.storage.load_all()
        await self.storage.save_config(SparrowConfig(
            staging_dir=str(self.root / "Temp"),
            library_dir=str(self.root / "Library"),
            tmdb_api_key="fixture",
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            torrent_client=TorrentClientConfig(type=TorrentClientType.TRANSMISSION),
        ))

        async def broadcast(event):
            pass

        self.service = AgentService(self.storage, str(self.root / "state"), broadcast)
        self.tmdb_fixtures: dict = {}

        async def tmdb_get(path: str, **_):
            if path in self.tmdb_fixtures:
                return self.tmdb_fixtures[path]
            raise AssertionError(f"eval fixture has no TMDB response for {path}")

        self.service.toolbox.tmdb_get = tmdb_get

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    def _fixture_tv_details(self) -> dict:
        d = dict(FIXTURE_SHOW)
        d["seasons"] = [
            {"season_number": s["season"], "episode_count": s["episodes"],
             "air_date": s["air_date"], "name": s["name"]}
            for s in FIXTURE_SHOW["seasons"]
        ]
        return d

    async def _seed_season_one_library(self) -> None:
        self.tmdb_fixtures["/tv/1396"] = self._fixture_tv_details()
        await self.storage.add_library_item(LibraryItem(
            id="tv-1396", title="Fixture Crime Drama", media_type=MediaType.TV,
            path=str(self.root / "Library" / "Fixture Crime Drama"), tmdb_id=1396,
            episodes={"1": {str(e): {
                "path": f"S01E{e:02d}.mkv", "verified": True, "quality": "1080p",
                "added_at": time.time() - 86400,
            } for e in range(1, 9)}},
        ))

    async def _run_librarian_pass(self) -> AgentSession:
        session = AgentSession(agent=AgentKind.LIBRARIAN,
                               model=self.service.cheap_model())
        self.service.store.save_session(session)
        await self.service.runtime.wake(session.id, Event(
            kind="timer", session_id=session.id,
            payload={"description": "Scheduled library pass."}))
        return self.service.store.get_session(session.id)

    def _spawn_attempts(self, session: AgentSession) -> list[dict]:
        attempts = []
        for message in session.messages:
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" \
                        and block.get("name") == "spawn_job":
                    attempts.append(block.get("input") or {})
        return attempts

    async def test_incident_replay_owned_season_one_monitoring_off(self) -> None:
        """The real incident, sanitized: season 1 requested and owned,
        monitoring off. However the Librarian reasons, seasons 2–4 must not
        end up as jobs."""
        for run in range(EVAL_RUNS):
            with self.subTest(run=run):
                await self._seed_season_one_library()
                self.service.store.save_mandate(Mandate(
                    tmdb_id=1396, media_type="tv", mode=MonitoringMode.EXACT,
                    requested_episodes={"1": list(range(1, 9))}))

                session = await self._run_librarian_pass()

                out_of_scope = [
                    job for job in self.service.store.get_jobs()
                    if job.tmdb_id == 1396 and any(
                        int(season) != 1 for season in job.wanted_episodes)
                ]
                self.assertEqual(
                    out_of_scope, [],
                    f"Librarian created out-of-scope jobs: "
                    f"{[j.wanted_episodes for j in out_of_scope]} "
                    f"(spawn attempts: {self._spawn_attempts(session)})")
                # Nothing beyond season 1 may even exist as an active job.
                for job in self.service.store.get_jobs(JobStatus.ACTIVE):
                    self.assertEqual(set(job.wanted_episodes) - {"1"}, set())

    async def test_keep_current_mandate_spawns_only_the_new_episode(self) -> None:
        """With keep-current monitoring and one newly aired episode, the
        Librarian should spawn a job for exactly that episode."""
        for run in range(EVAL_RUNS):
            with self.subTest(run=run):
                yesterday = time.strftime(
                    "%Y-%m-%d", time.localtime(time.time() - 86400))
                details = self._fixture_tv_details()
                details["status"] = "Returning Series"
                details["in_production"] = True
                details["last_episode_to_air"] = {
                    "season_number": 4, "episode_number": 4,
                    "air_date": yesterday}
                self.tmdb_fixtures["/tv/1396"] = details
                self.tmdb_fixtures["/tv/1396/season/4"] = {"episodes": [
                    {"episode_number": e,
                     "air_date": yesterday if e == 4 else "2023-05-14",
                     "runtime": 47, "name": f"Episode {e}"}
                    for e in range(1, 5)
                ]}
                await self.storage.add_library_item(LibraryItem(
                    id="tv-1396", title="Fixture Crime Drama",
                    media_type=MediaType.TV,
                    path=str(self.root / "Library" / "Fixture Crime Drama"),
                    tmdb_id=1396,
                    episodes={"4": {str(e): {
                        "path": f"S04E{e:02d}.mkv", "verified": True,
                        "quality": "1080p", "added_at": time.time() - 86400,
                    } for e in range(1, 4)}},
                ))
                self.service.store.save_mandate(Mandate(
                    tmdb_id=1396, media_type="tv",
                    mode=MonitoringMode.KEEP_CURRENT,
                    requested_episodes={"4": [1, 2, 3]},
                    granted_at=time.time() - 30 * 86400))

                session = await self._run_librarian_pass()

                jobs = [j for j in self.service.store.get_jobs()
                        if j.tmdb_id == 1396]
                self.assertTrue(
                    jobs, "Librarian did not spawn a job for the newly aired "
                          f"episode (spawn attempts: {self._spawn_attempts(session)})")
                for job in jobs:
                    self.assertEqual(
                        {s: sorted(int(e) for e in eps)
                         for s, eps in job.wanted_episodes.items()},
                        {"4": [4]},
                        "Job scope must be exactly the newly aired episode")
                # Clean up for the next run.
                for job in jobs:
                    self.service.store.delete_job(job.id)


if __name__ == "__main__":
    unittest.main()
