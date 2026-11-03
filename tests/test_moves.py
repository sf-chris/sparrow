import asyncio
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.agents.accounts import Accounts
from backend.agents.catalogue import Catalogue
from backend.agents.media_state import file_version
from backend.agents.models import Job
from backend.agents.moves import Moves
from backend.agents.node_executor import Executor, NodeError
from backend.agents.nodes import Nodes
from backend.agents.placement import Placement, PlacementError
from backend.models import LibraryItem, MediaType, SparrowConfig
from backend.storage import Storage


class MoveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("one", "two", "incoming", "remote", "remote-in"):
            (self.root / name).mkdir()
        self.storage = Storage(str(self.root / "server"))
        await self.storage.load_all()
        await self.storage.save_config(
            SparrowConfig(
                library_dir=str(self.root / "one"),
                extra_library_dirs=[str(self.root / "two")],
                staging_dir=str(self.root / "incoming"),
            )
        )
        self.accounts = Accounts(self.storage.data_dir)
        self.owner = self.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )
        self.jobs = []
        service = SimpleNamespace(store=SimpleNamespace(get_jobs=lambda: self.jobs))
        self.nodes = Nodes(self.storage, lambda: service)
        self.catalogue = Catalogue(self.storage, self.nodes)
        self.placement = Placement(self.accounts, self.nodes, self.storage, lambda: service)
        self.moves = Moves(
            self.accounts, self.nodes, self.storage, self.catalogue, self.placement, lambda: service
        )
        self.remote = Executor(
            self.root / "node",
            {"library": str(self.root / "remote"), "staging": str(self.root / "remote-in")},
            fetcher=lambda token, offset: self.nodes.transfer_bytes(token, self.node_id, offset),
        )
        code = self.nodes.enroll("antivac")["code"]
        self.node_id = self.nodes.pair(
            code, secrets.token_urlsafe(32), self.remote.capabilities()
        )["node_id"]
        self.paused = False

        async def worker():
            while True:
                command = None if self.paused else self.nodes.next_command(self.node_id)
                if command:
                    result = await self.remote.execute(command)
                    self.nodes.finish(self.node_id, command["id"], result)
                    self.remote.delivered(command["id"])
                else:
                    if not self.paused:
                        self.nodes.heartbeat(self.node_id, self.remote.capabilities())
                    await asyncio.sleep(0.005)

        self.worker = asyncio.create_task(worker())
        self.item = LibraryItem(
            id="item-show",
            title="Fixture show",
            tmdb_id=42,
            media_type=MediaType.TV,
            path="",
            metadata={"library_id": "local"},
        )
        self.assets = []
        for episode in (1, 2):
            relative = f"Fixture show/Season 01/S01E0{episode}.mkv"
            path = self.root / "one" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(secrets.token_bytes(3 * 1024 * 1024 + episode))
            version = file_version(path)
            asset = self.catalogue.save_asset(
                self.item.id,
                "local",
                "library",
                relative,
                {"version": version, "size_bytes": version["size_bytes"], "quality": "1080p"},
                season=1,
                episode=episode,
            )
            self.assets.append(asset)
            self.item.set_episode_file(
                1, episode, {"asset_id": asset, "verified": True, "path": str(path), "file_version": version}
            )
        (self.root / "one" / "Fixture show/Season 01/S01E01.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        await self.storage.add_library_item(self.item)
        with self.accounts.connect() as db:
            db.execute(
                "INSERT INTO watch_state VALUES (?, ?, 600, 1400, 0, ?, 's', 1)",
                (self.owner["id"], self.assets[0], time.time()),
            )

    async def asyncTearDown(self):
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        self.temp.cleanup()

    async def finish(self, move):
        for _ in range(5):
            if await self.moves.advance(move["id"]):
                break
        return self.moves.get(move["id"])

    async def test_a_move_between_disks_keeps_progress_and_removes_the_old_copy(self):
        move = self.moves.start(self.owner, self.item.id, "local/library-2")
        self.assertEqual(move["to"], "This server · two")
        done = await self.finish(move)
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["copied"], 3)
        moved = self.root / "two" / "Fixture show/Season 01/S01E01.mkv"
        self.assertTrue(moved.is_file())
        self.assertTrue((self.root / "two" / "Fixture show/Season 01/S01E01.en.srt").is_file())
        self.assertFalse((self.root / "one" / "Fixture show/Season 01/S01E01.mkv").exists())
        self.assertFalse((self.root / "one" / "Fixture show/Season 01/S01E01.en.srt").exists())
        asset = self.catalogue.asset(self.owner, self.assets[0])
        self.assertEqual((asset["root_id"], asset["state"]), ("library-2", "ready"))
        self.assertEqual(asset["watch"]["position"], 600)
        item = self.storage.get_library_item(self.item.id)
        self.assertEqual(item.episodes["1"]["1"]["path"], str(moved))

    async def test_a_move_to_another_computer_follows_the_title_there(self):
        with self.accounts.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,media_type TEXT NOT NULL,tmdb_id INTEGER NOT NULL,node_id TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(user_id,media_type,tmdb_id,node_id))"
            )
            db.execute(
                "INSERT INTO subscriptions VALUES ('sub', ?, 'tv', 42, 'local', '{}')",
                (self.owner["id"],),
            )
        done = await self.finish(self.moves.start(self.owner, self.item.id, self.node_id))
        self.assertEqual(done["state"], "done")
        arrived = self.root / "remote" / "Fixture show/Season 01/S01E02.mkv"
        self.assertTrue(arrived.is_file())
        self.assertFalse((self.root / "one" / "Fixture show/Season 01/S01E02.mkv").exists())
        asset = self.catalogue.asset(self.owner, self.assets[1])
        self.assertEqual((asset["node_id"], asset["state"]), (self.node_id, "ready"))
        item = self.storage.get_library_item(self.item.id)
        self.assertEqual(item.metadata["library_id"], self.node_id)
        self.assertEqual(item.episodes["1"]["2"]["node_id"], self.node_id)
        self.assertEqual(self.catalogue.item_for(self.node_id, "tv", 42).id, self.item.id)
        with self.accounts.connect() as db:
            self.assertEqual(
                db.execute("SELECT node_id FROM subscriptions WHERE id='sub'").fetchone()[0],
                self.node_id,
            )
        with self.accounts.connect() as db:
            self.assertFalse(db.execute("SELECT 1 FROM node_transfers").fetchone())

    async def test_a_sleeping_destination_waits_and_nothing_is_removed(self):
        move = self.moves.start(self.owner, self.item.id, self.node_id)
        self.paused = True
        with self.accounts.connect() as db:
            db.execute("UPDATE nodes SET last_seen=0")
        self.assertFalse(await self.moves.advance(move["id"]))
        waiting = self.moves.get(move["id"])
        self.assertEqual(waiting["state"], "copying")
        self.assertIn("Waiting for antivac", waiting["message"])
        self.assertTrue((self.root / "one" / "Fixture show/Season 01/S01E01.mkv").is_file())
        self.paused = False
        await asyncio.sleep(0.05)
        self.assertEqual((await self.finish(move))["state"], "done")

    async def test_moves_are_refused_while_a_request_works_or_with_nowhere_to_go(self):
        self.jobs.append(Job(tmdb_id=42, media_type="tv", library_id="local"))
        with self.assertRaisesRegex(PlacementError, "still working"):
            self.moves.start(self.owner, self.item.id, self.node_id)
        self.jobs.clear()
        with self.assertRaisesRegex(PlacementError, "already on"):
            self.moves.start(self.owner, self.item.id, "local/library")
        self.moves.start(self.owner, self.item.id, "local/library-2")
        with self.assertRaisesRegex(PlacementError, "already moving"):
            self.moves.start(self.owner, self.item.id, self.node_id)

    async def test_automatic_moves_somewhere_else(self):
        move = self.moves.start(self.owner, self.item.id, "auto")
        self.assertNotEqual(move["to"], "This server · one")

    async def test_a_changed_original_is_kept(self):
        path = self.root / "one" / "Fixture show/Season 01/S01E01.mkv"
        with self.assertRaisesRegex(NodeError, "changed after it was copied"):
            await self.nodes.execute(
                "local",
                "release",
                {"root_id": "library", "path": "Fixture show/Season 01/S01E01.mkv", "sha256": "0" * 64},
            )
        self.assertTrue(path.is_file())
        (self.root / "incoming" / "x.mkv").write_bytes(b"x")
        with self.assertRaisesRegex(NodeError, "library folder"):
            await self.nodes.execute(
                "local", "release", {"root_id": "staging", "path": "x.mkv", "sha256": "0" * 64}
            )
        self.assertTrue((self.root / "incoming" / "x.mkv").is_file())


if __name__ == "__main__":
    unittest.main()
