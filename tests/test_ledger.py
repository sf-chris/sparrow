"""The cost ledger traces every model and tool call and answers what a run cost."""
import csv
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from backend.agents import ledger
from backend.agents.account_api import COOKIE, install_accounts
from backend.agents.models import AgentKind, AgentSession, Event, Job
from backend.agents.runtime import AgentRuntime, AgentSpec, ToolDef
from backend.agents.service import AgentService
from backend.agents.store import AgentStore
from backend.models import SparrowConfig
from backend.storage import Storage
from test_discovery import Block


def answer(name, args, tokens=1000):
    usage = SimpleNamespace(input_tokens=tokens, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(content=[Block(name, args)], usage=usage)


def downloads_table(store, *downloads):
    with store._connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS downloads(id TEXT PRIMARY KEY, data TEXT)")
        for download in downloads:
            db.execute("INSERT OR REPLACE INTO downloads VALUES(?,?)", (download["id"], json.dumps(download)))


class LedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AgentStore(self.temp.name)
        self.job = Job(title="Fixture Show", wanted_episodes={"1": [2]})
        self.store.save_job(self.job)
        self.session = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id, model="cheap-model")
        self.store.save_session(self.session)

        async def search(ctx, args):
            ctx.facts.setdefault("searches", []).append({"query": args["query"], "cached": False, "waited": 12.5, "rows": 40})
            return "Found 40."

        async def again(ctx, args):
            ctx.facts.setdefault("searches", []).append({"query": args["query"], "cached": True, "waited": 0, "rows": 40})
            return "Found 40 again."

        async def finish(ctx, args):
            ctx.close = True
            return "Done."

        self.spec = AgentSpec("fetch", lambda: "cheap-model", AsyncMock(return_value="Fixture"),
                              lambda _: [ToolDef("tpb_search", "Search", {"type": "object"}, search),
                                         ToolDef("cached_search", "Search again", {"type": "object"}, again),
                                         ToolDef("job_close", "Finish", {"type": "object"}, finish)], effort="low")
        self.runtime = AgentRuntime(self.store, lambda: "unused")
        self.runtime.register(self.spec)

    async def test_each_call_records_its_trigger_cost_and_the_tools_it_ran(self):
        replies = iter([answer("tpb_search", {"query": "Fixture Show S01E02"}),
                        answer("cached_search", {"query": "Fixture Show S01E02"}), answer("job_close", {})])
        self.runtime._call_api = AsyncMock(side_effect=lambda *_: next(replies))
        await self.runtime.wake(self.session.id, Event(kind="job_created", payload={"description": "New request."}))

        calls, tools, _, _ = ledger.load(self.store)
        self.assertEqual([c["phase"] for c in calls], ["scout_pick"] * 3)
        self.assertEqual(calls[0]["trigger"]["kinds"], ["job_created"])
        self.assertEqual([c["trigger"]["step"] for c in calls], [0, 1, 2])
        self.assertGreater(calls[0]["cost"], 0)
        self.assertEqual(calls[0]["input_tokens"], 1000)
        self.assertEqual(calls[0]["effort"], "low")
        self.assertEqual(calls[0]["rates"]["input"], 0.10)  # the rate table the cost was priced at
        self.assertEqual(calls[0]["tools"][0]["name"], "tpb_search")
        search = next(t for t in tools if t["name"] == "tpb_search")
        self.assertEqual(search["call_id"], calls[0]["id"])
        self.assertEqual(search["job_id"], self.job.id)
        self.assertEqual(search["outcome"], "waited")  # queued 12.5 s for an indexer slot
        self.assertEqual(search["facts"]["args"], {"query": "Fixture Show S01E02"})
        self.assertEqual(search["facts"]["searches"][0]["waited"], 12.5)
        self.assertEqual(next(t for t in tools if t["name"] == "cached_search")["outcome"], "cached")
        self.assertEqual(next(t for t in tools if t["name"] == "job_close")["outcome"], "ok")

    async def test_phases_tell_escalation_and_stalls_apart(self):
        self.session.model = "smart-model"  # not the model it started on: escalated
        self.store.save_session(self.session)
        self.runtime._call_api = AsyncMock(return_value=answer("job_close", {}))
        await self.runtime.wake(self.session.id, Event(kind="timer"))
        stalled = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id, model="cheap-model")
        self.store.save_session(stalled)
        await self.runtime.wake(stalled.id, Event(kind="download_stalled"))
        calls, _, _, _ = ledger.load(self.store)
        self.assertEqual([c["phase"] for c in calls], ["escalated_search", "stall"])
        self.assertEqual(ledger.phase_for("media"), "media_verify")
        self.assertEqual(ledger.phase_for("subtitle"), "subtitle_manager")
        self.assertEqual(ledger.phase_for("librarian"), "librarian")

    async def test_a_failed_model_call_is_recorded_with_no_cost_and_its_class(self):
        failure = RuntimeError("HTTP 400. No tool call found")
        failure.status_code = 400
        self.runtime._call_api = AsyncMock(side_effect=failure)
        await self.runtime.wake(self.session.id, Event(kind="timer"))
        calls, _, _, _ = ledger.load(self.store)
        self.assertEqual(len(calls), 1)
        self.assertIn("No tool call found", calls[0]["error"])
        self.assertEqual(calls[0]["error_class"], "http_4xx")
        self.assertEqual(calls[0]["cost"], 0)
        self.assertEqual(ledger.error_class(TimeoutError()), "timeout")
        self.assertEqual(ledger.error_class(SimpleNamespace(status_code=429)), "rate_limited")
        self.assertEqual(ledger.error_class(httpx.ConnectError("down")), "unreachable")

    async def test_the_fetch_agent_phase_follows_its_tools(self):
        storage = Storage(self.temp.name)
        await storage.load_all()
        await storage.save_config(SparrowConfig())
        service = AgentService(storage, self.temp.name, AsyncMock())
        phase = service.runtime._specs["fetch"].phase
        cheap = AgentSession(agent=AgentKind.FETCH, model="cheap-model")
        smart = AgentSession(agent=AgentKind.FETCH, model="smart-model")
        # Other tests load a developer .env; the roles here are fixed.
        with patch.dict(os.environ, {"SPARROW_SMART_MODEL": "smart-model", "SPARROW_CHEAP_MODEL": "cheap-model"}):
            self.assertEqual(phase(cheap, {"kinds": ["job_created"]}), "scout_pick")
            self.assertEqual(phase(smart, {"kinds": ["timer"]}), "escalated_search")
            self.assertEqual(phase(smart, {"kinds": ["download_stalled"]}), "stall")

    def test_the_report_sums_a_run_and_flags_waste(self):
        other = Job(title="Cheap Show", wanted_episodes={"1": [1]})
        self.store.save_job(other)
        for job, cost in ((self.job, 0.12), (other, 0.004)):
            call = ledger.record_call(self.store, session_id=f"s-{job.id}", job_id=job.id, agent="fetch", phase="scout_pick",
                                      model="cheap-model", cost=cost, trigger={"kinds": ["job_created"], "step": 0})
            ledger.record_tool(self.store, session_id=f"s-{job.id}", tool_id="t1", call_id=call, job_id=job.id, name="find_releases",
                               outcome="ok", facts={"searches": [{"query": "Q", "cached": False, "waited": 0, "rows": 3}] * 2,
                                                    "peeks": [{"id": "1", "listed": True, "wanted": 1}, {"id": "2", "listed": False}]})
        ledger.record_call(self.store, session_id="sub", job_id=other.id, agent="subtitle", phase="subtitle_check",
                           model="cheap-model", cost=0.02)
        ledger.record_call(self.store, session_id=f"s-{self.job.id}", job_id=self.job.id, agent="fetch", phase="pick_review",
                           model="smart-model", cost=0.003)
        ledger.record_tool(self.store, session_id=f"s-{self.job.id}", tool_id="t2", job_id=self.job.id, name="tpb_search",
                           outcome="rate_limited")
        ledger.record_tool(self.store, session_id=f"s-{self.job.id}", tool_id="t3", job_id=self.job.id, name="escalate_model",
                           outcome="ok", facts={"reason": "Nothing usable."})

        summary = ledger.summarize(self.store)
        stats = summary["stats"]
        self.assertEqual(stats["titles"], 2)
        self.assertAlmostEqual(stats["search_total"], 0.127)
        self.assertEqual(stats["escalation_rate"], 0.5)
        self.assertAlmostEqual(stats["subtitle_total"], 0.02)
        self.assertEqual(stats["search_by_phase"], {"scout_pick": 0.124, "pick_review": 0.003})
        costly = next(t for t in summary["titles"] if t["job_id"] == self.job.id)
        self.assertEqual(costly["peeks"], {"listed": 1, "unlisted": 1})
        flags = " ".join(costly["flags"])
        for expected in ("above the 10¢", "escalated", "more than once", "rate limit"):
            self.assertIn(expected, flags)
        text = ledger.report(summary)
        self.assertIn("Fixture Show", text)
        self.assertIn("median", text)
        self.assertIn("Search cost by phase: scout_pick $0.1240, pick_review $0.0030", text)
        self.assertIn("Top outliers", text)
        self.assertIn("Fixture Show S01E02: finding it cost 12¢. Target 10¢.", text)
        self.assertIn("- Cheap Show S01E01: $0.0040 (scout_pick $0.0040)", text)  # subtitles are not search

    def test_each_pick_is_followed_to_what_came_of_it(self):
        now = time.time()
        jobs = [Job(title=f"Show {n}", wanted_episodes={"1": [n]}) for n in (1, 2, 3)]
        for job in jobs:
            self.store.save_job(job)
        approved, replaced, vetoed = jobs

        def pick(job, tool_id, ts, **facts):
            ledger.record_call(self.store, session_id=job.id, job_id=job.id, agent="fetch", phase="scout_pick", cost=0.001, ts=ts)
            ledger.record_tool(self.store, session_id=job.id, tool_id=tool_id, job_id=job.id, name="propose_release", ts=ts,
                               outcome="ok", facts={"args": {"release": "r1", "reason": "best seeded"}, "pick": "r1",
                                                    "reviewer": "smart-model", "review_cost": 0.002, **facts})

        pick(approved, "a", now, name="Show 1 S01E01 1080p", info_hash="a" * 40, approved=True, download_id="dl-a")
        pick(replaced, "b", now, name="Show 2 S01E02 1080p", approved=True, download_id="dl-b")
        pick(vetoed, "c", now, name="Show 3 S01E03 1080p", info_hash="c" * 40, approved=False, reason="Wrong season.")
        # After escalating, the smart model downloads the vetoed release anyway, and it is fine.
        ledger.record_tool(self.store, session_id=vetoed.id, tool_id="d", job_id=vetoed.id, name="client_add", ts=now + 60,
                           outcome="ok", facts={"download_id": "dl-c"})
        downloads_table(
            self.store,
            {"id": "dl-a", "name": "Show 1 S01E01 1080p", "status": "organized", "torrent_hash": "a" * 40,
             "added_at": now, "completed_at": now + 600, "metadata": {"job_id": approved.id}},
            {"id": "dl-b", "name": "Show 2 S01E02 1080p", "status": "error", "torrent_hash": "b" * 40,
             "added_at": now, "metadata": {"job_id": replaced.id}},
            {"id": "dl-b2", "name": "Show 2 S01E02 720p", "status": "organized", "torrent_hash": "d" * 40,
             "added_at": now + 30, "completed_at": now + 900, "metadata": {"job_id": replaced.id}},
            {"id": "dl-c", "name": "Show 3 S01E03 1080p", "status": "organized", "torrent_hash": "c" * 40,
             "added_at": now + 60, "completed_at": now + 700, "metadata": {"job_id": vetoed.id}},
        )
        summary = ledger.summarize(self.store)
        by_job = {t["job_id"]: t for t in summary["titles"]}
        self.assertEqual([d["result"] for d in by_job[approved.id]["decisions"]], ["library"])
        self.assertEqual(by_job[approved.id]["decisions"][0]["picker_reason"], "best seeded")
        self.assertEqual([d["result"] for d in by_job[replaced.id]["decisions"]], ["replaced"])
        veto = by_job[vetoed.id]["decisions"][0]
        self.assertEqual((veto["verdict"], veto["later_chosen"], veto["result"]), ("vetoed", True, "library"))
        self.assertIn("false veto", " ".join(by_job[vetoed.id]["flags"]))
        self.assertIn("the review missed it", " ".join(by_job[replaced.id]["flags"]))
        reviews = summary["stats"]["reviews"]
        self.assertEqual((reviews["reviews"], reviews["approved"], reviews["vetoed"]), (3, 2, 1))
        self.assertEqual((reviews["approved_then_abandoned"], reviews["false_vetoes"]), (1, 1))
        self.assertAlmostEqual(reviews["cost"], 0.006)
        # Time from request to a chosen download, and to the library.
        created = approved.created_at
        self.assertEqual(by_job[approved.id]["time_to_download"], round(now - created))
        self.assertEqual(by_job[approved.id]["time_to_library"], round(now + 600 - created))
        self.assertIn("vetoed then chosen anyway 1 (false vetoes 1)", ledger.report(summary))

    def test_a_tagged_run_is_reported_on_its_own(self):
        other = Job(title="Other Run", wanted_episodes={"1": [1]})
        self.store.save_job(other)
        for job in (self.job, other):
            ledger.record_call(self.store, session_id=job.id, job_id=job.id, agent="fetch", phase="scout_pick", cost=0.01)
        self.assertEqual(ledger.tag_batch(self.store, "bench-1", [self.job.id]), 1)
        self.assertEqual(ledger.tag_batch(self.store, "bench-1", [self.job.id]), 0)
        self.assertEqual(ledger.batches(self.store), {"bench-1": [self.job.id]})
        titles = ledger.summarize(self.store, batch="bench-1")["titles"]
        self.assertEqual([t["title"] for t in titles], ["Fixture Show"])
        out = io.StringIO()
        with redirect_stdout(out):
            ledger.main(["tag", "--data", self.temp.name, "--batch", "bench-2", "--jobs", other.id])
            ledger.main(["report", "--data", self.temp.name, "--batch", "bench-2", "--json"])
        report = json.loads(out.getvalue().split("\n", 1)[1])
        self.assertEqual([t["title"] for t in report["titles"]], ["Other Run"])

    def test_the_export_has_a_stable_schema_in_jsonl_and_csv(self):
        call = ledger.record_call(self.store, session_id="s", job_id=self.job.id, agent="fetch", phase="scout_pick",
                                  model="cheap-model", cost=0.001, trigger={"kinds": ["timer"], "step": 0})
        ledger.record_tool(self.store, session_id="s", tool_id="t", call_id=call, job_id=self.job.id, name="find_releases",
                           outcome="ok", facts={"kept": 3})
        rows = [json.loads(line) for line in ledger.export_text(self.store, "jsonl").splitlines()]
        self.assertEqual([r["kind"] for r in rows], ["call", "tool"])
        self.assertEqual(set(rows[0]), {"schema", "kind", *ledger.CALL_FIELDS})
        self.assertEqual(set(rows[1]), {"schema", "kind", *ledger.TOOL_FIELDS})
        self.assertEqual(rows[0]["schema"], ledger.EXPORT_SCHEMA)
        table = list(csv.DictReader(io.StringIO(ledger.export_text(self.store, "csv"))))
        self.assertEqual([r["kind"] for r in table], ["call", "tool"])
        self.assertEqual(json.loads(table[1]["facts"]), {"kept": 3})
        self.assertEqual(table[1]["call_id"], call)

    def test_alerts_name_titles_and_runs_over_target_and_never_releases(self):
        for n, cost in enumerate((0.001, 0.003, 0.03, 0.04, 0.15)):
            job = Job(title=f"Show {n}", wanted_episodes={"1": [1]})
            self.store.save_job(job)
            ledger.record_call(self.store, session_id=job.id, job_id=job.id, agent="fetch", phase="scout_pick", cost=cost)
            if n >= 3:
                ledger.record_tool(self.store, session_id=job.id, tool_id="e", job_id=job.id, name="escalate_model",
                                   outcome="ok", facts={"reason": "nothing usable"})
        downloads_table(self.store, {"id": "dl", "name": "Show.4.S01E01.1080p.WEB-DL.x264-GROUP", "status": "downloading",
                                     "metadata": {"job_id": job.id}})
        summary = ledger.summarize(self.store)
        found = ledger.alerts(summary)
        self.assertEqual([a["measure"] for a in found], ["search_cost", "search_median", "search_p90", "escalation_rate"])
        self.assertEqual(found[0]["text"], "Show 4 S01E01: finding it cost 15¢. Target 10¢.")
        self.assertEqual(found[1]["text"], "This run: median cost to find a title 3¢. Target 2¢.")
        self.assertIn("2 of 5 titles were handed to the smart model (40%). Target 15%.", found[3]["text"])
        view = ledger.spending(self.store)
        self.assertEqual(view["titles"], 5)
        self.assertEqual([a["text"] for a in view["alerts"]], [
            "Show 4 S01E01: finding it cost 15¢. Target 10¢.",
            "Last 30 days (5 titles): median 3¢, target 2¢ · 90th percentile 15¢, target 10¢ · 40% escalated, target 15%.",
        ])
        self.assertNotIn("GROUP", json.dumps(view))  # no release names on the requests page
        # Fewer titles than a run: only the title's own target applies.
        single = ledger.summarize(self.store, job=job.id)
        self.assertEqual([a["measure"] for a in ledger.alerts(single)], ["search_cost"])

    def test_old_ledgers_gain_the_new_columns(self):
        with sqlite3.connect(self.store._db_path) as db:
            db.executescript("""
                CREATE TABLE ledger_calls(id TEXT PRIMARY KEY, ts REAL NOT NULL, session_id TEXT NOT NULL, job_id TEXT,
                    user_id TEXT, agent TEXT, phase TEXT NOT NULL, model TEXT, input_tokens INTEGER, cached_tokens INTEGER,
                    cache_write_tokens INTEGER, output_tokens INTEGER, cost REAL NOT NULL DEFAULT 0, latency REAL,
                    trigger TEXT, tools TEXT, error TEXT, source TEXT NOT NULL DEFAULT 'live');
                INSERT INTO ledger_calls(id, ts, session_id, job_id, phase, cost) VALUES('old', 1.0, 's', '', 'fetch', 0.05);
            """)
        ledger._READY.discard(self.store._db_path)
        ledger.record_call(self.store, session_id="s", phase="scout_pick", cost=0.01, effort="low", rates={"input": 1})
        calls, _, _, _ = ledger.load(self.store)
        self.assertEqual([(c["phase"], c["effort"]) for c in calls], [("fetch", None), ("scout_pick", "low")])

    def test_keys_never_reach_the_ledger(self):
        ledger.record_tool(self.store, session_id="s", tool_id="t", name="tmdb_get", outcome="ok",
                           facts={"args": {"api_key": "abc", "query": "Show"}, "note": "used sk-0123456789abcdefABCDEF"})
        _, tools, _, _ = ledger.load(self.store)
        self.assertEqual(tools[0]["facts"]["args"], {"api_key": "[redacted]", "query": "Show"})
        self.assertEqual(tools[0]["facts"]["note"], "used [redacted]")

    def test_old_runs_are_backfilled_from_their_spend_entries(self):
        self.session.spend.entries = [{"ts": 1.0, "model": "smart-model", "cost": 0.03, "input_tokens": 9000},
                                      {"ts": 2.0, "model": "smart-model", "cost": 0.002, "purpose": "pick review"}]
        self.store.save_session(self.session)
        self.assertEqual(ledger.backfill(self.store), 2)
        self.assertEqual(ledger.backfill(self.store), 0)  # once only
        calls, _, _, _ = ledger.load(self.store)
        self.assertEqual(sorted(c["phase"] for c in calls), ["fetch", "pick_review"])
        self.assertTrue(all(c["source"] == "backfill" for c in calls))

    def test_subtitle_spend_is_attributed_to_its_title(self):
        self.job.tmdb_id = 77
        self.store.save_job(self.job)
        subtitle = AgentSession(agent=AgentKind.SUBTITLE, budget_scope="subtitle:task-1")
        self.store.save_session(subtitle)
        with self.store._connect() as db:
            db.execute("CREATE TABLE subtitle_tasks(id TEXT, asset_id TEXT)")
            db.execute("CREATE TABLE assets(id TEXT, item_id TEXT)")
            db.execute("CREATE TABLE library_items(id TEXT, data TEXT)")
            db.execute("INSERT INTO subtitle_tasks VALUES('task-1','asset-1')")
            db.execute("INSERT INTO assets VALUES('asset-1','item-1')")
            db.execute("INSERT INTO library_items VALUES('item-1',?)", (json.dumps({"tmdb_id": 77}),))
        ledger.record_call(self.store, session_id=subtitle.id, agent="subtitle", phase="subtitle_check", cost=0.02)
        title = next(t for t in ledger.summarize(self.store)["titles"] if t["job_id"] == self.job.id)
        self.assertAlmostEqual(title["subtitle_cost"], 0.02)

    def test_long_facts_stay_valid_json(self):
        ledger.record_tool(self.store, session_id="s", tool_id="t", name="memory_write", outcome="ok",
                           facts={"args": {"content": "x" * 50_000}, "rows": list(range(500))})
        _, tools, _, _ = ledger.load(self.store)
        self.assertLess(len(tools[0]["facts"]["args"]["content"]), 500)
        self.assertEqual(len(tools[0]["facts"]["rows"]), 50)


class LedgerApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Storage(self.temp.name)
        await self.storage.load_all()
        service = SimpleNamespace(store=AgentStore(self.temp.name))
        self.store = service.store
        app = FastAPI()
        self.accounts = install_accounts(app, self.storage, lambda: service)
        ledger.install_ledger(app, lambda: service)
        self.owner = self.accounts.create_user("owner", "fixture-password-123", "Owner", bootstrap=True)
        self.viewer = self.accounts.create_user("viewer", "fixture-password-456", "Viewer",
                                                invitation=self.accounts.invite("viewer", []))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                        cookies={COOKIE: self.accounts.new_session(self.owner["id"])})
        self.addAsyncCleanup(self.client.aclose)
        job = Job(title="Costly Show", wanted_episodes={"1": [3]})
        self.store.save_job(job)
        ledger.record_call(self.store, session_id="s", job_id=job.id, agent="fetch", phase="escalated_search",
                           model="smart-model", cost=0.21)
        ledger.record_tool(self.store, session_id="s", tool_id="t", job_id=job.id, name="propose_release", outcome="ok",
                           facts={"pick": "r1", "name": "Costly.Show.S01E03.1080p-GROUP", "approved": False})

    async def test_administrators_read_the_ledger_and_its_export(self):
        summary = (await self.client.get("/api/ledger")).json()
        self.assertEqual(summary["stats"]["titles"], 1)
        self.assertEqual(summary["titles"][0]["decisions"][0]["name"], "Costly.Show.S01E03.1080p-GROUP")
        self.assertEqual(summary["alerts"][0]["text"], "Costly Show S01E03: finding it cost 21¢. Target 10¢.")
        self.assertEqual(summary["targets"], ledger.TARGETS)
        exported = await self.client.get("/api/ledger/export", params={"format": "csv"})
        self.assertEqual(exported.status_code, 200)
        self.assertIn("text/csv", exported.headers["content-type"])
        self.assertEqual(len(list(csv.DictReader(io.StringIO(exported.text)))), 2)
        spending = (await self.client.get("/api/v1/admin/spending")).json()
        self.assertEqual([a["text"] for a in spending["alerts"]], ["Costly Show S01E03: finding it cost 21¢. Target 10¢."])
        self.assertNotIn("GROUP", json.dumps(spending))

    async def test_other_people_cannot(self):
        self.client.cookies.set(COOKIE, self.accounts.new_session(self.viewer["id"]))
        for path in ("/api/ledger", "/api/ledger/export", "/api/v1/admin/spending"):
            self.assertEqual((await self.client.get(path)).status_code, 403, path)


if __name__ == "__main__":
    unittest.main()
