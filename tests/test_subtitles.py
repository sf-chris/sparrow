import asyncio
import base64
import json
import unittest
from unittest.mock import AsyncMock, patch
from backend.agents.subtitles import install_subtitles
from backend.agents.subtitle_worker import cues_from_text, render, inspect_evidence
from backend.agents.subtitle_provider import SubtitleProvider
from backend.agents.media_state import file_version
from backend.agents.service import AgentService
from backend.agents.node_executor import canonical
from backend.agents.runtime import ToolError
import test_playback
from test_discovery import response


class SubtitleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_playback.PlaybackTests.asyncSetUp
    asyncTearDown = test_playback.PlaybackTests.asyncTearDown
    browser = test_playback.PlaybackTests.browser
    start = test_playback.PlaybackTests.start

    async def manager(self, review=False):
        self.service = AgentService(
            self.storage, str(self.root / "server"), AsyncMock()
        )
        self.service.emit = AsyncMock()
        self.service.runtime._track_spend = lambda *_: None
        self.service.runtime._api_key_getter = lambda: "fixture-key" if review else ""
        self.subtitles = install_subtitles(
            self.app, self.accounts, self.nodes, self.catalogue, lambda: self.service
        )
        self.subtitles.attach(self.service)
        return self.subtitles

    def evidence(self):
        folder = self.nodes.local().cache_root / "subtitles" / "fixture"
        folder.mkdir(parents=True, exist_ok=True)
        prepared = folder / "prepared.vtt"
        prepared.write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello &amp; welcome.\n"
        )
        return {
            "quality": {
                "passed": True,
                "reasons": [],
                "median_timing_error": 0.1,
                "max_timing_error": 0.2,
            },
            "samples": [
                {
                    "transcript": "Hello and welcome",
                    "subtitle": {"text": "Hello and welcome"},
                }
            ],
            "path": "subtitles/fixture/prepared.vtt",
            "version": file_version(prepared),
            "original_path": "subtitles/fixture/prepared.vtt",
            "original_version": file_version(prepared),
        }

    async def prepared(self, review=False):
        manager = await self.manager(review)
        evidence = self.evidence()
        real = self.nodes.execute

        async def execute(node, kind, args, **kw):
            if kind == "subtitle_prepare":
                return evidence
            return await real(node, kind, args, **kw)

        if review:
            self.service.runtime._call_api = AsyncMock(
                side_effect=[
                    response("evidence", {}),
                    response(
                        "verdict",
                        {
                            "approved": True,
                            "reason": "The sampled spoken dialogue agrees and the measured timings pass.",
                        },
                    ),
                ]
            )
        with patch.object(self.nodes, "execute", side_effect=execute):
            result = await self.client.post(
                f"/api/v1/assets/{self.asset}/subtitles/repair",
                json={"text": "1\n00:00:01,000 --> 00:00:03,000\nHello and welcome.\n"},
            )
            self.assertEqual(result.status_code, 200, result.text)
            await asyncio.gather(*list(manager.tasks.values()))
        return manager.task(result.json()["id"])

    async def test_measured_track_waits_for_review_then_becomes_playable_with_personal_offset(
        self,
    ):
        task = await self.prepared(review=True)
        self.assertEqual(task["state"], "ready")
        logged = self.storage.operations.page(self.owner, category="subtitle")
        self.assertEqual(logged["entries"][0]["severity"], "success")
        tracks = (
            await self.client.get(f"/api/v1/assets/{self.asset}/subtitles")
        ).json()["tracks"]
        self.assertTrue(tracks[0]["review"]["approved"])
        session = await self.start()
        caption = session["subtitles"][0]
        self.assertEqual(caption["id"], tracks[0]["id"])
        changed = await self.client.patch(
            f'/api/v1/subtitles/tracks/{caption["id"]}/offset', json={"seconds": 2}
        )
        self.assertEqual(changed.status_code, 200)
        text = (await self.client.get(caption["url"])).text
        self.assertIn("00:00:03.000 --> 00:00:05.000", text)
        self.assertNotIn("&amp;amp;", text)
        original = await self.client.get(caption["url"] + "?original=true")
        self.assertIn("00:00:01.000", original.text)
        second = await self.start(audio_index=self.facts["audio_tracks"][1]["index"])
        self.assertFalse(any(t.get("id") == caption["id"] for t in second["subtitles"]))
        user = self.accounts.create_user(
            "another",
            "another-password-123",
            "Another",
            invitation=self.accounts.invite("viewer", None),
        )
        async with self.browser(user) as browser:
            self.assertIn("00:00:01.000", (await browser.get(caption["url"])).text)
        self.assertEqual(self.service.runtime._call_api.call_count, 2)

    async def test_model_outage_keeps_measured_subtitles_pending_without_blocking_video(
        self,
    ):
        task = await self.prepared()
        self.assertEqual(task["state"], "review_pending")
        logged = self.storage.operations.page(self.owner, category="subtitle")
        self.assertEqual(logged["entries"][0]["severity"], "warning")
        self.assertIn("retry", logged["entries"][0]["summary"])
        track = self.subtitles.tracks(self.owner, self.asset)[0]
        self.assertEqual((await self.client.get(track["url"])).status_code, 404)
        session = await self.start()
        self.assertEqual(
            (
                await self.client.get(session["url"], headers={"Range": "bytes=0-31"})
            ).status_code,
            206,
        )

    async def test_cancel_during_preparation_discards_late_track_and_review(self):
        manager = await self.manager(True)
        entered = asyncio.Event()
        release = asyncio.Event()
        real = self.nodes.execute

        async def execute(node, kind, args, **kw):
            if kind == "subtitle_prepare":
                entered.set()
                await release.wait()
                return self.evidence()
            return await real(node, kind, args, **kw)

        with patch.object(self.nodes, "execute", side_effect=execute):
            result = await self.client.post(
                f"/api/v1/assets/{self.asset}/subtitles/repair",
                json={"text": "1\n00:00:01,000 --> 00:00:03,000\nHello and welcome.\n"},
            )
            await asyncio.wait_for(entered.wait(), 10)
            await self.client.delete("/api/v1/subtitles/tasks/" + result.json()["id"])
            release.set()
            await asyncio.gather(*list(manager.tasks.values()))
        self.assertEqual(manager.task(result.json()["id"])["state"], "cancelled")
        self.assertEqual(manager.tracks(self.owner, self.asset), [])


class SubtitleEvidenceTests(unittest.TestCase):
    def test_plain_caption_sanitization_and_invalid_timing(self):
        text = "1\n00:00:01,000 --> 00:00:02,000\n<script>attack()</script> & text\n"
        result = render(cues_from_text(text, "srt"), vtt=True)
        self.assertNotIn("<script>", result)
        with self.assertRaises(ValueError):
            cues_from_text("1\n00:00:03,000 --> 00:00:02,000\nbad\n", "srt")

    def test_wrong_text_and_missing_dialogue_are_never_quality_passes(self):
        cues = [{"start": 1, "end": 2, "text": "This is the expected dialogue"}]
        sample = {
            "cue_index": 0,
            "subtitle": cues[0],
            "language": "en",
            "words": [
                {
                    "text": "Completely different words",
                    "start": 1,
                    "end": 2,
                    "probability": 1,
                }
            ],
        }
        self.assertFalse(inspect_evidence(cues, [sample], 24, "en")["passed"])
        self.assertFalse(inspect_evidence(cues, [], 24, "en")["passed"])
