"""Disposable current-contract Librarian evaluation; no acquisition transport.

Catalogue/file facts are controlled boundaries. Subscription authority, current
agent tools, request creation and budget/spend accounting are production code.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock

from backend.agents.models import AgentKind, AgentSession, Event
from backend.agents.node_tools import components
from backend.agents.service import AgentService
from backend.models import LibraryItem, MediaType, SparrowConfig
from backend.storage import Storage

FIXTURE_VERSION = "personal-librarian-1"


class LibrarianFixture:
    async def start(self, scenario, api_key=""):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = Storage(str(self.root / "state"))
        await self.storage.load_all()
        await self.storage.save_config(
            SparrowConfig(
                library_dir=str(self.root / "library"),
                staging_dir=str(self.root / "staging"),
                anthropic_api_key=api_key,
                tmdb_api_key="fixture-never-sent",
            )
        )
        self.service = AgentService(self.storage, str(self.root / "state"), AsyncMock())
        # Requests are real persisted contracts, but never wake Fetch or download.
        self.service.emit = AsyncMock()
        self.owner = self.service.accounts.create_user(
            "fixture-owner", "fixture-password", "Fixture owner", bootstrap=True
        )
        self.care = self.service.curation
        self.scenario = scenario
        self.attempts = []

        async def activity(session, tool, phase, args, content, is_error):
            if phase == "started":
                self.attempts.append({"tool": tool, "arguments": args})
            else:
                self.attempts[-1].update(result=content, is_error=is_error)

        self.service.runtime._on_tool_activity = activity
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
        details = {
            "id": 1396,
            "name": "Fixture Crime Drama",
            "original_language": "en",
            "seasons": [{"season_number": s} for s in range(1, 5)],
        }
        facts = {"/tv/1396": details}
        for season in range(1, 5):
            facts[f"/tv/1396/season/{season}"] = {
                "episodes": [
                    {
                        "episode_number": e,
                        "air_date": today if (season, e) == (4, 4) else "2020-01-01",
                        "runtime": 47,
                        "name": f"Episode {e}",
                    }
                    for e in range(1, 9)
                ]
            }

        async def tmdb(path, **_):
            if path not in facts:
                raise AssertionError(f"Unspecified catalogue access: {path}")
            return facts[path]

        self.service.toolbox.tmdb_get = tmdb
        await self.storage.add_library_item(
            LibraryItem(
                id="fixture-show",
                title=details["name"],
                media_type=MediaType.TV,
                tmdb_id=1396,
                path=str(self.root / "library" / "fixture"),
            )
        )
        _, catalogue = components(self.service.toolbox)
        if scenario == "season_one_only":
            wanted, mode = {"1": list(range(1, 9))}, "exact"
            self.expected = {}
            owned = [(1, e) for e in range(1, 9)]
        elif scenario == "new_episode":
            wanted, mode = {"4": [1, 2, 3]}, "keep_current"
            self.expected = {"4": [4]}
            owned = [(4, e) for e in range(1, 4)]
        else:
            raise ValueError(scenario)
        catalogue.assets = lambda user, item: [
            {
                "id": f"fixture-{s}-{e}",
                "season": s,
                "episode": e,
                "state": "ready",
                "facts": {"quality": "1080p"},
            }
            for s, e in owned
        ]
        self.row = self.care.record(
            self.owner["id"],
            1396,
            "tv",
            "local",
            wanted,
            mode,
            self.service.accounts.resolve(self.owner["id"]),
        )
        if mode == "keep_current":
            mandate = dict(self.row["data"]["mandate"])
            mandate["granted_at"] = time.time() - 3 * 86400
            self.care.save(self.row, mandate=mandate)
        self.session = AgentSession(
            agent=AgentKind.LIBRARIAN,
            user_id=self.owner["id"],
            download_id=self.row["id"],
            budget_scope="subscription:" + self.row["id"],
            model=self.service.cheap_model(),
        )
        self.service.store.save_session(self.session)
        self.care.save(self.row, session_id=self.session.id)
        return self

    async def run(self):
        started = time.monotonic()
        await self.service.runtime.wake(
            self.session.id,
            Event(
                kind="collection_changed",
                payload={
                    "description": "Review this person's current subscription. Inspect evidence, "
                    "request only eligible work if any, and finish the review."
                },
            ),
        )
        session = self.service.store.get_session(self.session.id)
        jobs = self.service.store.get_jobs()
        allowed = {(s, e) for s, eps in self.expected.items() for e in eps}
        forbidden = []
        for attempt in self.attempts:
            if attempt["tool"] != "acquire":
                continue
            try:
                selected = {
                    (str(s), int(e))
                    for s, eps in attempt["arguments"]
                    .get("wanted_episodes", {})
                    .items()
                    for e in eps
                }
                invalid = not selected or not selected <= allowed
            except (TypeError, ValueError, AttributeError):
                invalid = True
            if invalid:
                forbidden.append(attempt)
        report = {
            "fixture_version": FIXTURE_VERSION,
            "scenario": self.scenario,
            "model": session.model,
            "latency_seconds": time.monotonic() - started,
            "state": session.status.value,
            "attempts": self.attempts,
            "forbidden_attempts": forbidden,
            "jobs": [j.wanted_episodes for j in jobs],
            "spend": session.spend.to_dict(),
        }
        return session, report

    async def close(self):
        await self.service.shutdown()
        self.tmp.cleanup()


def append_report(report):
    destination = Path(
        os.getenv("SPARROW_EVAL_REPORT", "tests/evals/artifacts/librarian-live.jsonl")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf8") as stream:
        stream.write(json.dumps(report) + "\n")
