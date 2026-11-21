"""Choosing folders on any computer, and pairing one from a pasted command."""

import asyncio
import io
import json
import os
import secrets
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from backend.agents import folders
from backend.agents.folders import FolderError
from backend.agents.node_executor import Executor
from backend.agents.nodes import Nodes
from backend.models import SparrowConfig
from backend.storage import Storage


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.place = Path(self.temp.name).resolve() / "media"
        (self.place / "Films").mkdir(parents=True)
        (self.place / "Series").mkdir()
        (self.place / ".hidden").mkdir()
        (self.place / "notes.txt").write_text("not a folder")
        os.symlink(self.place / "Films", self.place / "Shortcut")
        self.points = folders.starting_points([str(self.place)])

    def tearDown(self):
        self.temp.cleanup()

    def test_places_and_listings_stay_inside_this_computers_places(self):
        self.assertEqual(self.points, [self.place])
        places = folders.browse("", self.points)
        self.assertEqual([p["path"] for p in places["folders"]], [str(self.place)])
        self.assertIn("free_bytes", places["folders"][0])
        listing = folders.browse(str(self.place), self.points)
        # Files, hidden folders and links aren't places to keep media.
        self.assertEqual([r["name"] for r in listing["folders"]], ["Films", "Series"])
        self.assertTrue(listing["writable"])
        self.assertEqual(listing["parent"], "")
        inner = folders.browse(str(self.place / "Films"), self.points)
        self.assertEqual(inner["parent"], str(self.place))
        self.assertEqual([c["name"] for c in inner["crumbs"]], [str(self.place), "Films"])
        for outside in ("/", str(self.place.parent), str(self.place / ".." / "x"), "relative"):
            with self.assertRaises(FolderError):
                folders.browse(outside, self.points)

    def test_sparrows_own_state_is_never_a_place_it_finds(self):
        from unittest import mock

        state = self.place / "state"
        state.mkdir()
        with mock.patch.object(folders, "in_container", return_value=False), \
                mock.patch.object(folders, "_linux_disks", return_value=[state]), \
                mock.patch("pathlib.Path.home", return_value=self.place):
            found = folders.starting_points(None, [state])
        self.assertIn(self.place, found)
        self.assertNotIn(state, found)
        # Places someone sets are used as given.
        self.assertEqual(folders.starting_points([str(state)], [state]), [state])

    def test_new_folder_names_are_checked_before_anything_is_made(self):
        for name, words in [
            ("", "Name the folder"),
            ("a/b", "can’t contain"),
            (".cache", "start with a dot"),
            ("Films.", "end with a space or a dot"),
            ("CON", "reserved on Windows"),
            ("Films", "already a folder called Films"),
        ]:
            with self.assertRaisesRegex(FolderError, words):
                folders.make_folder(str(self.place), name, self.points)
        with self.assertRaises(FolderError):
            folders.make_folder(str(self.place.parent), "Escape", self.points)
        made = folders.make_folder(str(self.place), " Incoming ", self.points)
        self.assertEqual(made, str(self.place / "Incoming"))
        self.assertTrue(Path(made).is_dir())

    def test_overlapping_folders_are_explained(self):
        cases = [
            (["/m/Library"], "/m/Library", "can’t be the same folder"),
            (["/m/Library"], "/m/Library/Incoming", "Incoming can’t be inside Library: downloads"),
            (["/m/Incoming/Library"], "/m/Incoming", "Library can’t be inside Incoming"),
            (["/m/Library", "/m/Library/Disk"], "/m/Incoming", "Library 2 can’t be inside Library."),
        ]
        for libraries, staging, words in cases:
            self.assertIn(words, folders.overlap(folders.labelled(libraries, staging)))
        self.assertEqual(folders.overlap(folders.labelled(["/m/Library"], "/m/Incoming")), "")


class NodeFolderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.place = self.root / "disk"
        self.place.mkdir()
        self.saved = []
        self.executor = Executor(
            self.root / "node", {}, places=[str(self.place)],
            on_folders=lambda roots, markers: self.saved.append((roots, markers)),
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def run_command(self, kind, args):
        return await self.executor.execute(
            {"id": secrets.token_hex(8), "kind": kind, "args": args, "expires": time.time() + 30}
        )

    async def test_a_new_computer_chooses_and_makes_its_folders_from_sparrow(self):
        capabilities = self.executor.capabilities()
        self.assertTrue(capabilities["folders"])
        self.assertEqual(capabilities["roots"], [])
        self.assertTrue(capabilities["hostname"])
        places = await self.run_command("browse", {})
        self.assertEqual(places["value"]["folders"][0]["path"], str(self.place))
        made = await self.run_command("make_folder", {"path": str(self.place), "name": "Films"})
        self.assertEqual(made["value"]["path"], str(self.place / "Films"))
        refused = await self.run_command(
            "set_folders", {"libraries": [str(self.place / "Films")], "staging": str(self.place / "Films/In")}
        )
        self.assertIn("Incoming can’t be inside Library", refused["error"])
        outside = await self.run_command(
            "set_folders", {"libraries": [str(self.root / "elsewhere")], "staging": str(self.place / "In")}
        )
        self.assertIn("isn’t in a place", outside["error"])
        result = await self.run_command(
            "set_folders", {"libraries": [str(self.place / "Films")], "staging": str(self.place / "Incoming")}
        )
        self.assertTrue(result["ok"], result)
        roots = {r["id"]: r for r in result["value"]["roots"]}
        self.assertEqual(roots["library"]["label"], str(self.place / "Films"))
        self.assertEqual(roots["staging"]["label"], str(self.place / "Incoming"))
        self.assertTrue((self.place / "Incoming/.sparrow-root-id").is_file())
        self.assertEqual(self.saved[-1][0]["staging"], str(self.place / "Incoming"))
        # The new folders are usable at once.
        self.assertEqual(self.executor.path("staging"), self.place / "Incoming")

    async def test_changing_the_main_folder_keeps_other_disks_in_order(self):
        for name in ("A", "B", "C", "In"):
            (self.place / name).mkdir()
        await self.run_command("set_folders", {
            "libraries": [str(self.place / "A"), str(self.place / "B")],
            "staging": str(self.place / "In"), "keep_extra": False,
        })
        await self.run_command("set_folders", {"libraries": [str(self.place / "C")], "staging": str(self.place / "In")})
        roots = self.saved[-1][0]
        self.assertEqual(roots["library"], str(self.place / "C"))
        self.assertEqual(roots["library-2"], str(self.place / "B"))

    async def test_the_server_itself_changes_folders_in_its_settings(self):
        local = Executor(self.root / "local", {}, places=[str(self.place)])
        self.assertFalse(local.capabilities()["folders"])
        result = await local.execute({
            "id": "x", "kind": "set_folders", "expires": time.time() + 30,
            "args": {"libraries": [str(self.place / "A")], "staging": str(self.place / "B")},
        })
        self.assertIn("settings", result["error"])


class NewComputerDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_its_own_transmission_waits_for_an_incoming_folder(self):
        from backend.agents.node_client import managed_downloader

        with tempfile.TemporaryDirectory() as temp:
            config = {"roots": {}, "downloader": {"type": "transmission", "managed": True,
                      "username": "sparrow", "password": "x", "port": 0}}
            # A newly paired computer has no folders yet: nothing starts, nothing fails.
            self.assertIsNone(await managed_downloader(Path(temp) / "node.json", config))


class PairingCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.agents.nodes import install_nodes

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.storage = Storage(str(self.root / "server"))
        await self.storage.load_all()
        await self.storage.save_config(SparrowConfig())
        app = FastAPI()

        @app.middleware("http")
        async def administrator(request, call_next):
            request.state.user = {"id": "owner", "role": "admin"}
            return await call_next(request)

        self.nodes = install_nodes(app, self.storage, lambda: None)
        self.client = TestClient(app, base_url="http://192.168.1.103:8081")

    async def asyncTearDown(self):
        self.temp.cleanup()

    def enroll(self):
        response = self.client.post("/api/v1/admin/nodes/enroll", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_one_command_reports_progress_pairs_and_takes_the_computers_name(self):
        enrollment = self.enroll()
        code, node_id = enrollment["code"], enrollment["node_id"]
        status = self.client.get(f"/api/v1/admin/nodes/pairing/{node_id}").json()
        self.assertEqual((status["stage"], status["paired"], status["scripted"]), (None, False, False))
        script = self.client.get(f"/api/v1/node/setup/{code}.sh")
        self.assertEqual(script.status_code, 200)
        # The script reaches the server at the address the computer used.
        self.assertIn("SERVER='http://192.168.1.103:8081'", script.text)
        self.assertIn(f"CODE='{code}'", script.text)
        self.assertNotIn("__SPARROW_", script.text)
        windows = self.client.get(f"/api/v1/node/setup/{code}.ps1").text
        self.assertIn(f"$Code = '{code}'", windows)
        status = self.client.get(f"/api/v1/admin/nodes/pairing/{node_id}").json()
        self.assertEqual(status["stage"], "fetched")
        # A setup that started has time to download Python before its code expires.
        self.assertGreater(status["expires_in_seconds"], 800)
        self.assertEqual(
            self.client.post(f"/api/v1/node/setup/{code}/progress", json={"stage": "python"}).status_code, 200
        )
        self.assertEqual(
            self.client.post(f"/api/v1/node/setup/{code}/progress", json={"stage": "rm -rf /"}).status_code, 422
        )
        self.assertEqual(self.client.get(f"/api/v1/admin/nodes/pairing/{node_id}").json()["stage"], "python")

        executor = Executor(self.root / "node", {}, places=[str(self.root)], on_folders=lambda *a: None)
        capabilities = {**executor.capabilities(), "hostname": "antivac"}
        self.nodes.pair(code, secrets.token_urlsafe(32), capabilities)
        status = self.client.get(f"/api/v1/admin/nodes/pairing/{node_id}").json()
        self.assertTrue(status["paired"] and status["scripted"])
        self.assertEqual(status["node"]["name"], "antivac")
        # A used code can't set up another computer.
        self.assertIn("expired or was already used", self.client.get(f"/api/v1/node/setup/{code}.sh").text)

    async def test_codes_never_start_with_a_dash(self):
        from unittest import mock

        with mock.patch("backend.agents.nodes.secrets.token_urlsafe", side_effect=["-abc" * 8, "Abc" * 11]):
            self.assertEqual(self.nodes.enroll()["code"], "Abc" * 11)

    async def test_expired_or_unknown_commands_say_so_plainly(self):
        unknown = secrets.token_urlsafe(24)
        for kind in ("sh", "ps1"):
            text = self.client.get(f"/api/v1/node/setup/{unknown}.{kind}").text
            self.assertIn("Pair a computer for a fresh one", text)
        self.assertIn("exit 1", self.client.get(f"/api/v1/node/setup/{unknown}.sh").text)
        self.assertEqual(self.client.get(f"/api/v1/node/setup/{unknown}/package.tar.gz").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/node/setup/x;rm.sh").status_code, 404)
        enrollment = self.enroll()
        with self.nodes.accounts.connect() as db:
            db.execute("UPDATE node_enrollment SET expires=?", (time.time() - 1,))
        self.assertIn("expired", self.client.get(f"/api/v1/node/setup/{enrollment['code']}.sh").text)

    async def test_the_package_runs_with_only_the_standard_library_and_its_own_copies(self):
        code = self.enroll()["code"]
        archive = self.client.get(f"/api/v1/node/setup/{code}/package.tar.gz")
        self.assertEqual(archive.status_code, 200)
        target = self.root / "installed"
        with tarfile.open(fileobj=io.BytesIO(archive.content)) as package:
            names = package.getnames()
            package.extractall(target, filter="data")
        for needed in ("sparrow-node.py", "backend/agents/node_client.py", "vendor/httpx/__init__.py"):
            self.assertIn(needed, names)
        self.assertFalse([n for n in names if "__pycache__" in n])
        with zipfile.ZipFile(io.BytesIO(self.client.get(f"/api/v1/node/setup/{code}/package.zip").content)) as z:
            self.assertIn("sparrow-node.py", z.namelist())
        config = self.root / "node.json"
        config.write_text(json.dumps({"roots": {}, "markers": {}}))
        # -I -S: no site-packages, so only what the package carries is importable.
        checked = subprocess.run(
            [sys.executable, "-I", "-S", str(target / "sparrow-node.py"), "--config", str(config), "check"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["protocol"], 1)
        probe = subprocess.run(
            [sys.executable, "-I", "-S", "-c",
             "import sys; sys.path[:0]=[sys.argv[1], sys.argv[1]+'/vendor'];"
             "import backend.agents.scout, backend.services.torrent_client, backend.agents.folders",
             str(target)],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)

    async def test_choosing_a_paired_computers_folders_from_sparrow(self):
        enrollment = self.enroll()
        place = self.root / "antivac"
        place.mkdir()
        saved = {}
        executor = Executor(
            self.root / "node", {}, places=[str(place)],
            on_folders=lambda roots, markers: saved.update(roots),
        )
        credential = secrets.token_urlsafe(32)
        node_id = self.nodes.pair(enrollment["code"], credential, executor.capabilities())["node_id"]

        async def node():
            # What Sparrow Node's poll loop does.
            for _ in range(200):
                command = self.nodes.next_command(node_id)
                if command:
                    self.nodes.finish(node_id, command["id"], await executor.execute(command))
                await asyncio.sleep(0.01)

        worker = asyncio.create_task(node())
        try:
            listing = await asyncio.to_thread(
                self.client.get, "/api/v1/admin/storage/browse", params={"node_id": node_id}
            )
            self.assertEqual(listing.status_code, 200, listing.text)
            self.assertEqual(listing.json()["folders"][0]["path"], str(place))
            made = await asyncio.to_thread(
                self.client.post, "/api/v1/admin/storage/browse/folder",
                json={"node_id": node_id, "path": str(place), "name": "Films"},
            )
            self.assertEqual(made.json()["path"], str(place / "Films"))
            chosen = await asyncio.to_thread(
                self.client.post, f"/api/v1/admin/nodes/{node_id}/folders",
                json={"libraries": [str(place / "Films")], "staging": str(place / "Incoming")},
            )
            self.assertEqual(chosen.status_code, 200, chosen.text)
            labels = {r["id"]: r.get("label") for r in chosen.json()["capabilities"]["roots"]}
            self.assertEqual(labels["library"], str(place / "Films"))
            self.assertEqual(saved["staging"], str(place / "Incoming"))
            local = await asyncio.to_thread(
                self.client.post, "/api/v1/admin/nodes/local/folders",
                json={"libraries": ["/x"], "staging": "/y"},
            )
            self.assertEqual(local.status_code, 422)
        finally:
            worker.cancel()

    async def test_old_or_offline_computers_say_what_to_do(self):
        enrollment = self.enroll()
        old = Executor(self.root / "old", {})  # Paired before folders could be chosen here.
        node_id = self.nodes.pair(enrollment["code"], secrets.token_urlsafe(32), old.capabilities())["node_id"]
        response = self.client.get("/api/v1/admin/storage/browse", params={"node_id": node_id})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Update Sparrow Node", response.json()["detail"])
        with self.nodes.accounts.connect() as db:
            db.execute("UPDATE nodes SET last_seen=0")
        self.assertIn("offline", self.client.get(
            "/api/v1/admin/storage/browse", params={"node_id": node_id}).json()["detail"])

    async def test_this_server_browses_its_own_folders_without_its_state(self):
        listing = self.client.get("/api/v1/admin/storage/browse", params={"node_id": "local"})
        self.assertEqual(listing.status_code, 200, listing.text)
        paths = [Path(p["path"]) for p in listing.json()["folders"]]
        state = Path(self.storage.data_dir).resolve()
        self.assertNotIn(state, paths)


if __name__ == "__main__":
    unittest.main()
