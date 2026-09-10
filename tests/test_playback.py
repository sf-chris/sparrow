import base64
import secrets
import tempfile
import unittest
from pathlib import Path
import httpx
from fastapi import FastAPI
from backend.storage import Storage
from backend.models import SparrowConfig, LibraryItem, MediaType
from backend.agents.account_api import install_accounts, COOKIE
from backend.agents.nodes import Nodes
from backend.agents.catalogue import Catalogue
from backend.agents.playback import install_playback, byte_range
from backend.agents.node_executor import executable, run_media, probe_file


async def make_video(folder, seconds=24):
    subtitle = folder / "original.srt"
    subtitle.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nFirst subtitle.\n\n2\n00:00:07,000 --> 00:00:10,000\nA second caption.\n\n3\n00:00:19,000 --> 00:00:22,000\nThe final subtitle.\n"
    )
    source = folder / "fixture.mp4"
    await run_media(
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=s=320x240:r=24",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880",
        "-i",
        subtitle,
        "-t",
        str(seconds),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "2:a",
        "-map",
        "3:0",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-c:s",
        "mov_text",
        "-metadata:s:a:0",
        "language=eng",
        "-metadata:s:a:1",
        "language=spa",
        "-metadata:s:s:0",
        "language=eng",
        "-movflags",
        "+faststart",
        "-y",
        source,
    )
    return source


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not executable("ffmpeg") or not executable("ffprobe"):
            self.skipTest("Packaged media tools required")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.storage = Storage(str(self.root / "server"))
        await self.storage.load_all()
        await self.storage.save_config(
            SparrowConfig(
                library_dir=str(self.library), staging_dir=str(self.root / "incoming")
            )
        )
        self.app = FastAPI()
        self.accounts = install_accounts(self.app, self.storage, lambda: None)
        self.owner = self.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )
        self.nodes = Nodes(self.storage, lambda: None)
        self.catalogue = Catalogue(self.storage, self.nodes)
        install_playback(
            self.app, self.storage, self.accounts, self.nodes, self.catalogue
        )
        self.source = await make_video(self.library)
        self.facts = await probe_file(self.source)
        await self.storage.add_library_item(
            LibraryItem(
                id="movie",
                title="Fixture",
                media_type=MediaType.MOVIE,
                path=str(self.source),
            )
        )
        self.asset = self.catalogue.save_asset(
            "movie", "local", "library", "fixture.mp4", self.facts
        )
        self.client = self.browser(self.owner)

    def browser(self, user):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
            headers={"X-Sparrow-Request": "1"},
            cookies={COOKIE: self.accounts.new_session(user["id"])},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        hls = self.nodes.local()._hls_cache
        if hls:
            for identity in list(hls.jobs):
                await hls.stop(identity)
        self.temp.cleanup()

    async def start(self, **values):
        response = await self.client.post(
            "/api/v1/playback", json={"asset_id": self.asset, **values}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_direct_ranges_captions_and_progress_ordering(self):
        first = await self.start()
        self.assertEqual(first["mode"], "direct")
        response = await self.client.get(first["url"], headers={"Range": "bytes=20-79"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, self.source.read_bytes()[20:80])
        self.assertEqual(
            response.headers["content-range"],
            f"bytes 20-79/{self.source.stat().st_size}",
        )
        suffix = await self.client.get(first["url"], headers={"Range": "bytes=-16"})
        self.assertEqual(suffix.content, self.source.read_bytes()[-16:])
        self.assertEqual(
            (
                await self.client.get(
                    first["url"], headers={"Range": "bytes=9999999999-"}
                )
            ).status_code,
            416,
        )
        caption = await self.client.get(first["subtitles"][0]["url"])
        self.assertIn("WEBVTT", caption.text)
        self.assertIn("A second caption.", caption.text)
        endpoint = f'/api/v1/playback/{first["id"]}/progress'
        self.assertTrue(
            (
                await self.client.put(endpoint, json={"sequence": 2, "position": 12})
            ).json()["saved"]
        )
        self.assertFalse(
            (
                await self.client.put(endpoint, json={"sequence": 1, "position": 20})
            ).json()["saved"]
        )
        self.assertEqual((await self.start())["position"], 12)
        self.assertFalse(
            (
                await self.client.put(endpoint, json={"sequence": 3, "position": 20})
            ).json()["saved"]
        )
        self.assertEqual(
            self.catalogue.asset(self.owner, self.asset)["watch"]["position"], 12
        )

    async def test_media_urls_enforce_personal_sessions_scope_and_revocation(self):
        first = await self.start()
        invitation = self.accounts.invite("viewer", [])
        user = self.accounts.create_user(
            "viewer", "fixture-password-456", "Viewer", invitation=invitation
        )
        async with self.browser(user) as viewer:
            self.assertEqual((await viewer.get(first["url"])).status_code, 404)
            self.assertEqual(
                (
                    await viewer.post("/api/v1/playback", json={"asset_id": self.asset})
                ).status_code,
                404,
            )
        self.accounts.revoke(self.client.cookies.get(COOKIE))
        self.assertEqual((await self.client.get(first["url"])).status_code, 401)

    async def test_format_fallback_prepares_real_seekable_audio_selected_segments(self):
        second_audio = self.facts["audio_tracks"][1]["index"]
        session = await self.start(audio_index=second_audio, position=18)
        self.assertEqual(session["mode"], "hls")
        playlist = await self.client.get(session["url"])
        self.assertIn("#EXT-X-ENDLIST", playlist.text)
        prefix = session["url"].rsplit("/", 1)[0]
        for index in (3, 0):
            segment = await self.client.get(f"{prefix}/{index}.ts")
            self.assertEqual(
                segment.status_code,
                200,
                segment.text[:200] if segment.status_code != 200 else "",
            )
            self.assertGreater(len(segment.content), 1000)
            path = self.root / f"segment-{index}.ts"
            path.write_bytes(segment.content)
            facts = await probe_file(path)
            self.assertEqual(facts["video_codec"], "h264")
            self.assertEqual(facts["audio_tracks"][0]["codec"], "aac")
        self.assertEqual(
            (await self.client.delete(f'/api/v1/playback/{session["id"]}')).status_code,
            200,
        )
        self.assertEqual((await self.client.get(f"{prefix}/0.ts")).status_code, 404)

    async def test_changed_file_cannot_start_or_resume_as_verified(self):
        session = await self.start()
        self.source.write_bytes(b"replacement")
        self.assertEqual(
            (
                await self.client.post(
                    "/api/v1/playback", json={"asset_id": self.asset}
                )
            ).status_code,
            409,
        )
        self.assertEqual((await self.client.get(session["url"])).status_code, 409)


class RangeTests(unittest.TestCase):
    def test_range_boundaries(self):
        self.assertEqual(byte_range("bytes=8-100", 10), (8, 9, True))
        self.assertEqual(byte_range("bytes=-100", 10), (0, 9, True))
        for header in (
            "bytes=-0",
            "bytes=11-",
            "bytes=6-5",
            "bytes=1-2,4-5",
            "bytes=-",
            "bytes=abc",
        ):
            with self.subTest(header=header), self.assertRaises(ValueError):
                byte_range(header, 10)
