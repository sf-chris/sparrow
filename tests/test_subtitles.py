import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from backend.agents.media_state import file_version
from backend.agents.models import SessionStatus
from backend.agents.service import AgentService
from backend.agents.subtitle_sync import ONSET_BIAS
from backend.agents.subtitle_worker import cues_from_text, render
from backend.agents.subtitles import DEFAULT_REVIEW_MODEL, install_subtitles
from backend.agents import subtitle_review as review
import test_playback
from test_discovery import response

# Irregular dialogue across the 24-second fixture; captions sit 0.3 s late.
VOICE = [0.8, 2.9, 4.4, 6.9, 8.1, 10.6, 12.2, 14.9, 16.3, 18.8, 20.1, 22.3]
LATE = 0.3


def srt(starts):
    return "".join(
        f"{n}\n00:00:{int(s):02d},{round(s % 1 * 1000):03d} --> 00:00:{int(s + 1):02d},{round((s + 1) % 1 * 1000):03d}\nLine {n} of the dialogue.\n\n"
        for n, s in enumerate(starts, 1)
    )


class SubtitleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_playback.PlaybackTests.asyncSetUp
    asyncTearDown = test_playback.PlaybackTests.asyncTearDown
    browser = test_playback.PlaybackTests.browser
    start = test_playback.PlaybackTests.start

    async def test_import_uses_resolved_subtitle_defaults_and_optional_check(self):
        from backend.agents.product_api import install_product

        manager = await self.manager()
        catalogue = install_product(
            self.app, self.storage, self.accounts, self.nodes, lambda: self.service
        )
        for automatic, required, expected in [
            (True, False, True),
            (False, False, False),
            (False, True, True),
        ]:
            self.accounts.set_preferences(
                self.owner["id"],
                {
                    "subtitle_auto_prepare": automatic,
                    "require_subtitles": required,
                    "verify_subtitles": True,
                },
            )
            with (
                patch.object(
                    catalogue,
                    "confirm_import",
                    AsyncMock(return_value={"imported": [{"asset_id": self.asset}]}),
                ),
                patch.object(
                    manager, "enqueue", AsyncMock(return_value={"id": "queued"})
                ) as enqueue,
            ):
                result = await self.client.post(
                    "/api/v1/admin/imports/fixture/confirm",
                    json={"selections": [{"id": "fixture", "media_type": "movie"}]},
                )
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(enqueue.await_count, int(expected))
                if expected:
                    self.assertEqual(enqueue.call_args.args[0]["id"], self.owner["id"])
                    self.assertEqual(enqueue.call_args.args[1], self.asset)
                    self.assertTrue(
                        enqueue.call_args.kwargs["preferences"]["values"][
                            "verify_subtitles"
                        ]
                    )

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

    def speech(self, voice=VOICE, language="es"):
        """Scripted dialogue evidence as the storage node would save it."""
        folder = self.nodes.local().cache_root / "subtitle-evidence" / ("a" * 32)
        folder.mkdir(parents=True, exist_ok=True)
        utterances = [
            {
                "id": f"u{n:05d}",
                "start": round(t - 0.1, 3),
                "end": round(t + 0.9, 3),
                "onset": round(t + ONSET_BIAS, 3),
                "onset_source": "speech_detector",
                "clean_onset": True,
                "language": language,
                "text": f"línea {n}",
                "confidence": 0.9,
                "speech_support": 1.0,
                "flags": [],
                "chunk": 0,
                "words": [],
            }
            for n, t in enumerate(voice, 1)
        ]
        evidence = {
            "schema": "sparrow-speech-evidence-1",
            "source": {"audio_seconds": 24.0},
            "coverage": {"languages": {language: len(utterances)}},
            "utterances": utterances,
            "verified": False,
        }
        path = folder / "evidence.json"
        path.write_text(json.dumps(evidence))
        return {
            "state": "complete",
            "progress": 1,
            "path": f"subtitle-evidence/{'a' * 32}/evidence.json",
            "version": file_version(path),
            "model": "fixture",
            "coverage": evidence["coverage"],
        }

    async def request(self, body, *, calls=None, evidence=None, prepare=None):
        manager = self.subtitles
        real = self.nodes.execute
        seen = []

        async def execute(node, kind, args, **kw):
            seen.append(kind)
            if kind == "subtitle_evidence":
                return evidence or self.speech()
            if kind == "subtitle_prepare" and prepare:
                return await prepare(node, kind, args, **kw)
            return await real(node, kind, args, **kw)

        if calls is not None:
            self.service.runtime._call_api = AsyncMock(side_effect=calls)
        with patch.object(self.nodes, "execute", side_effect=execute):
            result = await self.client.post(
                f"/api/v1/assets/{self.asset}/subtitles/repair", json=body
            )
            self.assertEqual(result.status_code, 200, result.text)
            await asyncio.gather(*list(manager.tasks.values()))
        self.kinds = seen
        return manager.task(result.json()["id"])

    def foreign(self, **values):
        # The fixture's second audio stream is labelled Spanish.
        return {
            "text": srt([t + LATE for t in VOICE]),
            "audio_index": self.facts["audio_tracks"][1]["index"],
            **values,
        }

    def checked_review(self):
        lines = [{"speech": f"u{n:05d}", "english": f"line {n}"} for n in range(1, 13)]
        captions = [
            {"caption": f"c{n:04d}", "verdict": "ok", "speech": [f"u{n:05d}"]}
            for n in range(1, 13)
        ]
        return lines, captions

    async def test_same_language_track_is_playable_without_analysis_or_ai(self):
        await self.manager()
        task = await self.request({"text": srt(VOICE)})
        self.assertEqual(task["state"], "ready")
        self.assertFalse(task["data"]["verify"])
        self.assertNotIn("subtitle_evidence", self.kinds)
        self.assertFalse(self.service.store.get_sessions())
        track = self.subtitles.tracks(self.owner, self.asset)[0]
        self.assertFalse(track["sync_checked"])
        self.assertEqual((await self.client.get(track["url"])).status_code, 200)
        playback = await self.start()
        caption = next(t for t in playback["subtitles"] if t.get("id") == track["id"])
        self.assertNotIn("checked", caption["title"])
        prefs = self.accounts.resolve(self.owner["id"])["values"]
        self.assertTrue(self.subtitles.satisfies(self.owner, self.asset, prefs))
        self.assertFalse(
            self.subtitles.satisfies(
                self.owner, self.asset, {**prefs, "verify_subtitles": True}
            )
        )
        legacy = {k: v for k, v in prefs.items() if k != "verify_subtitles"}
        self.assertFalse(self.subtitles.satisfies(self.owner, self.asset, legacy))

    async def test_foreign_dialogue_is_measured_and_retimed_without_ai(self):
        await self.manager()
        task = await self.request(self.foreign())
        self.assertEqual(task["state"], "ready", task["data"]["message"])
        self.assertIn("adjusted", task["data"]["message"])
        self.assertFalse(self.service.store.get_sessions())
        tracks = self.subtitles.tracks(self.owner, self.asset)
        self.assertEqual(len(tracks), 1, "the uncorrected copy is superseded")
        track = tracks[0]
        self.assertTrue(track["timing_adjusted"])
        self.assertAlmostEqual(track["timing"]["before"]["offset"], LATE, delta=0.02)
        self.assertAlmostEqual(track["timing"]["offset"], 0, delta=0.02)
        served = cues_from_text((await self.client.get(track["url"])).text, "vtt")
        original = cues_from_text(
            (await self.client.get(track["original_url"])).text, "vtt"
        )
        self.assertAlmostEqual(served[3]["start"], VOICE[3], delta=0.02)
        self.assertAlmostEqual(original[3]["start"], VOICE[3] + LATE, delta=0.02)
        logged = self.storage.operations.page(self.owner, category="subtitle")
        self.assertEqual(logged["entries"][0]["summary"], "Subtitles ready.")
        # A personal delay applies on top of the shared corrected copy.
        await self.client.patch(
            f'/api/v1/subtitles/tracks/{track["id"]}/offset', json={"seconds": 2}
        )
        delayed = cues_from_text((await self.client.get(track["url"])).text, "vtt")
        self.assertAlmostEqual(delayed[3]["start"], VOICE[3] + 2, delta=0.02)

    async def test_checked_review_reads_speech_before_captions_and_gate_holds(self):
        await self.manager(review=True)
        lines, captions = self.checked_review()
        calls = [
            response("overview", {}),
            response("page", {"page": 1}),
            response("judge", {"page": 1, "captions": captions, "missing": []}),
            response("gloss", {"page": 1, "lines": lines[:3]}),
            response("gloss", {"page": 1, "lines": lines}),
            response("judge", {"page": 1, "captions": captions[:5], "missing": []}),
            response("judge", {"page": 1, "captions": captions, "missing": []}),
            response("verdict", {"approved": True, "reason": "Every line matches its dialogue."}),
        ]
        task = await self.request(self.foreign(verify=True), calls=calls)
        self.assertEqual(task["state"], "ready", task["data"]["message"])
        track = self.subtitles.tracks(self.owner, self.asset)[0]
        self.assertTrue(track["sync_checked"])
        self.assertTrue(track["timing_adjusted"])
        logged = self.storage.operations.page(self.owner, category="subtitle")
        self.assertEqual(
            logged["entries"][0]["summary"], "Subtitles checked against the dialogue."
        )
        session = self.service.store.get_session(task["data"]["review_session"])
        self.assertEqual(session.model, DEFAULT_REVIEW_MODEL)
        self.assertEqual(session.status, SessionStatus.CLOSED)
        spec = self.service.runtime._specs["subtitle"]
        self.assertTrue(spec.cache)
        self.assertGreaterEqual(spec.max_tokens, 16000)
        results = [
            block
            for message in session.messages
            if message["role"] == "user" and isinstance(message["content"], list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]
        page_view, early_judge, short_gloss, revealed = (r["content"] for r in results[1:5])
        self.assertIn("línea 1", page_view)
        self.assertNotIn("Line 1 of the dialogue", page_view)
        self.assertIn("Gloss this page", early_judge)
        self.assertIn("missing", short_gloss)
        self.assertIn("Line 1 of the dialogue", revealed)
        self.assertIn("→ line 1", revealed)
        self.assertIn("Judge every caption", results[5]["content"])

    async def test_judging_in_the_same_step_as_the_gloss_is_refused(self):
        from types import SimpleNamespace
        from test_discovery import Block

        await self.manager(review=True)
        lines, captions = self.checked_review()
        batch = SimpleNamespace(
            content=[
                Block("gloss", {"page": 1, "lines": lines}),
                Block("judge", {"page": 1, "captions": captions, "missing": []}),
            ]
        )
        calls = [
            batch,
            response("judge", {"page": 1, "captions": captions, "missing": []}),
            response("verdict", {"approved": True, "reason": "Matches."}),
        ]
        task = await self.request(self.foreign(verify=True), calls=calls)
        self.assertEqual(task["state"], "ready", task["data"]["message"])
        session = self.service.store.get_session(task["data"]["review_session"])
        errors = [
            block["content"]
            for message in session.messages
            if message["role"] == "user" and isinstance(message["content"], list)
            for block in message["content"]
            if block.get("is_error")
        ]
        self.assertEqual(len(errors), 1)
        self.assertIn("Read this page's captions", errors[0])

    async def test_a_retime_keeps_a_read_page_read(self):
        await self.manager(review=True)
        lines, captions = self.checked_review()
        calls = [
            response("gloss", {"page": 1, "lines": lines}),
            response("retime", {"mode": "shift", "seconds": -0.04}),
            response("judge", {"page": 1, "captions": captions, "missing": []}),
            response("verdict", {"approved": True, "reason": "Matches."}),
        ]
        task = await self.request(self.foreign(verify=True), calls=calls)
        self.assertEqual(task["state"], "ready", task["data"]["message"])
        session = self.service.store.get_session(task["data"]["review_session"])
        errors = [
            block["content"]
            for message in session.messages
            if message["role"] == "user" and isinstance(message["content"], list)
            for block in message["content"]
            if block.get("is_error")
        ]
        self.assertEqual(errors, [])

    async def test_manager_settles_what_cheap_page_checks_flag(self):
        from types import SimpleNamespace
        from test_discovery import Block
        from backend.agents import subtitles as module

        await self.manager(review=True)
        calls = {"translate": 0, "compare": 0}

        async def checker(system, prompt, schema):
            if "translate" in system.split(".")[0]:
                calls["translate"] += 1
                ids = [line.split()[0] for line in prompt.splitlines()[1:] if line.startswith("u")]
                return {"lines": [{"id": i, "english": f"line {int(i[1:])}"} for i in ids]}, {"output_tokens": 10}
            calls["compare"] += 1
            captions = [line.split()[0] for line in prompt.splitlines() if line.startswith("c0")]
            return {
                "captions": [
                    {"caption": c, "verdict": "wrong" if c == "c0003" else "ok", "speech": [f"u{int(c[1:]):05d}"], "note": "different meaning" if c == "c0003" else ""}
                    for c in captions
                ],
                "missing": [],
            }, {"output_tokens": 10}

        checker.model = "fixture-checker"
        self.subtitles.contract_caller = lambda model: checker
        lines, captions = self.checked_review()
        calls_to_opus = [
            response("report", {}),
            response("page", {"page": 1}),
            response("gloss", {"page": 1, "lines": lines}),
            response("judge", {"page": 1, "captions": captions, "missing": []}),
            response("edit_captions", {"changes": [{"caption": "c0003", "text": "Line 3, corrected.", "verdict": "ok", "speech": ["u00003"]}]}),
            response("verdict", {"approved": True, "reason": "Audited and settled."}),
        ]
        with patch.dict("os.environ", {"SPARROW_SUBTITLE_CONTRACTOR": "fixture-checker"}):
            task = await self.request(self.foreign(verify=True), calls=calls_to_opus)
        self.assertEqual(task["state"], "ready", task["data"]["message"])
        self.assertEqual(calls, {"translate": 1, "compare": 1})
        state = task["data"]["review"]
        self.assertEqual(state["contract"]["model"], "fixture-checker")
        self.assertEqual(state["audit"]["done"], [1])
        track = self.subtitles.tracks(self.owner, self.asset)[0]
        served = cues_from_text((await self.client.get(track["url"])).text, "vtt")
        self.assertEqual(served[2]["text"], "Line 3, corrected.")
        session = self.service.store.get_session(task["data"]["review_session"])
        report = next(
            block["content"]
            for message in session.messages
            if message["role"] == "user" and isinstance(message["content"], list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        )
        self.assertIn("c0003", report)
        self.assertIn("different meaning", report)

    async def test_approval_is_refused_when_captions_do_not_match(self):
        await self.manager(review=True)
        lines, captions = self.checked_review()
        wrong = [{**c, "verdict": "wrong"} if n < 4 else c for n, c in enumerate(captions)]
        calls = [
            response("gloss", {"page": 1, "lines": lines}),
            response("judge", {"page": 1, "captions": wrong, "missing": []}),
            response("verdict", {"approved": True, "reason": "Looks fine."}),
            response("verdict", {"approved": False, "reason": "Four captions describe other dialogue."}),
        ]
        task = await self.request(self.foreign(verify=True), calls=calls)
        self.assertEqual(task["state"], "needs_attention")
        self.assertIn("Four captions", task["data"]["message"])
        session = self.service.store.get_session(task["data"]["review_session"])
        refused = [
            block["content"]
            for message in session.messages
            if message["role"] == "user" and isinstance(message["content"], list)
            for block in message["content"]
            if block.get("is_error")
        ]
        self.assertTrue(any("Approval refused" in text for text in refused))
        self.assertTrue(
            all(t["state"] == "rejected" for t in self.subtitles.tracks(self.owner, self.asset))
        )

    async def test_agent_switches_to_a_better_source_itself(self):
        await self.manager(review=True)
        (self.library / "fixture.en.srt").write_text(srt([t + LATE for t in VOICE]))
        lines, captions = self.checked_review()
        calls = [
            response("sources", {}),
            response("use_source", {"source": "sidecar:fixture.en.srt"}),
            response("gloss", {"page": 1, "lines": lines}),
            response("judge", {"page": 1, "captions": captions, "missing": []}),
            response("verdict", {"approved": True, "reason": "The sidecar matches throughout."}),
        ]
        body = {"audio_index": self.facts["audio_tracks"][1]["index"], "verify": True}
        task = await self.request(body, calls=calls)
        self.assertEqual(task["state"], "ready", task["data"]["message"])
        chosen = self.subtitles.track(task["data"]["track_id"])
        self.assertEqual(chosen["data"]["source_id"], "sidecar:fixture.en.srt")
        self.assertTrue(chosen["data"]["review"]["approved"])
        self.assertAlmostEqual(chosen["data"]["timing"]["offset"], 0, delta=0.02)
        outcomes = {a["source_id"]: a["outcome"] for a in task["data"]["attempts"]}
        self.assertEqual(outcomes["embedded:3"], "set_aside")
        listed = self.subtitles.tracks(self.owner, self.asset)
        self.assertEqual([t["state"] for t in listed if t["source"] == "embedded"], ["rejected"])

    async def test_agent_fixes_a_wrong_caption_and_must_judge_it_again(self):
        await self.manager(review=True)
        lines, captions = self.checked_review()
        wrong = [{**c, "verdict": "wrong"} if c["caption"] == "c0003" else c for c in captions]
        calls = [
            response("gloss", {"page": 1, "lines": lines}),
            response("judge", {"page": 1, "captions": wrong, "missing": []}),
            response("verdict", {"approved": True, "reason": "Close enough."}),
            response("edit_captions", {"changes": [{"caption": "c0003", "text": "Line 3, corrected.", "align_to": ["u00003"]}]}),
            response("verdict", {"approved": True, "reason": "Fixed."}),
            response("page", {"page": 1}),
            response("judge", {"page": 1, "captions": captions, "missing": []}),
            response("verdict", {"approved": True, "reason": "Every caption now matches."}),
        ]
        task = await self.request(self.foreign(verify=True), calls=calls)
        self.assertEqual(task["state"], "ready", task["data"]["message"])
        track = self.subtitles.tracks(self.owner, self.asset)[0]
        self.assertTrue(track["sync_checked"])
        served = cues_from_text((await self.client.get(track["url"])).text, "vtt")
        self.assertEqual(served[2]["text"], "Line 3, corrected.")
        self.assertAlmostEqual(served[2]["start"], VOICE[2], delta=0.02)
        original = cues_from_text((await self.client.get(track["original_url"])).text, "vtt")
        self.assertEqual(original[2]["text"], "Line 3 of the dialogue.")
        session = self.service.store.get_session(task["data"]["review_session"])
        errors = [
            block["content"]
            for message in session.messages
            if message["role"] == "user" and isinstance(message["content"], list)
            for block in message["content"]
            if block.get("is_error")
        ]
        self.assertIn("Wrong captions remain", errors[0])
        self.assertIn("Pages not yet judged", errors[1])

    async def test_agent_writes_subtitles_when_none_exist(self):
        await self.manager(review=True)
        written = [
            {"speech": [f"u{n:05d}"], "text": f"Written line {n}."} for n in range(1, 13)
        ]
        calls = [
            response("overview", {}),
            response("write_page", {"page": 1, "captions": written}),
            response("use_written", {}),
            response("verdict", {"approved": True, "reason": "Written from the dialogue."}),
        ]
        body = {"language": "fr", "audio_index": self.facts["audio_tracks"][1]["index"], "verify": True}
        task = await self.request(body, calls=calls)
        self.assertEqual(task["state"], "ready", task["data"]["message"])
        self.assertEqual(task["data"]["message"], "Subtitles written from the dialogue and checked.")
        track = self.subtitles.tracks(self.owner, self.asset)[0]
        self.assertEqual(track["source"], "written")
        self.assertTrue(track["sync_checked"])
        served = cues_from_text((await self.client.get(track["url"])).text, "vtt")
        self.assertEqual(len(served), 12)
        self.assertAlmostEqual(served[4]["start"], VOICE[4], delta=0.02)
        self.assertGreaterEqual(served[4]["end"] - served[4]["start"], 1.0)
        playback = await self.start(audio_index=self.facts["audio_tracks"][1]["index"])
        self.assertTrue(any("written by Sparrow" in t["title"] for t in playback["subtitles"]))

    async def test_missing_key_leaves_subtitles_playable_and_pending(self):
        await self.manager()
        task = await self.request(self.foreign(verify=True))
        self.assertEqual(task["state"], "review_pending")
        logged = self.storage.operations.page(self.owner, category="subtitle")
        self.assertEqual(logged["entries"][0]["severity"], "warning")
        track = self.subtitles.tracks(self.owner, self.asset)[0]
        self.assertEqual((await self.client.get(track["url"])).status_code, 200)
        self.assertFalse(track["sync_checked"])
        prefs = self.accounts.resolve(self.owner["id"])["values"]
        self.assertFalse(
            self.subtitles.satisfies(
                self.owner, self.asset, {**prefs, "verify_subtitles": True}
            )
        )

    async def test_cancel_during_preparation_discards_the_late_track(self):
        manager = await self.manager(True)
        entered = asyncio.Event()
        release = asyncio.Event()
        real = self.nodes.execute

        async def execute(node, kind, args, **kw):
            if kind == "subtitle_prepare":
                entered.set()
                await release.wait()
            return await real(node, kind, args, **kw)

        with patch.object(self.nodes, "execute", side_effect=execute):
            result = await self.client.post(
                f"/api/v1/assets/{self.asset}/subtitles/repair",
                json={"text": srt(VOICE)},
            )
            await asyncio.wait_for(entered.wait(), 10)
            await self.client.delete("/api/v1/subtitles/tasks/" + result.json()["id"])
            release.set()
            await asyncio.gather(*list(manager.tasks.values()), return_exceptions=True)
        self.assertEqual(manager.task(result.json()["id"])["state"], "cancelled")
        self.assertEqual(manager.tracks(self.owner, self.asset), [])


class CaptionTextTests(unittest.TestCase):
    def test_plain_caption_sanitization_and_invalid_timing(self):
        text = "1\n00:00:01,000 --> 00:00:02,000\n<script>attack()</script> & text\n"
        result = render(cues_from_text(text, "srt"), vtt=True)
        self.assertNotIn("<script>", result)
        with self.assertRaises(ValueError):
            cues_from_text("1\n00:00:03,000 --> 00:00:02,000\nbad\n", "srt")

    def test_styled_files_play_in_time_order_without_vector_shapes(self):
        ass = (
            "[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:05.00,0:00:07.00,Default,,0,0,0,,Hello there\n"
            "Dialogue: 0,0:00:01.00,0:00:03.00,Sign,,0,0,0,,{\\p1}m 0 0 l 100 0 100 100{\\p0}\n"
            "Dialogue: 0,0:00:02.00,0:00:03.00,Sign,,0,0,0,,{\\pos(10,10)}A sign\n"
        )
        cues = cues_from_text(ass, "ass")
        self.assertEqual([c["text"] for c in cues], ["A sign", "Hello there"])


def utterance(n, t, text="発話"):
    return {
        "id": f"u{n:05d}",
        "start": t,
        "end": t + 1,
        "onset": t + ONSET_BIAS,
        "onset_source": "speech_detector",
        "clean_onset": True,
        "text": text,
        "flags": [],
    }


class RetimeTests(unittest.TestCase):
    def test_sections_follow_their_measured_offsets(self):
        cues = [{"start": float(t), "end": t + 1.0, "text": "x"} for t in range(0, 600, 10)]
        measured = {
            "measurable": True,
            "consistent": True,
            "offset": 0.1,
            "sections": [
                {"start": 0, "end": 180, "pairs": 10, "offset": 0.0},
                {"start": 180, "end": 360, "pairs": 10, "offset": 0.4},
                {"start": 360, "end": 540, "pairs": 2, "offset": 3.0},
            ],
        }
        moved, _ = review.retime(cues, measured, "sections")
        self.assertEqual(moved[1]["start"], 10.0)
        self.assertAlmostEqual(moved[20]["start"], 199.6)
        # A section with too few measurements follows its nearest measured one.
        self.assertAlmostEqual(moved[40]["start"], 399.6)
        shifted, _ = review.retime(cues, measured, "shift")
        self.assertAlmostEqual(shifted[1]["start"], 9.9)
        with self.assertRaises(ValueError):
            review.retime(cues, {"measurable": False}, "sections")


class EditCarryTests(unittest.TestCase):
    def test_replace_fixes_systematic_errors_and_keeps_verdicts(self):
        utterances = [utterance(n, 10.0 * n) for n in range(1, 4)]
        cues = [
            {"start": 10.0, "end": 12.0, "text": "l'm fine."},
            {"start": 20.0, "end": 22.0, "text": "Hello, Isla."},
            {"start": 30.0, "end": 32.0, "text": "lt's late."},
        ]
        pages = review.build_pages(utterances, cues, 40)
        state = {
            "judgements": {"1": {review.caption_id(i): {"verdict": "ok", "speech": [f"u{i + 1:05d}"], "note": ""} for i in range(3)}},
            "missing": {"1": []},
        }
        new, _, renamed = review.edit(
            cues, utterances, [], [], [], [{"find": "l'm", "with": "I'm"}, {"find": "lt's", "with": "It's"}]
        )
        self.assertEqual([c["text"] for c in new], ["I'm fine.", "Hello, Isla.", "It's late."])
        judgements, _, reopen = review.carry(pages, cues, pages, new, state, renamed)
        self.assertEqual(reopen, [])
        self.assertEqual(len(judgements["1"]), 3)
        # A reworded caption needs a new verdict; the others keep theirs.
        reworded, _, _ = review.edit(cues, utterances, [], [{"caption": "c0002", "text": "Hi."}], [])
        judgements, _, reopen = review.carry(pages, cues, pages, reworded, state, {})
        self.assertEqual(reopen, [1])
        self.assertEqual(review.unjudged(pages[0], reworded, judgements), ["c0002"])

    def test_signs_need_no_speech_and_are_not_held_to_it(self):
        utterances = [utterance(1, 50.0)]
        cues = [{"start": 5.0, "end": 8.0, "text": "The Bird"}, {"start": 50.0, "end": 52.0, "text": "Hello."}]
        pages = review.build_pages(utterances, cues, 60)
        judged, _ = review.check_judgement(
            pages[0], utterances, cues, [],
            [{"caption": "c0001", "verdict": "sign", "speech": []}, {"caption": "c0002", "verdict": "ok", "speech": ["u00001"]}],
            [],
        )
        state = {"judgements": {"1": judged}, "missing": {"1": []}, "listens": []}
        measured = {"measurable": False}
        self.assertEqual(review.gate(state, pages, cues, utterances, "full", measured), [])


class ReviewPageTests(unittest.TestCase):
    def setUp(self):
        self.utterances = [utterance(n, 4.0 * n) for n in range(1, 200)]
        self.cues = [{"start": 4.0 * n, "end": 4.0 * n + 2, "text": f"Caption {n}"} for n in range(1, 200)]
        self.pages = review.build_pages(self.utterances, self.cues, 800)

    def test_pages_cover_every_line_once_and_fit_inline(self):
        seen = [u["id"] for p in self.pages for u in review.page_utterances(p, self.utterances)]
        self.assertEqual(sorted(seen), sorted(u["id"] for u in self.utterances))
        for page in self.pages:
            glosses = {u["id"]: "an English reading of this line" for u in self.utterances}
            view = review.revealed_view(page, len(self.pages), self.utterances, self.cues, glosses, {}, [])
            self.assertLess(len(view), 6000)

    def test_gloss_and_judgement_must_cover_the_page(self):
        page = self.pages[0]
        on_page = review.page_utterances(page, self.utterances)
        partial = review.check_gloss(page, self.utterances, [{"speech": on_page[0]["id"], "english": "x"}])
        self.assertFalse(review.glossed(page, self.utterances, partial))
        everything = {u["id"]: "x" for u in on_page}
        self.assertTrue(review.glossed(page, self.utterances, everything))
        with self.assertRaisesRegex(ValueError, "not recognised speech"):
            review.check_gloss(page, self.utterances, [{"speech": "u09999", "english": "x"}])
        captions = review.page_captions(page, self.cues)
        entries = [
            {"caption": review.caption_id(i), "verdict": "ok", "speech": [on_page[k]["id"]]}
            for k, (i, _) in enumerate(captions)
        ]
        judged, gaps = review.check_judgement(page, self.utterances, self.cues, [], entries, [])
        self.assertEqual(len(judged), len(captions))
        with self.assertRaisesRegex(ValueError, "Cite the speech"):
            review.check_judgement(
                page, self.utterances, self.cues, [], [{**entries[0], "speech": []}] + entries[1:], []
            )

    def test_gate_refuses_mismatched_sections_gaps_and_far_matches(self):
        state = {"judgements": {}, "missing": {}, "listens": []}
        for page in self.pages:
            state["judgements"][str(page["number"])] = {
                review.caption_id(i): {"verdict": "ok", "speech": [f"u{i + 1:05d}"], "note": ""}
                for i, _ in review.page_captions(page, self.cues)
            }
        measured = {"measurable": True, "consistent": True, "offset": 0.0}
        self.assertEqual(review.gate(state, self.pages, self.cues, self.utterances, "full", measured), [])
        first = state["judgements"]["1"]
        key = next(iter(first))
        first[key] = {**first[key], "verdict": "wrong"}
        reasons = review.gate(state, self.pages, self.cues, self.utterances, "full", measured)
        self.assertTrue(any("Wrong captions remain" in r for r in reasons))
        first[key] = {**first[key], "verdict": "ok", "speech": ["u00150"]}
        reasons = review.gate(state, self.pages, self.cues, self.utterances, "full", measured)
        self.assertTrue(any("nowhere near" in r for r in reasons))
        first[key] = {**first[key], "speech": [f"u{review.caption_index(key) + 1:05d}"]}
        state["missing"]["2"] = [{"speech": ["u00050"], "note": "uncaptioned"}]
        reasons = review.gate(state, self.pages, self.cues, self.utterances, "full", measured)
        self.assertTrue(any("no caption" in r for r in reasons))
        self.assertFalse(any("no caption" in r for r in review.gate(state, self.pages, self.cues, self.utterances, "forced", measured)))
        state["missing"]["2"] = []
        late = {"measurable": True, "consistent": True, "offset": 0.4, "sections": []}
        self.assertTrue(any("overall" in r for r in review.gate(state, self.pages, self.cues, self.utterances, "full", late)))
        uneven = {**measured, "sections": [{"start": 0, "end": 180, "pairs": 8, "offset": 0.3}]}
        self.assertTrue(any("Section" in r for r in review.gate(state, self.pages, self.cues, self.utterances, "full", uneven)))




if __name__ == "__main__":
    unittest.main()


class PageCheckerTests(unittest.TestCase):
    def test_blank_overrides_use_the_defaults_and_off_disables(self):
        from backend.agents import subtitles as module

        blank = {"OPENAI_API_KEY": "key", "SPARROW_SUBTITLE_CONTRACTOR": "", "SPARROW_SUBTITLE_VERIFIER": " "}
        with patch.dict("os.environ", blank):
            self.assertEqual((module.contractor_model(), module.verifier_model()), (module.DEFAULT_CONTRACTOR, module.DEFAULT_VERIFIER))
        with patch.dict("os.environ", {**blank, "SPARROW_SUBTITLE_VERIFIER": "off"}):
            self.assertEqual(module.verifier_model(), "")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "", "SPARROW_SUBTITLE_CONTRACTOR": ""}):
            self.assertEqual(module.contractor_model(), "")

    def setUp(self):
        self.utterances = [utterance(n, 4.0 * n, f"発話{n}") for n in range(1, 40)]
        self.pages = review.build_pages(self.utterances, [], 170)

    def test_quoted_text_and_order_recover_lines_and_retries_are_bounded(self):
        from backend.agents import subtitle_contract

        page = self.pages[0]
        on_page = review.page_utterances(page, self.utterances)
        answers = [
            {"lines": [{"id": u["text"], "english": "quoted"} for u in on_page[:-2]]},
            {"lines": [{"id": "", "english": "by order"} for _ in on_page]},
        ]
        calls = []

        async def checker(system, prompt, schema):
            calls.append(prompt)
            return answers[len(calls) - 1], {"output_tokens": 1}

        found = asyncio.run(
            subtitle_contract.check_pages(checker, [page], self.utterances, [], {}, "ja", compare=False)
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual({found["translations"][u["id"]] for u in on_page[:-2]}, {"quoted"})
        self.assertEqual({found["translations"][u["id"]] for u in on_page[-2:]}, {"by order"})
        self.assertEqual(found["spend"]["failures"], [])

        async def refuses(system, prompt, schema):
            return {"lines": []}, {}

        silent = asyncio.run(subtitle_contract.check_pages(refuses, [page], self.utterances, [], {}, "ja", compare=False))
        gaps = subtitle_contract.untranslated([page], self.utterances, silent["translations"])
        self.assertEqual([g["speech"] for g in gaps[str(page["number"])]], [[u["id"] for u in on_page]])
        self.assertTrue(silent["spend"]["failures"])
