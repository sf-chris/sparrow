import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.agents.accounts import Accounts
from backend.agents.models import Job, JobStatus
from backend.agents.node_executor import Executor
from backend.agents.placement import GB, Placement, PlacementError
from backend.agents.service import AgentService
from backend.configuration import apply_config_update
from backend.models import MediaType, LibraryItem, SparrowConfig, TorrentClientConfig
from backend.storage import Storage


def computer(node_id, name, *disks, online=True, download=True):
    return {
        "id": node_id,
        "name": name,
        "online": online,
        "disabled": False,
        "capabilities": {
            "download": download,
            "roots": [
                {
                    "id": root_id,
                    "available": True,
                    "writable": True,
                    "free_bytes": int(free * GB),
                    "total_bytes": 4000 * GB,
                    "label": f"/{name}/{root_id}",
                }
                for root_id, free in disks
            ]
            + [{"id": "staging", "available": True, "free_bytes": 1}],
        },
    }


class FakeNodes:
    def __init__(self, *computers):
        self.computers = list(computers)

    def list(self):
        return self.computers

    def info(self, node_id):
        return next((c for c in self.computers if c["id"] == node_id), None)


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.accounts = Accounts(self.temp.name)
        self.owner = self.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )
        self.library = []
        self.jobs = []
        self.nodes = FakeNodes(
            computer("local", "medivac", ("library", 500)),
            computer("anti", "antivac", ("library", 900), ("library-2", 2000)),
        )
        storage = SimpleNamespace(get_library=lambda: self.library)
        service = SimpleNamespace(store=SimpleNamespace(get_jobs=lambda: self.jobs))
        self.placement = Placement(self.accounts, self.nodes, storage, lambda: service)

    def tearDown(self):
        self.temp.cleanup()

    def choose(self, destination="auto", tmdb_id=7, media_type="tv", count=2, **kw):
        return self.placement.choose(
            self.owner,
            destination,
            tmdb_id=tmdb_id,
            media_type=media_type,
            count=count,
            values={"preferred_quality": "1080p"},
            **kw,
        )

    def owns(self, tmdb_id, node_id, root_id, media_type="tv"):
        item = LibraryItem(
            id=f"item-{tmdb_id}-{node_id}",
            title="Fixture",
            tmdb_id=tmdb_id,
            media_type=MediaType(media_type),
            path="",
        )
        self.library.append(item)
        with self.accounts.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, item_id TEXT NOT NULL, node_id TEXT NOT NULL, root_id TEXT NOT NULL, path TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(node_id,root_id,path))"
            )
            db.execute(
                "INSERT INTO assets VALUES (?, ?, ?, ?, ?, '{}')",
                (f"a-{time.time_ns()}", item.id, node_id, root_id, "Show/S01E01.mkv"),
            )

    def test_automatic_picks_the_disk_with_most_free_space(self):
        choice = self.choose()
        self.assertEqual((choice["node_id"], choice["root_id"]), ("anti", "library-2"))
        self.assertIn("most free space", choice["reason"])
        self.assertIn("antivac", choice["reason"])

    def test_a_show_stays_where_it_already_lives(self):
        self.owns(7, "local", "library")
        choice = self.choose()
        self.assertEqual(choice["node_id"], "local")
        self.assertEqual(choice["reason"], "Kept with the rest of the show on medivac.")
        film = self.choose(tmdb_id=8, media_type="movie", count=1)
        self.assertEqual(film["root_id"], "library-2")

    def test_an_active_request_counts_as_home_before_any_file_lands(self):
        self.jobs.append(
            Job(tmdb_id=7, media_type="tv", node_id="anti", library_id="anti", root_id="")
        )
        choice = self.choose()
        self.assertEqual((choice["node_id"], choice["root_id"]), ("anti", "library"))

    def test_a_sleeping_home_is_waited_for_not_split(self):
        self.owns(7, "local", "library")
        self.nodes.computers[0]["online"] = False
        choice = self.choose()
        self.assertEqual(choice["node_id"], "local")
        self.assertIn("starts when it's back", choice["reason"])

    def test_a_full_home_moves_on_and_says_so(self):
        self.owns(7, "local", "library")
        self.nodes.computers[0]["capabilities"]["roots"][0]["free_bytes"] = 2 * GB
        choice = self.choose()
        self.assertEqual(choice["node_id"], "anti")
        self.assertIn("— medivac is nearly full (2 GB free)", choice["reason"])

    def test_fill_in_order_skips_full_and_offline_disks(self):
        self.placement.set_rule("fill")
        self.nodes.computers[0]["capabilities"]["roots"][0]["free_bytes"] = 1 * GB
        choice = self.choose()
        self.assertEqual((choice["node_id"], choice["root_id"]), ("anti", "library"))
        self.assertIn("first in order with room", choice["reason"])
        self.assertIn("nearly full", choice["reason"])

    def test_take_turns_rotates_only_when_committed(self):
        self.placement.set_rule("round_robin")
        preview = self.choose(commit=False)
        self.assertEqual(self.choose(commit=False)["root_id"], preview["root_id"])
        turns = [
            (c["node_id"], c["root_id"])
            for c in (self.choose(tmdb_id=n) for n in range(10, 14))
        ]
        self.assertEqual(
            turns,
            [("local", "library"), ("anti", "library"), ("anti", "library-2"), ("local", "library")],
        )
        self.assertIn("next in turn", self.choose(tmdb_id=20)["reason"])

    def test_storage_without_a_download_app_is_skipped_for_new_downloads(self):
        self.nodes.computers[1]["capabilities"]["download"] = False
        self.assertEqual(self.choose()["node_id"], "local")
        self.assertEqual(self.choose(acquire=False)["node_id"], "anti")
        self.assertIn("no download app yet", self.choose("anti/library-2")["reason"])
        self.nodes.computers[0]["capabilities"]["download"] = False
        nowhere = self.choose()
        self.assertEqual(nowhere["root_id"], "library-2")
        self.assertIn("no storage here has a download app yet", nowhere["reason"])

    def test_a_person_only_gets_storage_they_may_use(self):
        limited = self.accounts.create_user(
            "limited",
            "fixture-password-456",
            "Limited",
            invitation=self.accounts.invite("requester", ["local"]),
        )
        choice = self.placement.choose(
            limited, "auto", tmdb_id=7, media_type="tv", values={}
        )
        self.assertEqual(choice["node_id"], "local")
        with self.assertRaisesRegex(PlacementError, "available to your account"):
            self.placement.choose(limited, "anti", tmdb_id=7, media_type="tv", values={})
        options = self.placement.destinations(limited)
        self.assertEqual([c["value"] for c in options["computers"]], ["local"])

    def test_one_chosen_place_waits_when_asleep_and_refuses_when_full(self):
        self.nodes.computers[1]["online"] = False
        choice = self.choose("anti/library-2", source="personal")
        self.assertEqual(choice["root_id"], "library-2")
        self.assertIn("your storage", choice["reason"])
        self.assertIn("offline", choice["reason"])
        self.nodes.computers[1]["online"] = True
        self.nodes.computers[1]["capabilities"]["roots"][1]["free_bytes"] = GB
        with self.assertRaisesRegex(PlacementError, "nearly full"):
            self.choose("anti/library-2")
        self.assertEqual(self.choose("local")["reason"], "On medivac, as chosen.")

    def test_a_computer_with_several_disks_keeps_its_show_together(self):
        self.owns(7, "anti", "library")
        choice = self.choose("anti")
        self.assertEqual(choice["root_id"], "library")
        self.assertEqual(self.choose("anti", tmdb_id=9)["root_id"], "library-2")

    def test_ask_and_removed_choices_need_a_new_choice(self):
        with self.assertRaisesRegex(PlacementError, "Choose where"):
            self.choose("ask")
        with self.assertRaisesRegex(PlacementError, "no longer exists"):
            self.choose("pool:gone")
        with self.assertRaisesRegex(PlacementError, "no longer paired"):
            self.choose("missing")

    def test_pools_keep_their_order_and_rule(self):
        pool = self.placement.save_pool(
            None, "Shows", "fill", ["anti/library", "local/library"]
        )
        choice = self.choose(f"pool:{pool['id']}")
        self.assertEqual((choice["node_id"], choice["root_id"]), ("anti", "library"))
        with self.assertRaisesRegex(PlacementError, "disks Sparrow can see"):
            self.placement.save_pool(None, "Bad", "fill", ["nowhere/library"])
        options = self.placement.destinations(self.owner)
        self.assertEqual(options["pools"][0]["name"], "Shows")
        self.assertEqual(self.placement.describe(f"pool:{pool['id']}"), "Shows")
        self.assertEqual(self.placement.describe("anti/library-2"), "antivac · library-2")

    def test_storage_is_part_of_the_versioned_preference_contract(self):
        settings = self.accounts.server_settings()
        self.assertEqual(settings["defaults"]["storage"], "auto")
        self.accounts.set_preferences(self.owner["id"], {"storage": "anti/library-2"})
        resolved = self.accounts.resolve(self.owner["id"])
        self.assertEqual(resolved["values"]["storage"], "anti/library-2")
        self.assertEqual(resolved["sources"]["storage"], "personal")
        request = self.accounts.resolve(self.owner["id"], {"storage": "local"})
        self.assertEqual(request["sources"]["storage"], "request")
        with self.assertRaises(ValueError):
            self.accounts.set_preferences(self.owner["id"], {"storage": "../etc"})


class PlacementApiTests(unittest.TestCase):
    setUp, tearDown = PlacementTests.setUp, PlacementTests.tearDown

    def test_pools_rule_and_preview_take_json_bodies(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.agents.placement import install_placement

        app = FastAPI()

        @app.middleware("http")
        async def signed_in(request, call_next):
            request.state.user = self.owner
            return await call_next(request)

        storage = SimpleNamespace(get_library=lambda: self.library)
        install_placement(app, self.accounts, self.nodes, storage, lambda: None)
        client = TestClient(app)
        pool = client.post(
            "/api/v1/admin/storage/pools",
            json={"name": "Shows", "rule": "fill", "members": ["anti/library"]},
        )
        self.assertEqual(pool.status_code, 200, pool.text)
        rule = client.put("/api/v1/admin/storage/rule", json={"rule": "round_robin"})
        self.assertEqual(rule.json(), {"rule": "round_robin"})
        preview = client.post(
            "/api/v1/storage/preview", json={"tmdb_id": 7, "media_type": "tv", "count": 2}
        ).json()
        self.assertEqual(preview["label"], "Automatic")
        self.assertIn("next in turn", preview["reason"])
        places = client.get("/api/v1/storage/destinations").json()
        self.assertEqual(places["pools"][0]["value"], f"pool:{pool.json()['id']}")


class PlacedRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.storage = Storage(self.temp.name)
        await self.storage.load_all()
        await self.storage.save_config(
            SparrowConfig(
                library_dir=str(root / "media"),
                extra_library_dirs=[str(root / "media2")],
                staging_dir=str(root / "incoming"),
                torrent_client=TorrentClientConfig.from_dict({"type": "qbittorrent"}),
            )
        )
        for name in ("media", "media2", "incoming"):
            (root / name).mkdir()
        self.service = AgentService(self.storage, self.temp.name, AsyncMock())
        self.service.emit = AsyncMock()
        self.owner = self.service.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )

        async def tmdb(path, **kwargs):
            return (
                {"episodes": [{"episode_number": 1}, {"episode_number": 2}]}
                if "/season/" in path
                else {"id": 42, "name": "Fixture show", "original_language": "en"}
            )

        self.service.toolbox.tmdb_get = AsyncMock(side_effect=tmdb)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_a_request_is_placed_once_and_says_where_and_why(self):
        job = await self.service.create_job(
            42, {"1": [1]}, user_id=self.owner["id"], library_id=None
        )
        self.assertEqual(job.library_id, "local")
        self.assertIn(job.root_id, ("", "library-2"))
        self.assertTrue(job.placement.startswith("On This server"))
        journal = self.service.store.get_journal(job.id)
        self.assertEqual([e.text for e in journal], [job.placement])
        again = await self.service.create_job(
            42, {"1": [2]}, user_id=self.owner["id"], library_id=None
        )
        self.assertEqual(again.id, job.id)
        self.assertEqual(again.root_id, job.root_id)

    async def test_follow_up_work_on_a_computer_uses_the_shows_disk(self):
        first = await self.service.create_job(
            42, {"1": [1]}, user_id=self.owner["id"], library_id=None,
            preference_overrides={"storage": "local/library-2"},
        )
        self.assertEqual(first.root_id, "library-2")
        first.status = JobStatus.COMPLETE
        self.service.store.save_job(first)
        self.assertEqual(self.service.placement.root_on("local", 42, "tv"), "library")
        first.status = JobStatus.ACTIVE
        self.service.store.save_job(first)
        self.assertEqual(self.service.placement.root_on("local", 42, "tv"), "library-2")


class SeveralDisksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_library_folders_must_be_separate(self):
        config = SparrowConfig()
        base = self.root
        config = apply_config_update(
            config,
            {
                "library_dir": str(base / "a"),
                "staging_dir": str(base / "in"),
                "extra_library_dirs": [str(base / "b")],
            },
        )
        self.assertEqual(config.extra_library_dirs, [str(base / "b")])
        for extra in (str(base / "a" / "inner"), str(base / "in")):
            with self.assertRaises(ValueError):
                apply_config_update(
                    SparrowConfig.from_dict(config.to_dict()),
                    {"extra_library_dirs": [extra]},
                )
        with self.assertRaisesRegex(ValueError, "main library"):
            apply_config_update(SparrowConfig(), {"extra_library_dirs": [str(base / "b")]})

    def test_a_node_reports_each_disk_and_publishes_only_into_a_library(self):
        for name in ("one", "two", "in"):
            (self.root / name).mkdir()
        executor = Executor(
            self.root / "node",
            {"library": str(self.root / "one"), "library-2": str(self.root / "two"), "staging": str(self.root / "in")},
        )
        roots = {r["id"]: r for r in executor.capabilities()["roots"]}
        self.assertEqual(set(roots), {"library", "library-2", "staging"})
        self.assertEqual(roots["library-2"]["label"], str(self.root / "two"))
        self.assertNotIn("label", roots["staging"])
        import asyncio
        from backend.agents.media_state import file_version

        source = self.root / "in" / "dl-1" / "a.mkv"
        source.parent.mkdir()
        source.write_bytes(b"x" * 10)
        result = asyncio.run(
            executor.execute(
                {
                    "id": "pub-into-staging",
                    "kind": "publish",
                    "args": {
                        "root_id": "staging",
                        "path": "dl-1/a.mkv",
                        "version": file_version(source),
                        "destination": "a.mkv",
                        "destination_root": "staging",
                    },
                    "expires": time.time() + 30,
                }
            )
        )
        self.assertEqual(result["error"], "Publish into a library folder.")


if __name__ == "__main__":
    unittest.main()


class NodeSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_linux_node_pairs_several_disks_and_its_own_downloader(self):
        import json
        from argparse import Namespace
        from unittest.mock import patch

        import httpx

        from backend.agents import node_client

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seen = {}

            def pair(request):
                seen.update(json.loads(request.content))
                return httpx.Response(200, json={"node_id": "anti", "protocol": 1})

            real = httpx.AsyncClient
            with patch.object(
                node_client.httpx,
                "AsyncClient",
                lambda **kw: real(transport=httpx.MockTransport(pair), **kw),
            ):
                await node_client.configure(
                    Namespace(
                        server="http://192.168.1.103:8081",
                        code="c" * 24,
                        library=[str(root / "one"), str(root / "two")],
                        staging=str(root / "in"),
                        config=str(root / "state" / "node.json"),
                        node_id=None,
                        downloader_config=None,
                        managed_downloader=True,
                        allow_private_http=True,
                    )
                )
            config = json.loads((root / "state" / "node.json").read_text())
            self.assertEqual(set(config["roots"]), {"library", "library-2", "staging"})
            self.assertEqual(set(config["markers"]), {"library", "library-2", "staging"})
            self.assertTrue(config["downloader"]["managed"])
            reported = {r["id"] for r in seen["capabilities"]["roots"]}
            self.assertEqual(reported, {"library", "library-2", "staging"})
            self.assertTrue(seen["capabilities"]["download"])
            self.assertEqual(config["node_id"], "anti")
