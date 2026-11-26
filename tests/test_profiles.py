"""Versioned agent profiles: one place for each role's model, effort, bounds
and prompt/tool version; sessions and ledger rows record what they ran under,
and no profile change or new session resets a request's allowance."""
import contextlib
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.agents import ledger, profiles
from backend.agents.models import AgentKind, AgentSession, CaseState, Event, Job
from backend.agents.service import AgentService
from backend.models import SparrowConfig
from backend.storage import Storage
from test_discovery import Block

MODEL_VARIABLES = ("SPARROW_SMART_MODEL", "SPARROW_CHEAP_MODEL", "SPARROW_FETCH_SCOUT", "SPARROW_PROFILES",
                   "SPARROW_SUBTITLE_MODEL", "SPARROW_SUBTITLE_EFFORT", "SPARROW_SUBTITLE_BUDGET",
                   "SPARROW_SUBTITLE_CONTRACTOR", "SPARROW_SUBTITLE_CONTRACTOR_EFFORT", "SPARROW_SUBTITLE_VERIFIER")


@contextlib.contextmanager
def environment(**values):
    """Only these model settings (a developer .env must not leak in)."""
    with patch.dict(os.environ, {k: v for k, v in values.items()}):
        for name in MODEL_VARIABLES:
            if name not in values:
                os.environ.pop(name, None)
        with patch("backend.configuration._saved_config", None):
            yield


def answer(name, args, tokens=1000):
    usage = SimpleNamespace(input_tokens=tokens, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(content=[Block(name, args)], usage=usage)


class ResolutionTests(unittest.TestCase):
    def test_defaults_match_what_ran_before_profiles(self):
        with environment():
            by_role = {p.id: p for p in profiles.all_profiles(SparrowConfig())}
        self.assertEqual(set(by_role), set(profiles.ROLES))
        cheap, smart = "glm-5.3-flash", "glm-5.3"
        expected = {
            "discovery": (cheap, "low", 8, 1400), "fetch_scout": (cheap, "low", 40, 4096),
            "fetch_review": (smart, "low", 1, 8000), "fetch_escalation": (smart, "high", 40, 4096),
            "media": (cheap, "low", 40, 4096), "media_escalation": (smart, "high", 40, 4096),
            "librarian": (cheap, "low", 6, 1400), "subtitle_manager": (smart, "high", 40, 16000),
            "subtitle_checker": (cheap, "low", 1, 8000), "subtitle_verifier": (smart, "low", 1, 8000),
            "subtitle_reader": (cheap, "low", 1, 0),
        }
        for role, (model, effort, steps, tokens) in expected.items():
            p = by_role[role]
            self.assertEqual((p.model, p.effort_sent, p.max_steps, p.max_tokens), (model, effort, steps, tokens), role)
            self.assertRegex(p.version, r"^[0-9a-f]{12}$")
            self.assertTrue(p.prompt and p.tools, role)
        self.assertTrue(by_role["subtitle_manager"].cache)
        escalation = by_role["fetch_escalation"]
        # Calls bound a wave; dollars are a ceiling.
        self.assertEqual((escalation.wave_calls, escalation.wave_dollars, escalation.waves), (4, 0.25, 3))
        self.assertEqual(by_role["fetch_scout"].wave_dollars, 0)
        self.assertEqual(by_role["fetch_scout"].note_only_limit, 2)

    def test_effort_is_always_a_level_glm_accepts(self):
        # GLM 5.3 takes low, high or max and thinks at max when sent nothing.
        for requested, sent in (("", "low"), ("minimal", "low"), ("medium", "low"), ("high", "high"),
                                ("xhigh", "high"), ("max", "max"), ("nonsense", "low")):
            self.assertEqual(profiles.effort_for("glm-5.3", requested), sent, requested)

    def test_versions_are_stable_and_change_with_the_contents(self):
        with environment():
            first, again = profiles.resolve("fetch_escalation"), profiles.resolve("fetch_escalation")
            self.assertEqual(first, again)
            self.assertEqual(first.version, again.version)
            bound = first.bind("glm-5.3-flash")
            self.assertEqual((bound.model, bound.id), ("glm-5.3-flash", "fetch_escalation"))
            self.assertNotEqual(bound.version, first.version)
            self.assertIs(first.bind(first.model), first)
        with environment(SPARROW_PROFILES=json.dumps({"fetch_escalation": {"max_steps": 20}})):
            changed = profiles.resolve("fetch_escalation")
        self.assertEqual(changed.max_steps, 20)
        self.assertNotEqual(changed.version, first.version)
        self.assertEqual(changed.prompt, first.prompt)  # same prompt and tools, other bounds

    def test_the_existing_variables_still_override(self):
        cfg = SparrowConfig(smart_model="glm-5.3-flashx", cheap_model="glm-5.3-flashx")
        with environment():
            self.assertEqual(profiles.resolve("fetch_review", cfg).model, "glm-5.3-flashx")  # saved config
            self.assertEqual(profiles.resolve("librarian", cfg).model, "glm-5.3-flashx")
            # Settings saved by an older release may name a model GLM cannot run.
            older = SparrowConfig(smart_model="retired-model", cheap_model="retired-model")
            self.assertEqual(profiles.resolve("fetch_review", older).model, "glm-5.3")
            self.assertEqual(profiles.resolve("librarian", older).model, "glm-5.3-flash")
        with environment(SPARROW_SMART_MODEL="glm-5.3", SPARROW_CHEAP_MODEL="glm-5.3-flash"):
            self.assertEqual(profiles.resolve("fetch_escalation", cfg).model, "glm-5.3")  # environment wins
            self.assertEqual(profiles.resolve("fetch_scout", cfg).model, "glm-5.3-flash")
            self.assertEqual(profiles.resolve("fetch_review", cfg).effort_sent, "low")
        with environment(SPARROW_FETCH_SCOUT="off"):
            scout = profiles.resolve("fetch_scout")
            self.assertEqual(scout.model, "glm-5.3")  # smart throughout
            session = AgentSession(agent=AgentKind.FETCH, model="glm-5.3")
            self.assertEqual(profiles.role_for(session), "fetch_scout")  # no escalation, so no wave bound
        with environment(SPARROW_SUBTITLE_MODEL="glm-5.3-flash", SPARROW_SUBTITLE_EFFORT="max",
                         SPARROW_SUBTITLE_BUDGET="0.8"):
            manager = profiles.resolve("subtitle_manager")
            checker = profiles.resolve("subtitle_checker")
        self.assertEqual((manager.model, manager.effort_sent, manager.max_dollars), ("glm-5.3-flash", "max", 0.8))
        self.assertEqual((checker.model, checker.effort_sent), ("glm-5.3-flash", "low"))

    def test_sparrow_profiles_overrides_fields_and_ignores_bad_ones(self):
        custom = {"fetch_scout": {"effort": "high", "max_steps": 12}, "fetch_escalation": {"model": "glm-5.3-flashx",
                  "effort": "max", "max_tokens": "lots"}, "no_such_role": {"model": "x"}, "librarian": {"colour": "blue"}}
        with environment(SPARROW_PROFILES=json.dumps(custom)):
            scout, escalation = profiles.resolve("fetch_scout"), profiles.resolve("fetch_escalation")
            librarian = profiles.resolve("librarian")
        self.assertEqual((scout.max_steps, scout.effort, scout.effort_sent), (12, "high", "high"))
        self.assertEqual((escalation.model, escalation.effort_sent, escalation.max_tokens),
                         ("glm-5.3-flashx", "max", 4096))
        self.assertEqual(librarian.max_steps, 6)
        with environment(SPARROW_PROFILES="not json"):
            self.assertEqual(profiles.resolve("fetch_scout").max_steps, 40)

    def test_roles_follow_the_session(self):
        with environment():
            fetch = AgentSession(agent=AgentKind.FETCH, model="glm-5.3-flash")
            self.assertEqual(profiles.for_session(fetch).id, "fetch_scout")
            fetch.model = "glm-5.3"
            self.assertEqual(profiles.for_session(fetch).id, "fetch_escalation")
            media = AgentSession(agent=AgentKind.MEDIA, model="glm-5.3")
            self.assertEqual(profiles.for_session(media).id, "media_escalation")
            older = AgentSession(agent=AgentKind.LIBRARIAN, model="glm-5.3-flashx")  # created under another config
            profile = profiles.for_session(older)
            self.assertEqual((profile.id, profile.model), ("librarian", "glm-5.3-flashx"))
            self.assertEqual(profiles.for_session(AgentSession(agent=AgentKind.SUBTITLE)).id, "subtitle_manager")

    def test_the_command_lists_every_role(self):
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with environment(), redirect_stdout(out):
            profiles.main([])
        for role in profiles.ROLES:
            self.assertIn(f"| {role} |", out.getvalue())


class RuntimeProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = environment(SPARROW_SMART_MODEL="glm-5.3", SPARROW_CHEAP_MODEL="glm-5.3-flash")
        self.env.__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.storage = Storage(self.temp.name)
        await self.storage.load_all()
        await self.storage.save_config(SparrowConfig())
        self.service = AgentService(self.storage, self.temp.name, AsyncMock())
        self.runtime = self.service.runtime
        self.job = Job(tmdb_id=42, title="Fixture Show", wanted_episodes={"1": [2]})
        self.session = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id, model=self.service.fetch_model())
        self.job.session_id = self.session.id
        self.service.store.save_job(self.job)
        self.service.store.save_session(self.session)

    async def test_sessions_and_ledger_rows_record_the_profile_they_ran_under(self):
        replies = iter([answer("escalate_model", {"reason": "Nothing usable."}),
                        answer("wake_me", {"reason": "Waiting for the download."})])
        self.runtime._call_api = AsyncMock(side_effect=lambda *_: next(replies))
        await self.runtime.wake(self.session.id, Event(kind="job_created", payload={"description": "New request."}))

        session = self.service.store.get_session(self.session.id)
        scout, escalated = profiles.resolve("fetch_scout"), profiles.resolve("fetch_escalation")
        self.assertEqual([(p["id"], p["version"]) for p in session.profiles],
                         [(scout.id, scout.version), (escalated.id, escalated.version)])
        self.assertEqual((session.profile, session.profile_version), (escalated.id, escalated.version))
        calls, _, _, _ = ledger.load(self.service.store)
        self.assertEqual([(c["profile"], c["profile_version"]) for c in calls],
                         [(scout.id, scout.version), (escalated.id, escalated.version)])
        self.assertEqual(ledger.summarize(self.service.store)["stats"]["profiles"],
                         {f"fetch_scout@{scout.version}": 1, f"fetch_escalation@{escalated.version}": 1})
        rows = [json.loads(line) for line in ledger.export_text(self.service.store).splitlines()]
        self.assertEqual(rows[0]["profile"], "fetch_scout")
        previews = {p["agent"]: p for p in self.service.prompt_previews()}
        self.assertEqual(previews["fetch"]["profile"]["id"], "fetch_scout")

    async def test_a_profile_change_or_new_session_never_resets_the_allowance(self):
        self.runtime.policy_getter = lambda: {"max_agent_calls": 40, "max_agent_dollars": 0.5}
        # Spent under the scout profile's first version.
        self.session.spend.entries.append({"ts": time.time(), "model": "glm-5.3-flash", "cost": 0.49})
        self.session.spend.dollars = 0.49
        self.session.profile, self.session.profile_version = "fetch_scout", profiles.resolve("fetch_scout").version
        self.service.store.save_session(self.session)
        self.runtime._call_api = AsyncMock(return_value=answer("wake_me", {"reason": "Waiting."}))

        # The profile changes (a new version) and the request gets a new session.
        with patch.dict(os.environ, {"SPARROW_PROFILES": json.dumps({"fetch_scout": {"max_steps": 30}})}):
            self.assertNotEqual(profiles.resolve("fetch_scout").version, self.session.profile_version)
            fresh = AgentSession(agent=AgentKind.FETCH, job_id=self.job.id, model=self.service.fetch_model())
            self.service.store.save_session(fresh)
            await self.runtime.wake(fresh.id, Event(kind="nudge"))
        self.runtime._call_api.assert_not_called()
        self.assertEqual(self.service.store.get_session(fresh.id).outcome, CaseState.BUDGET_LIMITED)

        # Escalating to another profile mid-request does not reset it either.
        self.session.model = "glm-5.3"
        self.service.store.save_session(self.session)
        await self.runtime.wake(self.session.id, Event(kind="timer"))
        self.runtime._call_api.assert_not_called()
        stopped = self.service.store.get_session(self.session.id)
        self.assertEqual((stopped.outcome, profiles.role_for(stopped)), (CaseState.BUDGET_LIMITED, "fetch_escalation"))

    async def test_the_provider_receives_the_profile_effort(self):
        from backend.agents import glm_loop

        sent = []

        async def create(*args, **kwargs):
            sent.append((args[1], kwargs.get("effort"), kwargs.get("max_tokens")))
            return answer("wake_me", {"reason": "Waiting."})

        self.runtime._api_key_getter = lambda: "fixture-not-sent"
        with patch.object(glm_loop, "create", create), \
                patch.dict(os.environ, {"SPARROW_PROFILES": json.dumps({"fetch_escalation": {"effort": "max"}})}):
            await self.runtime._call_api(self.session, "orders", [])  # the scout: low
            self.session.model = "glm-5.3"
            await self.runtime._call_api(self.session, "orders", [])  # escalated: max
        self.assertEqual(sent, [("glm-5.3-flash", "low", 4096), ("glm-5.3", "max", 4096)])

    async def test_a_session_from_before_glm_continues_on_its_roles_model(self):
        self.session.model = "retired-model"
        self.service.store.save_session(self.session)
        self.runtime._call_api = AsyncMock(return_value=answer("wake_me", {"reason": "Waiting."}))
        await self.runtime.wake(self.session.id, Event(kind="nudge"))
        self.assertEqual(self.service.store.get_session(self.session.id).model, "glm-5.3-flash")


if __name__ == "__main__":
    unittest.main()
