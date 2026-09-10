import asyncio
import base64
import os
import secrets
import tempfile
import time
import unittest
from pathlib import Path

from backend.agents.node_executor import (
    Executor,
    NodeError,
    executable,
    run_media,
    probe_file,
    file_version,
)
from backend.agents.nodes import Nodes
from backend.agents.catalogue import Catalogue
from backend.models import SparrowConfig
from backend.storage import Storage


class NodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library, self.staging = self.root / "library", self.root / "staging"
        self.library.mkdir()
        self.staging.mkdir()
        self.executor = Executor(
            self.root / "node",
            {"library": str(self.library), "staging": str(self.staging)},
        )
        self.storage = Storage(str(self.root / "server"))
        await self.storage.load_all()
        await self.storage.save_config(
            SparrowConfig(library_dir=str(self.library), staging_dir=str(self.staging))
        )
        self.nodes = Nodes(self.storage, lambda: None)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def command(self, kind, args, identity=None):
        return await self.executor.execute(
            {
                "id": identity or secrets.token_hex(12),
                "kind": kind,
                "args": args,
                "expires": time.time() + 60,
            }
        )

    async def fixture(self, path):
        if not executable("ffmpeg") or not executable("ffprobe"):
            self.skipTest("Real media checks require packaged ffmpeg/ffprobe")
        await run_media(
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-metadata:s:a:0",
            "language=eng",
            "-movflags",
            "+faststart",
            "-y",
            path,
        )

    def test_paths_reject_escape_reserved_names_and_symlinks(self):
        for relative in (
            "../outside",
            "/etc/passwd",
            "C:/Windows/file",
            "folder/../outside",
            "file:stream",
            "CON.mkv",
            "folder./file",
            "a\\b",
            ".sparrow-root-id",
        ):
            with self.subTest(relative=relative), self.assertRaises(NodeError):
                self.executor.path("library", relative, write=True)
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.library / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation is unavailable to this Windows account")
        with self.assertRaises(NodeError):
            self.executor.path("library", "link/file.mkv", write=True)

    async def test_durable_receipts_deduplicate_and_reject_changed_arguments(self):
        (self.library / "fixture.mp4").write_bytes(b"0123456789")
        args = {"root_id": "library", "path": "fixture.mp4", "offset": 2, "length": 4}
        result = await self.command("read", args, "repeat")
        self.assertEqual(base64.b64decode(result["value"]["bytes"]), b"2345")
        restarted = Executor(self.root / "node", {"library": str(self.library)})
        again = await restarted.execute(
            {"id": "repeat", "kind": "read", "args": args, "expires": 0}
        )
        self.assertEqual(result, again)
        with self.assertRaises(NodeError):
            await restarted.execute(
                {
                    "id": "repeat",
                    "kind": "read",
                    "args": {**args, "offset": 4},
                    "expires": time.time() + 60,
                }
            )
        self.assertEqual(len(restarted.pending_results()), 1)
        restarted.delivered("repeat")
        self.assertEqual(restarted.pending_results(), [])

    async def test_expired_authority_and_changed_file_are_refused(self):
        path = self.library / "fixture.mp4"
        path.write_bytes(b"123")
        version = file_version(path)
        path.write_bytes(b"changed")
        result = await self.command("read", {"path": "fixture.mp4", "version": version})
        self.assertFalse(result["ok"])
        expired = await self.executor.execute(
            {"id": "expired", "kind": "list", "args": {}, "expires": 0}
        )
        self.assertFalse(expired["ok"])

    def test_replaced_volume_is_unavailable_instead_of_empty(self):
        (self.library / ".sparrow-root-id").write_text("different")
        executor = Executor(
            self.root / "marked",
            {"library": str(self.library)},
            {"library": "expected"},
        )
        self.assertFalse(executor.capabilities()["roots"][0]["available"])
        with self.assertRaises(NodeError):
            executor.path("library")

    async def test_real_publication_keeps_source_and_cannot_overwrite_another_file(
        self,
    ):
        folder = self.staging / "job"
        folder.mkdir()
        source = folder / "fixture.mp4"
        await self.fixture(source)
        args = {
            "root_id": "staging",
            "path": "job/fixture.mp4",
            "destination": "Fixture/movie.mp4",
            "version": file_version(source),
        }
        result = await self.command("publish", args, "publication")
        self.assertTrue(result["ok"], result)
        target = self.library / "Fixture/movie.mp4"
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), target.read_bytes())
        restarted = Executor(self.root / "node", self.executor.roots)
        replay = await restarted.execute(
            {
                "id": "publication",
                "kind": "publish",
                "args": args,
                "expires": time.time() + 60,
            }
        )
        self.assertEqual(result, replay)
        target.write_bytes(b"another existing movie")
        refused = await self.command("publish", args)
        self.assertFalse(refused["ok"])
        self.assertEqual(target.read_bytes(), b"another existing movie")
        self.assertTrue(source.exists())

    async def test_pairing_pending_commands_and_result_replay_survive_restart(self):
        enrollment = self.nodes.enroll("Windows fixture")
        credential = secrets.token_urlsafe(32)
        node_id = self.nodes.pair(
            enrollment["code"], credential, self.executor.capabilities()
        )["node_id"]
        self.assertEqual(self.nodes.authenticate(credential), node_id)
        with self.assertRaises(NodeError):
            self.nodes.pair(
                enrollment["code"],
                secrets.token_urlsafe(32),
                self.executor.capabilities(),
            )
        task = asyncio.create_task(
            self.nodes.execute(
                node_id, "capabilities", timeout=5, operation_id="durable"
            )
        )
        await asyncio.sleep(0.01)
        restarted = Nodes(self.storage, lambda: None)
        command = restarted.next_command(node_id)
        self.assertEqual(command["id"], "durable")
        result = await self.executor.execute(command)
        restarted.finish(node_id, command["id"], result)
        restarted.finish(node_id, command["id"], result)
        self.assertEqual((await task)["protocol"], 1)
        with self.assertRaises(NodeError):
            restarted.finish("another-node", command["id"], result)

    async def test_import_preview_confirm_and_rescan_preserve_identity(self):
        path = self.library / "Fixture.mp4"
        await self.fixture(path)
        catalogue = Catalogue(self.storage, self.nodes)
        owner = catalogue.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )

        async def title(*args):
            return {"title": "Fixture", "id": 42}

        preview = await catalogue.scan("local")
        self.assertEqual(len(preview["candidates"]), 1)
        confirmed = await catalogue.confirm_import(
            preview["id"],
            [
                {
                    "id": preview["candidates"][0]["id"],
                    "media_type": "movie",
                    "tmdb_id": 42,
                }
            ],
            title,
        )
        asset = catalogue.assets(owner)[0]
        self.assertEqual(asset["state"], "ready")
        self.assertEqual(asset["facts"]["audio_languages"], ["eng"])
        preview2 = await catalogue.scan("local")
        again = await catalogue.confirm_import(
            preview2["id"],
            [
                {
                    "id": preview2["candidates"][0]["id"],
                    "media_type": "movie",
                    "tmdb_id": 42,
                }
            ],
            title,
        )
        self.assertEqual(confirmed, again)
        self.assertEqual(len(catalogue.assets(owner)), 1)
        path.unlink()
        self.assertEqual(catalogue.assets(owner)[0]["state"], "unavailable")
        self.assertEqual(len(self.storage.get_library()), 1)

    async def test_import_correction_reuses_asset_and_removes_empty_previous_match(self):
        path=self.library/'Correctable.mp4';await self.fixture(path)
        catalogue=Catalogue(self.storage,self.nodes)
        owner=catalogue.accounts.create_user('owner','fixture-password-123','Owner',bootstrap=True)
        async def title(kind,identity):return {'id':identity,'title':f'Film {identity}'}
        scan=await catalogue.scan('local');selection={'id':scan['candidates'][0]['id'],'media_type':'movie','tmdb_id':42}
        first=await catalogue.confirm_import(scan['id'],[selection],title)
        second=await catalogue.confirm_import(scan['id'],[{**selection,'tmdb_id':43}],title)
        self.assertEqual(first['imported'][0]['asset_id'],second['imported'][0]['asset_id'])
        self.assertEqual(len(self.storage.get_library()),1)
        self.assertEqual(self.storage.get_library()[0].tmdb_id,43)
        self.assertTrue(path.exists());self.assertEqual(len(catalogue.assets(owner)),1)
        mixed=await catalogue.confirm_import(scan['id'],[selection,{**selection,'id':'not-in-preview'}],title)
        self.assertEqual(len(mixed['imported']),1);self.assertEqual(len(mixed['failed']),1)
