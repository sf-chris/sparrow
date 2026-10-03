import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from backend.storage import Storage
from backend.models import SparrowConfig
from backend.agents.service import AgentService
from backend.agents.models import Mandate, MonitoringMode, AgentKind, SessionStatus
from backend.agents.runtime import ToolError
from test_discovery import response


class CurationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(self.temp.name)
        await self.storage.load_all()
        await self.storage.save_config(
            SparrowConfig(
                library_dir=self.temp.name + "/media",
                staging_dir=self.temp.name + "/incoming",
            )
        )
        self.service = AgentService(self.storage, self.temp.name, AsyncMock())
        self.service.emit = AsyncMock()
        self.owner = self.service.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )
        self.other = self.service.accounts.create_user(
            "other",
            "fixture-password-456",
            "Other",
            invitation=self.service.accounts.invite("requester", None),
        )
        self.care = self.service.curation
        self.service.runtime._api_key_getter = lambda: "fixture-key"
        self.service.runtime._track_spend = lambda *_: None
        self.episodes = [
            {"episode_number": 1, "air_date": "2020-01-01"},
            {"episode_number": 2, "air_date": "2020-01-02"},
            {"episode_number": 3, "air_date": "2099-01-01"},
        ]

        async def tmdb(path, **kwargs):
            return (
                {"episodes": self.episodes}
                if "/season/" in path
                else {
                    "id": 42,
                    "name": "Fixture show",
                    "seasons": [{"season_number": 1}],
                    "original_language": "en",
                }
            )

        self.service.toolbox.tmdb_get = AsyncMock(side_effect=tmdb)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def follow(self, **kwargs):
        return self.care.record(
            self.owner["id"],
            42,
            "tv",
            "local",
            {"1": [1]},
            "backfill",
            self.service.accounts.resolve(self.owner["id"]),
            **kwargs,
        )

    async def test_large_followed_season_keeps_all_eligible_candidates(self):
        self.episodes = [
            {"episode_number": i, "air_date": "2020-01-01"}
            for i in range(1, 126)
        ]
        row = self.follow()
        facts = await self.care.evidence(row)
        self.assertEqual(len(facts["candidates"]), 125)
        self.assertEqual(facts["candidates"][-1]["episode"], 125)

    async def test_changed_facts_wake_agent_once_and_unchanged_idle_costs_no_calls(
        self,
    ):
        row = self.follow()
        self.service.runtime._call_api = AsyncMock(
            side_effect=[
                response("evidence", {}),
                response("acquire", {"wanted_episodes": {"1": [1, 2]}}),
                response(
                    "finish",
                    {
                        "message": "Requested the two aired episodes. Future episodes will wait for their air date."
                    },
                ),
            ]
        )
        await self.care.check(row["id"])
        jobs = self.service.store.get_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].wanted_episodes, {"1": [1, 2]})
        self.assertEqual(jobs[0].user_id, self.owner["id"])
        self.assertEqual(self.service.runtime._call_api.call_count, 3)
        await self.care.check(row["id"])
        await self.care.check(row["id"])
        self.assertEqual(self.service.runtime._call_api.call_count, 3)
        self.assertTrue(
            all(
                s.wake_at == 0
                for s in self.service.store.get_sessions(AgentKind.LIBRARIAN)
            )
        )

    async def test_scope_personal_authority_and_future_episodes_are_enforced(self):
        row = self.follow()
        with self.assertRaises(ToolError):
            await self.care.enforce(self.other["id"], 42, "tv", "local", {"1": [1]})
        with self.assertRaises(ToolError):
            await self.care.enforce(self.owner["id"], 42, "tv", "local", {"1": [3]})
        self.care.save(row, enabled=False)
        with self.assertRaises(ToolError):
            await self.care.enforce(self.owner["id"], 42, "tv", "local", {"1": [1]})

    async def test_failed_unchanged_review_retries_same_session_after_restart(self):
        row = self.follow()
        self.service.runtime._call_api = AsyncMock(return_value=None)
        await self.care.check(row["id"])
        failed = self.care.row(row["id"])
        session = self.service.store.get_session(failed["data"]["session_id"])
        self.assertGreater(session.wake_at, 0)
        self.assertNotEqual(
            failed["data"].get("fingerprint"), failed["data"]["observed_fingerprint"]
        )
        # Retry delay prevents repeated unchanged polling from spending tokens.
        await self.care.check(row["id"])
        self.assertEqual(self.service.runtime._call_api.call_count, 1)
        original_messages = list(session.messages)
        restarted = AgentService(self.storage, self.temp.name, AsyncMock())
        restarted.toolbox.tmdb_get = self.service.toolbox.tmdb_get
        restarted.runtime._api_key_getter = lambda: "fixture-key"
        restarted.runtime._call_api = AsyncMock(
            side_effect=[
                response("evidence", {}),
                response("finish", {"message": "Reviewed the current scope."}),
            ]
        )
        with patch("time.time", return_value=session.wake_at + 1):
            await restarted.curation.check(row["id"])
        complete = restarted.curation.row(row["id"])
        self.assertEqual(complete["data"]["session_id"], session.id)
        recovered = restarted.store.get_session(session.id)
        self.assertEqual(recovered.status, SessionStatus.CLOSED)
        self.assertGreaterEqual(len(recovered.messages), len(original_messages))
        self.assertEqual(
            complete["data"]["fingerprint"], complete["data"]["observed_fingerprint"]
        )
        await restarted.curation.check(row["id"])
        self.assertEqual(restarted.runtime._call_api.call_count, 2)

    async def test_missing_provider_does_not_mark_evidence_successfully_reviewed(self):
        row = self.follow()
        self.service.runtime._api_key_getter = lambda: ""
        await self.care.check(row["id"])
        saved = self.care.row(row["id"])
        self.assertNotEqual(
            saved["data"].get("fingerprint"), saved["data"]["observed_fingerprint"]
        )
        self.service.runtime._api_key_getter = lambda: "fixture-key"
        self.service.runtime._call_api = AsyncMock(
            return_value=response("finish", {"message": "Reviewed."})
        )
        await self.care.check(row["id"])
        self.assertEqual(self.service.runtime._call_api.call_count, 1)

    async def test_failed_review_keeps_successful_acquisition_receipt(self):
        row = self.follow()
        self.service.runtime._call_api = AsyncMock(
            side_effect=[
                response("acquire", {"wanted_episodes": {"1": [1]}}),
                None,
            ]
        )
        await self.care.check(row["id"])
        saved = self.care.row(row["id"])
        self.assertEqual(saved["data"]["acquired_session"], saved["data"]["session_id"])
        self.assertEqual(len(self.service.store.get_jobs()), 1)

    async def test_legacy_fingerprint_without_success_cannot_suppress_provider_recovery(
        self,
    ):
        import hashlib
        from backend.agents.node_executor import canonical

        row = self.follow()
        observation = await self.care.evidence(row)
        self.care.save(
            row, fingerprint=hashlib.sha256(canonical(observation).encode()).hexdigest()
        )
        self.service.runtime._call_api = AsyncMock(
            return_value=response(
                "finish", {"message": "Reviewed after provider recovery."}
            )
        )
        await self.care.check(row["id"])
        self.assertEqual(self.service.runtime._call_api.call_count, 1)
        await self.care.check(row["id"])
        self.assertEqual(self.service.runtime._call_api.call_count, 1)

    async def test_edit_during_reasoning_discards_old_response(self):
        row = self.follow()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(*args):
            entered.set()
            await release.wait()
            return response("acquire", {"wanted_episodes": {"1": [1]}})

        self.service.runtime._call_api = slow
        task = asyncio.create_task(self.care.check(row["id"]))
        await entered.wait()
        changed = self.care.record(
            self.owner["id"],
            42,
            "tv",
            "local",
            {},
            "exact",
            self.service.accounts.resolve(self.owner["id"]),
        )
        self.care.save(changed, enabled=False)
        release.set()
        await task
        self.assertEqual(self.service.store.get_jobs(), [])

    async def test_exact_scope_extension_updates_one_job_and_invalidates_old_revision(
        self,
    ):
        first = await self.service.create_job(42, {"1": [1]}, user_id=self.owner["id"])
        again = await self.service.create_job(42, {"1": [2]}, user_id=self.owner["id"])
        self.assertEqual(again.id, first.id)
        self.assertEqual(again.wanted_episodes, {"1": [1, 2]})
        self.assertGreater(again.revision, first.revision)
        self.assertEqual(len(self.service.store.get_jobs()), 1)
        # A new request's Fetch Agent works through the scout on the cheap model.
        self.assertEqual(self.service.store.get_session(first.session_id).model, self.service.cheap_model())

    async def test_care_cannot_replace_an_active_requests_preferences(self):
        first = await self.service.create_job(
            42, {"1": [1]}, user_id=self.owner["id"], monitoring="backfill"
        )
        with self.assertRaisesRegex(
            ToolError, "active request with different preferences"
        ):
            await self.service.create_job(
                42,
                {"1": [2]},
                user_id=self.owner["id"],
                origin="librarian",
                preference_overrides={"min_quality": "1080p"},
            )
        current = self.service.store.get_job(first.id)
        self.assertEqual(current.wanted_episodes, {"1": [1]})
        self.assertEqual(current.revision, first.revision)
        self.assertEqual(current.preferences, first.preferences)

    async def test_legacy_authority_migrates_paused_to_owner_only_and_memory_is_private(
        self,
    ):
        self.service.store.save_mandate(
            Mandate(tmdb_id=42, mode=MonitoringMode.BACKFILL)
        )
        self.care.migrate()
        self.care.migrate()
        self.assertEqual(len(self.care.rows()), 1)
        self.assertFalse(self.care.rows()[0]["data"]["enabled"])
        self.assertEqual(self.care.rows(self.other["id"]), [])
        self.service.store.write_memory(
            "global", "Private choices", user_id=self.owner["id"]
        )
        self.assertEqual(
            self.service.store.read_memory("global", user_id=self.other["id"]), ""
        )

    async def test_estimated_reasoning_budget_refuses_a_call_before_spending(self):
        self.follow()
        job = await self.service.create_job(42, {"1": [1]}, user_id=self.owner["id"])
        self.service.runtime.policy_getter = lambda: {
            "max_agent_calls": 40,
            "max_agent_dollars": 0.01,
        }
        self.service.runtime._call_api = AsyncMock()
        session = self.service.store.get_session(job.session_id)
        from backend.agents.models import Event

        await self.service.runtime.wake(session.id, Event(kind="check"))
        self.service.runtime._call_api.assert_not_called()
        self.assertIn(
            "spending limit", self.service.store.get_session(session.id).wake_reason
        )

    async def test_upgrade_contract_requires_the_preferred_quality(self):
        row = self.follow(upgrades=True)
        prefs = self.service.accounts.resolve(self.owner["id"])
        self.care.evidence = AsyncMock(
            return_value={
                "title": "Fixture show",
                "media_type": "tv",
                "tmdb_id": 42,
                "preferences": prefs,
                "candidates": [{"season": 1, "episode": 1, "reason": "upgrade"}],
            }
        )
        self.service.runtime._call_api = AsyncMock(
            side_effect=[
                response("acquire", {"wanted_episodes": {"1": [1]}}),
                response(
                    "finish",
                    {"message": "Requested an upgrade and retained the current copy."},
                ),
            ]
        )
        await self.care.check(row["id"])
        job = self.service.store.get_jobs()[0]
        self.assertEqual(job.origin, "upgrade")
        self.assertEqual(job.min_quality, job.preferred_quality)
        self.assertTrue(self.care.row(row["id"])["data"]["upgrades"])

    async def test_edit_while_acquisition_loads_metadata_cannot_create_or_restore_old_care(
        self,
    ):
        row = self.follow()
        entered = asyncio.Event()
        release = asyncio.Event()
        self.care.evidence = AsyncMock(
            return_value={
                "title": "Fixture show",
                "media_type": "tv",
                "tmdb_id": 42,
                "preferences": self.service.accounts.resolve(self.owner["id"]),
                "candidates": [{"season": 1, "episode": 1, "reason": "missing"}],
            }
        )

        async def slow_metadata(path, **kwargs):
            entered.set()
            await release.wait()
            return (
                {"episodes": self.episodes}
                if "/season/" in path
                else {"name": "Fixture show", "original_language": "en"}
            )

        self.service.toolbox.tmdb_get = slow_metadata
        self.service.runtime._call_api = AsyncMock(
            return_value=response("acquire", {"wanted_episodes": {"1": [1]}})
        )
        task = asyncio.create_task(self.care.check(row["id"]))
        await entered.wait()
        changed = self.care.record(
            self.owner["id"],
            42,
            "tv",
            "local",
            {},
            "exact",
            self.service.accounts.resolve(self.owner["id"]),
        )
        self.care.save(changed, enabled=False)
        release.set()
        await task
        self.assertEqual(self.service.store.get_jobs(), [])
        self.assertFalse(self.care.save(row, message="stale outcome"))
        self.assertFalse(self.care.row(row["id"])["data"]["enabled"])
