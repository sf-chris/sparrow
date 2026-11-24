"""Bounded escalation (issue #3 P4, the 2026-11-22 ledger audit): an escalated
search spends a wave allowance and needs a new candidate or a recorded
identity conflict for another wave; a request's repeated searches come from
the cache; turns that only take notes stop costing timer wakes. Each
guardrail is a ledger row."""
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.agents import escalation, ledger, profiles
from backend.agents.models import AgentKind, AgentSession, CaseState, Event, Job, SessionStatus
from backend.agents.runtime import ToolCtx, ToolError
from backend.agents.service import AgentService
from backend.agents.tools import SEARCH_REPEAT_TTL, search_key
from backend.models import SparrowConfig
from backend.storage import Storage
from test_discovery import Block
from test_profiles import environment

PLAUSIBLE = {"id": "71", "name": "Fixture Show S01E02 1080p WEB-DL", "info_hash": "b" * 40, "seeders": "60",
             "leechers": "2", "size": str(2 * 10**9), "num_files": "1", "added": "1700000000", "username": "x"}
JUNK = {"id": "72", "name": "Other Programme 2019 Documentary", "info_hash": "c" * 40, "seeders": "9",
        "leechers": "0", "size": str(10**9), "num_files": "1", "added": "1700000000", "username": "x"}


def answer(*calls, tokens=8000):
    usage = SimpleNamespace(input_tokens=tokens, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(content=[Block(name, args) for name, args in calls], usage=usage)


class Fixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # These cases measure a dollar-bounded wave; the call-bounded default has its own test.
        self.env = environment(SPARROW_SMART_MODEL="glm-5.3", SPARROW_CHEAP_MODEL="glm-5.3-flash",
                               SPARROW_PROFILES='{"fetch_escalation": {"wave_calls": 0, "wave_dollars": 0.02}}')
        self.env.__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.storage = Storage(self.temp.name)
        await self.storage.load_all()
        await self.storage.save_config(SparrowConfig(tmdb_api_key="fixture-not-sent"))
        self.service = AgentService(self.storage, self.temp.name, AsyncMock())
        self.runtime, self.tb = self.service.runtime, self.service.toolbox
        self.tb.SEARCH_SPACING = 0
        self.tb.tmdb_get = AsyncMock(return_value={"episodes": [{"episode_number": 2, "name": "Two"}]})
        self.job = Job(tmdb_id=42, title="Fixture Show", year=2020, wanted_episodes={"1": [2]})
        self.session = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id, model="glm-5.3-flash")
        self.job.session_id = self.session.id
        self.service.store.save_job(self.job)
        self.service.store.save_session(self.session)
        self.index = AsyncMock(return_value=[JUNK])
        patcher = patch("backend.agents.tools.apibay_query", self.index)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tool(self, name):
        return next(t for t in self.runtime.tools_for(self.session) if t.name == name).handler

    def paid(self, cost, call):
        """A smart-model call that has just been paid for and is running tools."""
        self.session.spend.entries.append({"ts": time.time(), "model": self.session.model, "cost": cost})
        self.session.spend.dollars = round(self.session.spend.dollars + cost, 6)
        self.runtime._current_call[self.session.id] = call


class EscalationBoundTests(Fixture):
    async def test_waves_need_a_new_candidate_or_a_recorded_conflict(self):
        self.tb.scouted[self.session.id] = {"rows": {"r1": {"info_hash": "a" * 40}}, "titles": ["Fixture Show"], "exclude": []}
        ctx = ToolCtx(self.session, self.runtime)
        text = await self.tool("escalate_model")(ctx, {"reason": "Nothing usable."})
        self.assertEqual(self.session.model, "glm-5.3")
        self.assertIn("$0.02 of smart-model calls per wave", text)
        state = self.session.controls["escalation"]
        self.assertEqual((state["wave"], state["known"]), (1, ["a" * 40]))
        self.assertEqual(ctx.facts["enforced"][0]["rule"], "escalation_wave")
        names = {t.name for t in self.runtime.tools_for(self.session)}
        self.assertIn("record_identity_conflict", names)
        search = self.tool("tpb_search")

        self.paid(0.015, "c1")
        await search(ctx, {"query": "Fixture Show S01"})  # the call asking is paid: the cap governs what follows
        self.paid(0.015, "c2")
        await search(ctx, {"query": "Fixture Show Season 1"})  # 1.5¢ of 2¢ spent before this call
        self.paid(0.015, "c3")
        ctx.facts = {}
        with self.assertRaisesRegex(ToolError, r"spent \$0\.030 of its \$0\.02 allowance and found no new candidate"):
            await search(ctx, {"query": "Fixture Show complete"})
        self.assertEqual(ctx.facts["enforced"][0]["rule"], "escalation_bound")
        self.assertFalse(ctx.hibernate)
        with self.assertRaises(ToolError):
            await search(ctx, {"query": "Fixture Show 1080p"})  # the same call's batch: still one refused call
        self.assertFalse(ctx.hibernate)
        self.assertEqual(self.index.await_count, 2)

        # An identity conflict opens the next wave.
        with self.assertRaisesRegex(ToolError, "what disagrees"):
            await self.tool("record_identity_conflict")(ctx, {"conflict": "numbering", "evidence": ""})
        reply = await self.tool("record_identity_conflict")(ctx, {
            "conflict": "TMDB calls episode 2 'Two' but the release numbering may be off by one",
            "evidence": "The S01 pack lists 'Fixture Show - 03 - Two.mkv'."})
        self.assertIn("Search wave 2 of 3 is open", reply)
        with self.assertRaisesRegex(ToolError, "already recorded"):
            await self.tool("record_identity_conflict")(ctx, {
                "conflict": "TMDB calls episode 2 'Two' but the release numbering may be off by one",
                "evidence": "The same pack listing as before."})
        self.paid(0.015, "c4")
        self.index.return_value = [PLAUSIBLE]
        await search(ctx, {"query": "Fixture Show 02"})  # wave 2 searches, and finds a copy nobody had seen
        self.assertEqual(state["found"], ["b" * 40])
        self.paid(0.03, "c5a")  # an earlier call of this wave
        self.paid(0.01, "c5")
        ctx.facts = {}
        await search(ctx, {"query": "Fixture Show 2020"})  # spent, but the new candidate opens wave 3
        self.assertEqual(ctx.facts["enforced"][0]["detail"]["reason"], "new_candidate")
        self.assertEqual((state["wave"], state["found"]), (3, []))
        self.assertIn("b" * 40, state["known"])  # the next wave needs a copy newer still

        self.paid(0.03, "c6a")
        self.paid(0.01, "c6")
        with self.assertRaisesRegex(ToolError, "all 3 search waves are used"):
            await search(ctx, {"query": "Fixture Show WEB"})  # 1 refused call in wave 3
        self.paid(0.01, "c7")
        ctx.facts = {}
        with self.assertRaisesRegex(ToolError, "This turn has ended"):
            await search(ctx, {"query": "Fixture Show x265"})
        self.assertTrue(ctx.hibernate)
        self.assertTrue(ctx.facts["enforced"][0]["detail"]["ended_turn"])
        self.assertEqual((self.session.outcome, self.session.wake_at), (CaseState.NEEDS_INPUT, 0.0))
        self.assertEqual(self.service.store.get_job(self.job.id).state_line, escalation.PAUSED)

        # The person asking for a check starts the count again.
        records = escalation.woken(self.runtime, self.session, [Event(kind="nudge")])
        self.assertEqual((state["wave"], records[0]["detail"]["reason"]), (1, "user_check"))
        self.paid(0.015, "c8")
        await search(ctx, {"query": "Fixture Show S01E02"})

    async def test_the_runtime_ends_a_turn_of_refused_searches_and_the_ledger_shows_why(self):
        replies = iter([
            answer(("escalate_model", {"reason": "Nothing usable."}), tokens=1000),
            answer(("tpb_search", {"query": "q one"})),
            answer(("tpb_search", {"query": "q two"})),
            answer(("tpb_search", {"query": "q three"})),
            answer(("tpb_search", {"query": "q four"})),
            answer(("wake_me", {"reason": "Must not be reached."})),
        ])
        self.runtime._call_api = AsyncMock(side_effect=lambda *_: next(replies))
        await self.runtime.wake(self.session.id, Event(kind="job_created", payload={"description": "New request."}))

        self.assertEqual(self.runtime._call_api.await_count, 5)  # the second refused call ended the turn
        self.assertEqual(self.index.await_count, 2)
        session = self.service.store.get_session(self.session.id)
        self.assertEqual((session.status, session.outcome), (SessionStatus.HIBERNATING, CaseState.NEEDS_INPUT))
        rows = ledger.load_enforcements(self.service.store)
        self.assertEqual([r["rule"] for r in rows], ["escalation_wave", "escalation_bound", "escalation_bound"])
        self.assertEqual([r["profile"] for r in rows], ["fetch_escalation"] * 3)
        self.assertTrue(rows[-1]["detail"]["ended_turn"])
        summary = ledger.summarize(self.service.store)
        self.assertEqual(summary["stats"]["enforcements"], {"escalation_wave": 1, "escalation_bound": 2})
        self.assertIn("Guardrails: escalation_bound 2, escalation_wave 1", ledger.report(summary))
        self.assertIn("GUARDRAIL escalation_bound (tpb_search)", ledger.timelines(self.service.store))
        exported = [json.loads(line) for line in ledger.export_text(self.service.store).splitlines()]
        self.assertEqual(sum(1 for r in exported if r["kind"] == "enforcement"), 3)

    async def test_the_scout_off_mode_has_no_wave_bound(self):
        with patch.dict(os.environ, {"SPARROW_FETCH_SCOUT": "off"}):
            self.session.model = "glm-5.3"
            ctx = ToolCtx(self.session, self.runtime)
            self.assertIsNone(escalation.bound(self.runtime, self.session))
            self.assertNotIn("record_identity_conflict", {t.name for t in self.runtime.tools_for(self.session)})
            for n in range(4):
                self.paid(0.05, f"c{n}")
                await self.tool("tpb_search")(ctx, {"query": f"Fixture Show {n}"})
        self.assertNotIn("escalation", self.session.controls)


class QueryDedupeTests(Fixture):
    async def test_a_request_repeating_its_own_search_is_served_from_the_cache(self):
        log = []
        await self.tb.index_search("Fixture Show S01E02", log=log, scope=self.job.id)
        key = search_key("  fixture show   s01e02 ")
        self.assertEqual(key, "fixture show s01e02")
        stamp, rows = self.tb._search_cache[key]
        self.tb._search_cache[key] = (stamp - 7200, rows)  # stale for everyone else
        found, cached = await self.tb.index_search("FIXTURE SHOW s01e02", log=log, scope=self.job.id)
        self.assertEqual((found, cached), (rows, True))
        self.assertTrue(log[-1]["dedupe"])
        self.assertEqual(self.index.await_count, 1)
        await self.tb.index_search("Fixture Show S01E02", log=log, scope="another-request")
        self.assertEqual(self.index.await_count, 2)  # another request gets a fresh answer
        self.tb._search_cache[key] = (time.time() - SEARCH_REPEAT_TTL - 1, rows)
        await self.tb.index_search("Fixture Show S01E02", log=log, scope=self.job.id)
        self.assertEqual(self.index.await_count, 3)  # not forever
        self.assertEqual(search_key("Fixture:  Punctuation"), "fixture: punctuation")  # the index reads it

    async def test_the_escalated_session_reuses_the_scouts_search(self):
        from backend.agents import scout

        await scout.scout(self.tb, self.job, [(1, 2)], ["Fixture Show"], searches=1)
        first = self.index.await_args_list[0].args[0]
        stamp, rows = self.tb._search_cache[search_key(first)]
        self.tb._search_cache[search_key(first)] = (stamp - 3600, rows)
        self.session.model = "glm-5.3"
        self.service.store.save_session(self.session)
        replies = iter([answer(("tpb_search", {"query": first.upper()}), tokens=500),
                        answer(("wake_me", {"reason": "Waiting."}), tokens=500)])
        self.runtime._call_api = AsyncMock(side_effect=lambda *_: next(replies))
        await self.runtime.wake(self.session.id, Event(kind="timer"))

        self.assertEqual(self.index.await_count, 1)
        _, tools, _, _ = ledger.load(self.service.store)
        search = next(t for t in tools if t["name"] == "tpb_search")
        self.assertEqual(search["outcome"], "cached")
        rows = ledger.load_enforcements(self.service.store)
        self.assertEqual([r["rule"] for r in rows if r["rule"] == "query_dedupe"], ["query_dedupe"])
        dedupe = next(r for r in rows if r["rule"] == "query_dedupe")
        self.assertEqual((dedupe["tool"], dedupe["detail"]["query"]), ("tpb_search", first.upper()))
        self.assertGreaterEqual(dedupe["detail"]["age"], 3600)


class IdleTurnTests(Fixture):
    async def test_note_only_turns_back_off_timer_wakes_until_something_happens(self):
        replies = []
        self.runtime._call_api = AsyncMock(side_effect=lambda *_: replies.pop(0))

        async def timer():
            self.session = self.service.store.get_session(self.session.id)
            await self.runtime.wake(self.session.id, Event(kind="timer", session_id=self.session.id))
            return self.service.store.get_session(self.session.id)

        idle = [("memory_write", {"scope": "show", "content": "Still nothing."}),
                ("wake_me", {"minutes": 5, "reason": "Checking again soon."})]
        for _ in range(2):
            replies.append(answer(*idle, tokens=500))
            session = await timer()
        self.assertEqual(session.controls["idle_turns"], 2)
        self.assertEqual(self.runtime._call_api.await_count, 2)

        before = time.time()
        session = await timer()  # the third idle wake: no model call
        self.assertEqual(self.runtime._call_api.await_count, 2)
        self.assertAlmostEqual(session.wake_at - before, 3600, delta=5)
        self.assertEqual(session.status, SessionStatus.HIBERNATING)
        self.assertIn("Looking again in about 1 hour", session.wake_reason)
        self.assertAlmostEqual(self.service.store.get_job(self.job.id).next_wake_at, session.wake_at, delta=1)
        self.assertEqual(self.service.store.pending_events(session.id), [])

        replies.append(answer(*idle, tokens=500))
        session = await timer()  # the deferred check itself runs, so nothing waits forever
        self.assertEqual(self.runtime._call_api.await_count, 3)
        before = time.time()
        session = await timer()  # still idle: the next check backs off further
        self.assertEqual(self.runtime._call_api.await_count, 3)
        self.assertAlmostEqual(session.wake_at - before, 7200, delta=5)

        # Any other event wakes it at once; progress resets the count.
        replies.append(answer(("inventory_read", {}), ("wake_me", {"reason": "Waiting for the download."}), tokens=500))
        await self.runtime.wake(self.session.id, Event(kind="nudge", session_id=self.session.id))
        session = self.service.store.get_session(self.session.id)
        self.assertEqual(self.runtime._call_api.await_count, 4)
        self.assertNotIn("idle_turns", session.controls)
        self.assertNotIn("deferred", session.controls)

        rows = ledger.load_enforcements(self.service.store)
        self.assertEqual([(r["rule"], r["detail"]["deferred"]) for r in rows], [("note_only_suppressed", 1),
                                                                                ("note_only_suppressed", 2)])
        self.assertEqual(rows[0]["profile"], "fetch_scout")

    async def test_kinds_without_a_limit_are_never_deferred(self):
        session = AgentSession(agent=AgentKind.LIBRARIAN, model="glm-5.3-flash", controls={"idle_turns": 9})
        spec = self.runtime._specs["librarian"]
        self.assertEqual(profiles.for_session(session).note_only_limit, 0)
        self.assertFalse(self.runtime._defer_idle_wake(session, spec, [Event(kind="timer")]))


if __name__ == "__main__":
    unittest.main()


class CallBoundedWaveTests(Fixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.env.__exit__(None, None, None)
        # The default profile: a wave is four searching calls on any provider.
        self.env = environment(SPARROW_SMART_MODEL="glm-5.3", SPARROW_CHEAP_MODEL="glm-5.3-flash")
        self.env.__enter__()

    async def test_a_wave_is_four_searching_calls_however_cheap(self):
        self.tb.scouted[self.session.id] = {"rows": {}, "titles": ["Fixture Show"], "exclude": []}
        ctx = ToolCtx(self.session, self.runtime)
        text = await self.tool("escalate_model")(ctx, {"reason": "Nothing usable."})
        self.assertIn("4 searching calls per wave", text)
        search = self.tool("tpb_search")
        for n in range(1, 5):
            self.paid(0.0005, f"c{n}")
            await search(ctx, {"query": f"Fixture Show query {n}"})
        self.paid(0.0005, "c5")
        with self.assertRaisesRegex(ToolError, r"made 4 of its 4 searching calls and found no new candidate"):
            await search(ctx, {"query": "Fixture Show query 5"})
