"""HTTP contract and failure boundaries; no provider account or network needed."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from backend.agents.runtime import ToolError
from backend.agents.subtitle_provider import SubtitleProvider, MAX_SUBTITLE_BYTES
from backend.models import MediaType


class SubtitleProviderTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, handler):
        client = httpx.AsyncClient
        return patch(
            "backend.agents.subtitle_provider.httpx.AsyncClient",
            side_effect=lambda **kwargs: client(
                transport=httpx.MockTransport(handler), **kwargs
            ),
        )

    async def test_search_requires_matching_title_episode_language_and_style(self):
        def candidate(identity, **overrides):
            attributes = {
                "language": "en",
                "files": [{"file_id": identity}],
                "feature_details": {
                    "parent_tmdb_id": 42,
                    "season_number": 2,
                    "episode_number": 3,
                },
                **overrides,
            }
            return {"id": str(identity), "attributes": attributes}

        def handle(request):
            self.assertEqual(request.url.params["parent_tmdb_id"], "42")
            self.assertEqual(request.url.params["episode_number"], "3")
            return httpx.Response(
                200,
                json={
                    "data": [
                        candidate(1, language="es"),
                        candidate(2, hearing_impaired=True),
                        candidate(
                            3,
                            feature_details={
                                "parent_tmdb_id": 99,
                                "season_number": 2,
                                "episode_number": 3,
                            },
                        ),
                        candidate(
                            4,
                            feature_details={
                                "parent_tmdb_id": 42,
                                "season_number": 2,
                                "episode_number": 4,
                            },
                        ),
                        candidate(5),
                        candidate(6, moviehash_match=True),
                        candidate(7),
                        candidate(8),
                    ]
                },
            )

        with self.transport(handle):
            result = await SubtitleProvider({"api_key": "fixture"}).search(
                {"season": 2, "episode": 3},
                SimpleNamespace(tmdb_id=42, title="Fixture", media_type=MediaType.TV),
                "en",
                "full",
                "hash",
            )
        self.assertEqual([r["file_id"] for r in result], [6, 5, 7])

    async def test_download_keeps_credentials_on_api_and_decodes_text(self):
        calls = []

        def handle(request):
            calls.append(request)
            if request.url.path.endswith("/login"):
                return httpx.Response(200, json={"token": "fixture-token"})
            if request.url.path == "/api/v1/download":
                self.assertEqual(
                    request.headers["Authorization"], "Bearer fixture-token"
                )
                self.assertEqual(json.loads(request.content)["file_id"], 7)
                return httpx.Response(
                    200, json={"link": "https://dl.opensubtitles.com/fixture.srt"}
                )
            self.assertNotIn("Authorization", request.headers)
            self.assertNotIn("Api-Key", request.headers)
            return httpx.Response(
                200, text="1\n00:00:01,000 --> 00:00:02,000\nHello.\n"
            )

        with self.transport(handle):
            text = await SubtitleProvider(
                {"api_key": "fixture", "username": "fixture", "password": "fixture"}
            ).download(7)
        self.assertIn("Hello.", text)
        self.assertEqual(len(calls), 3)

    async def test_quota_failure_does_not_attempt_a_file_download(self):
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(406, json={"message": "Quota used"})

        with self.transport(handle), self.assertRaisesRegex(ToolError, "allowance"):
            await SubtitleProvider({"api_key": "fixture"}).download(7)
        self.assertEqual(len(calls), 1)

    async def test_download_rejects_external_addresses_redirects_and_oversized_files(
        self,
    ):
        for link, status, content, message in [
            ("https://elsewhere.example/fixture.srt", 200, b"", "unsupported"),
            (
                "https://user:secret@dl.opensubtitles.com/fixture.srt",
                200,
                b"",
                "unsupported",
            ),
            ("https://dl.opensubtitles.com/fixture.srt", 302, b"", "expired"),
            (
                "https://dl.opensubtitles.com/fixture.srt",
                200,
                b"x" * (MAX_SUBTITLE_BYTES + 1),
                "2 MB",
            ),
        ]:
            with self.subTest(link=link, status=status, size=len(content)):

                def handle(request):
                    if request.url.path == "/api/v1/download":
                        return httpx.Response(200, json={"link": link})
                    self.assertEqual(request.url.host, "dl.opensubtitles.com")
                    return httpx.Response(
                        status,
                        content=content,
                        headers={"Location": "https://elsewhere.example"},
                    )

                with self.transport(handle), self.assertRaisesRegex(ToolError, message):
                    await SubtitleProvider({"api_key": "fixture"}).download(7)
