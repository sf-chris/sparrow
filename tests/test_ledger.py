"""The cost ledger traces every model and tool call and answers what a run cost."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.agents import ledger
from backend.agents.models import AgentKind, AgentSession, Event, Job
from backend.agents.runtime import AgentRuntime, AgentSpec, ToolDef
from backend.agents.store import AgentStore
from test_discovery import Block


def answer(name, args, tokens=1000):
    usage = SimpleNamespace(input_tokens=tokens, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(content=[Block(name, args)], usage=usage)


class LedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AgentStore(self.temp.name)
        self.job = Job(title="Fixture Show", wanted_episodes={"1": [2]})
        self.store.save_job(self.job)
        self.session = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id, model="gpt-6-luna")
        self.store.save_session(self.session)

        async def search(ctx, args):
            ctx.facts.setdefault("searches", []).append({"query": args["query"], "cached": False, "waited": 12.5, "rows": 40})
            return "Found 40."

        async def finish(ctx, args):
            ctx.close = True
            return "Done."

        spec = AgentSpec("fetch", lambda: "gpt-6-luna", AsyncMock(return_value="Fixture"),
                         lambda _: [ToolDef("tpb_search", "Search", {"type": "object"}, search),
                                    ToolDef("job_close", "Finish", {"type": "object"}, finish)])
        self.runtime = AgentRuntime(self.store, lambda: "unused")
        self.runtime.register(spec)

    async def test_each_call_records_its_trigger_cost_and_the_tools_it_ran(self):
        replies = iter([answer("tpb_search", {"query": "Fixture Show S01E02"}), answer("job_close", {})])
        self.runtime._call_api = AsyncMock(side_effect=lambda *_: next(replies))
        await self.runtime.wake(self.session.id, Event(kind="job_created", payload={"description": "New request."}))

        calls, tools, _, _ = ledger.load(self.store)
        self.assertEqual([c["phase"] for c in calls], ["fetch", "fetch"])
        self.assertEqual(calls[0]["trigger"]["kinds"], ["job_created"])
        self.assertEqual([c["trigger"]["step"] for c in calls], [0, 1])
        self.assertGreater(calls[0]["cost"], 0)
        self.assertEqual(calls[0]["input_tokens"], 1000)
        self.assertEqual(calls[0]["tools"][0]["name"], "tpb_search")
        search = next(t for t in tools if t["name"] == "tpb_search")
        self.assertEqual(search["call_id"], calls[0]["id"])
        self.assertEqual(search["job_id"], self.job.id)
        self.assertEqual(search["outcome"], "ok")
        self.assertEqual(search["facts"]["args"], {"query": "Fixture Show S01E02"})
        self.assertEqual(search["facts"]["searches"][0]["waited"], 12.5)

    async def test_a_failed_model_call_is_recorded_with_no_cost(self):
        self.runtime._call_api = AsyncMock(side_effect=RuntimeError("HTTP 400. No tool call found"))
        await self.runtime.wake(self.session.id, Event(kind="timer"))
        calls, _, _, _ = ledger.load(self.store)
        self.assertEqual(len(calls), 1)
        self.assertIn("No tool call found", calls[0]["error"])
        self.assertEqual(calls[0]["cost"], 0)

    def test_the_report_sums_a_run_and_flags_waste(self):
        other = Job(title="Cheap Show", wanted_episodes={"1": [1]})
        self.store.save_job(other)
        for job, cost in ((self.job, 0.12), (other, 0.004)):
            call = ledger.record_call(self.store, session_id=f"s-{job.id}", job_id=job.id, agent="fetch", phase="fetch",
                                      model="gpt-6-luna", cost=cost, trigger={"kinds": ["job_created"], "step": 0})
            ledger.record_tool(self.store, session_id=f"s-{job.id}", tool_id="t1", call_id=call, job_id=job.id, name="find_releases",
                               outcome="ok", facts={"searches": [{"query": "Q", "cached": False, "waited": 0, "rows": 3}] * 2})
        ledger.record_call(self.store, session_id="sub", job_id=other.id, agent="subtitle", phase="subtitle_check",
                           model="gpt-6-luna", cost=0.02)
        ledger.record_tool(self.store, session_id=f"s-{self.job.id}", tool_id="t2", job_id=self.job.id, name="tpb_search",
                           outcome="rate_limited")
        ledger.record_tool(self.store, session_id=f"s-{self.job.id}", tool_id="t3", job_id=self.job.id, name="escalate_model",
                           outcome="ok", facts={"reason": "Nothing usable."})

        summary = ledger.summarize(self.store)
        stats = summary["stats"]
        self.assertEqual(stats["titles"], 2)
        self.assertAlmostEqual(stats["search_total"], 0.124)
        self.assertEqual(stats["escalation_rate"], 0.5)
        self.assertAlmostEqual(stats["subtitle_total"], 0.02)
        costly = next(t for t in summary["titles"] if t["job_id"] == self.job.id)
        flags = " ".join(costly["flags"])
        for expected in ("above the 10¢", "escalated", "more than once", "rate limit"):
            self.assertIn(expected, flags)
        text = ledger.report(summary)
        self.assertIn("Fixture Show", text)
        self.assertIn("median", text)

    def test_old_runs_are_backfilled_from_their_spend_entries(self):
        self.session.spend.entries = [{"ts": 1.0, "model": "gpt-6-sol", "cost": 0.03, "input_tokens": 9000},
                                      {"ts": 2.0, "model": "gpt-6-sol", "cost": 0.002, "purpose": "pick review"}]
        self.store.save_session(self.session)
        self.assertEqual(ledger.backfill(self.store), 2)
        self.assertEqual(ledger.backfill(self.store), 0)  # once only
        calls, _, _, _ = ledger.load(self.store)
        self.assertEqual(sorted(c["phase"] for c in calls), ["fetch", "pick_review"])
        self.assertTrue(all(c["source"] == "backfill" for c in calls))

    def test_subtitle_spend_is_attributed_to_its_title(self):
        import json

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


if __name__ == "__main__":
    unittest.main()
