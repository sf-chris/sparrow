"""Changing where a computer keeps titles: point, add, or move everything across."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.agents.node_executor import Executor, NodeError
from backend.agents.relocation import FolderChange, Relocations, retiring, status
from test_moves import MoveFixture

SHOW = "Fixture show/Season 01"


class RelocationTests(MoveFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.relocations = Relocations(self.moves)
        self.moves.relocations = self.relocations
        self.new = self.root / "new"

    def asset(self, n=0):
        return self.catalogue.asset(self.owner, self.assets[n])

    async def settle(self):
        for _ in range(6):
            await self.moves.run_pending()
            if not self.relocations.get("local"):
                return
        self.fail("the titles never finished moving")

    async def test_moving_on_one_disk_links_every_title_then_stops_using_the_old_folder(self):
        original = os.stat(self.root / "one" / SHOW / "S01E01.mkv").st_ino
        with self.assertRaisesRegex(FolderChange, "whether the titles"):
            await self.relocations.change_library(self.owner, "local", str(self.new))
        await self.relocations.change_library(self.owner, "local", str(self.new), "move")

        # New titles go to the new folder at once; the old one waits at the end of the list.
        config = self.storage.get_config()
        self.assertEqual(config.library_dir, str(self.new))
        self.assertEqual(config.extra_library_dirs, [str(self.root / "two"), str(self.root / "one")])
        self.assertEqual(self.asset()["root_id"], "library-3")
        self.assertEqual(self.asset()["state"], "ready")  # Still plays where it is.
        self.assertEqual(retiring(self.accounts), {("local", "library-3"): "library"})
        disk = next(d for d in self.placement.disks() if d["key"] == "local/library-3")
        self.assertEqual((disk["storage"], disk["moving_to"]), (False, "local/library"))
        self.assertEqual(status(self.accounts, "local")["total"], 1)
        with self.assertRaisesRegex(FolderChange, "still moving"):
            await self.relocations.change_library(self.owner, "local", str(self.root / "other"), "move")

        await self.settle()
        moved = self.new / SHOW / "S01E01.mkv"
        self.assertTrue(moved.is_file())
        self.assertEqual(os.stat(moved).st_ino, original)  # The same bytes, renamed.
        self.assertTrue((self.new / SHOW / "S01E01.en.srt").is_file())
        self.assertFalse((self.root / "one" / SHOW / "S01E01.mkv").exists())
        self.assertFalse((self.root / "one" / SHOW / "S01E01.en.srt").exists())
        config = self.storage.get_config()
        self.assertEqual(config.extra_library_dirs, [str(self.root / "two")])
        self.assertIsNone(status(self.accounts, "local"))
        self.assertEqual((self.asset()["root_id"], self.asset()["state"]), ("library", "ready"))
        self.assertEqual(self.asset()["watch"]["position"], 600)
        item = self.storage.get_library_item(self.item.id)
        self.assertEqual(item.episodes["1"]["1"]["path"], str(moved))
        # The household's list of moves isn't flooded; the title's own history has it.
        self.assertEqual(self.moves.list(), [])
        self.assertEqual(len(self.moves.list(self.item.id)), 1)

    async def test_another_disk_gets_a_checked_copy(self):
        original = os.stat(self.root / "one" / SHOW / "S01E01.mkv").st_ino

        async def elsewhere(executor, root_id, args):
            raise NodeError("These folders are on different disks.")

        with mock.patch.object(Executor, "_link", elsewhere):
            await self.relocations.change_library(self.owner, "local", str(self.new), "move")
            await self.settle()
        moved = self.new / SHOW / "S01E01.mkv"
        self.assertNotEqual(os.stat(moved).st_ino, original)
        self.assertFalse((self.root / "one" / SHOW / "S01E01.mkv").exists())
        self.assertEqual((self.asset()["root_id"], self.asset()["state"]), ("library", "ready"))

    async def test_new_titles_skip_the_folder_being_emptied(self):
        await self.relocations.change_library(self.owner, "local", str(self.new), "move")
        for destination in ("auto", "local/library-3"):
            choice = self.placement.choose(
                self.owner, destination, tmdb_id=7, media_type="movie", acquire=False, needed=1
            )
            self.assertNotEqual((choice["node_id"], choice["root_id"]), ("local", "library-3"))
        # More of the show goes where the rest of it is going.
        more = self.placement.choose(
            self.owner, "auto", tmdb_id=42, media_type="tv", acquire=False, needed=1
        )
        self.assertEqual((more["node_id"], more["root_id"]), ("local", "library"))
        self.assertNotIn("local/library-3", [d["value"] for c in self.placement.destinations(self.owner)["computers"] for d in c["disks"]])

    async def test_using_both_folders_moves_nothing(self):
        await self.relocations.change_library(self.owner, "local", str(self.new), "both")
        config = self.storage.get_config()
        self.assertEqual(config.library_dir, str(self.root / "one"))
        self.assertEqual(config.extra_library_dirs, [str(self.root / "two"), str(self.new)])
        self.assertEqual(self.asset()["root_id"], "library")
        self.assertIsNone(self.relocations.get("local"))
        with self.assertRaisesRegex(FolderChange, "already keeps titles"):
            await self.relocations.add_library("local", str(self.new))

    async def test_a_computer_with_nothing_kept_just_points_at_the_new_folder(self):
        target = self.root / "remote-new"
        await self.relocations.change_library(self.owner, self.node_id, str(target))
        await asyncio.sleep(0.05)
        roots = {r["id"]: r for r in self.nodes.info(self.node_id)["capabilities"]["roots"]}
        self.assertEqual(roots["library"]["label"], str(target))
        self.assertEqual(roots["staging"]["label"], str(self.root / "remote-in"))
        self.assertTrue((target / ".sparrow-root-id").is_file())
        await self.relocations.add_library(self.node_id, str(self.root / "remote-two"))
        await self.relocations.change_incoming(self.node_id, str(self.root / "remote-down"))
        roots = {r["id"]: r for r in self.nodes.info(self.node_id)["capabilities"]["roots"]}
        self.assertEqual(roots["library-2"]["label"], str(self.root / "remote-two"))
        self.assertEqual(roots["staging"]["label"], str(self.root / "remote-down"))
        with self.assertRaisesRegex(FolderChange, "Incoming can’t be inside"):
            await self.relocations.change_incoming(self.node_id, str(target / "inside"))

    async def test_a_link_release_only_removes_the_same_bytes(self):
        old = self.root / "one" / SHOW / "S01E02.mkv"
        (self.root / "two" / SHOW).mkdir(parents=True)
        (self.root / "two" / SHOW / "S01E02.mkv").write_bytes(b"other")
        with self.assertRaisesRegex(NodeError, "different file already exists"):
            await self.nodes.execute(
                "local",
                "link",
                {"root_id": "library-2", "path": f"{SHOW}/S01E02.mkv", "from_root": "library",
                 "version": self.asset(1)["facts"]["version"]},
            )
        with self.assertRaisesRegex(NodeError, "changed after it was moved"):
            await self.nodes.execute(
                "local",
                "release",
                {"root_id": "library", "path": f"{SHOW}/S01E02.mkv",
                 "linked": {"root_id": "library-2", "path": f"{SHOW}/S01E02.mkv"}},
            )
        self.assertTrue(old.is_file())


class RelocationApiTests(MoveFixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        import httpx
        from fastapi import FastAPI

        from backend.agents.relocation import install_relocations

        app = FastAPI()

        @app.middleware("http")
        async def administrator(request, call_next):
            request.state.user = self.owner
            return await call_next(request)

        install_relocations(app, self.moves)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await super().asyncTearDown()

    async def test_folders_change_by_their_own_routes(self):
        new = str(self.root / "new")
        asked = await self.client.put("/api/v1/admin/nodes/local/library", json={"path": new})
        self.assertEqual(asked.status_code, 422)
        self.assertIn("whether the titles", asked.json()["detail"])
        both = await self.client.put(
            "/api/v1/admin/nodes/local/library", json={"path": new, "titles": "both"}
        )
        self.assertEqual(both.status_code, 200, both.text)
        roots = {r["id"]: r for r in both.json()["capabilities"]["roots"]}
        self.assertEqual(roots["library-3"]["label"], new)
        self.assertEqual(roots["library"]["library_titles"], 1)
        self.assertIsNone(both.json()["relocation"])
        nested = await self.client.put(
            "/api/v1/admin/nodes/local/incoming", json={"path": str(self.root / "one" / "in")}
        )
        self.assertEqual(nested.status_code, 422)
        remote = await self.client.put(
            f"/api/v1/admin/nodes/{self.node_id}/library", json={"path": str(self.root / "r2")}
        )
        self.assertEqual(remote.status_code, 200, remote.text)
        added = await self.client.post(
            f"/api/v1/admin/nodes/{self.node_id}/libraries", json={"path": str(self.root / "r3")}
        )
        self.assertEqual(
            {r["id"]: r["label"] for r in added.json()["capabilities"]["roots"] if r["id"].startswith("library")},
            {"library": str(self.root / "r2"), "library-2": str(self.root / "r3")},
        )
        self.paused = True
        with self.accounts.connect() as db:
            db.execute("UPDATE nodes SET last_seen=0")
        asleep = await self.client.put(
            f"/api/v1/admin/nodes/{self.node_id}/incoming", json={"path": str(self.root / "r4")}
        )
        self.assertEqual(asleep.status_code, 409)
        self.assertIn("offline", asleep.json()["detail"])


class AutomaticIncomingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        for folder in self.root.rglob("*"):
            if folder.is_dir():
                folder.chmod(0o755)
        self.temp.cleanup()

    async def folders(self, library):
        # A computer choosing its folders for the first time.
        executor = Executor(
            self.root / "data", {}, places=[str(self.root)], on_folders=lambda roots, markers: None
        )
        await executor._set_folders({"libraries": [str(library)], "staging": "auto"})
        return executor.roots["staging"]

    async def test_incoming_goes_beside_the_library_or_in_sparrows_own_folder(self):
        self.assertEqual(
            await self.folders(self.root / "Sparrow" / "Library"), self.root / "Sparrow" / "Incoming"
        )
        self.assertEqual(
            await self.folders(self.root / "Media"), self.root / "Sparrow Incoming"
        )
        # Chosen again, an incoming folder it already has stays.
        executor = Executor(
            self.root / "data", {"library": str(self.root / "Media"), "staging": str(self.root / "Mine")},
            places=[str(self.root)], on_folders=lambda roots, markers: None,
        )
        (self.root / "Mine").mkdir()
        await executor._set_folders({"libraries": [str(self.root / "Films")], "staging": "auto"})
        self.assertEqual(executor.roots["staging"], self.root / "Mine")
        locked = self.root / "share"
        (locked / "Films").mkdir(parents=True)
        locked.chmod(0o555)
        if os.access(locked, os.W_OK):
            self.skipTest("running as a user who can write anywhere")
        self.assertEqual(
            await self.folders(locked / "Films"), (self.root / "data" / "Incoming").resolve()
        )


if __name__ == "__main__":
    unittest.main()
