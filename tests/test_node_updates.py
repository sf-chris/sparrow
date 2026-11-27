"""Sparrow Node updating itself: fetch, check, swap, restart, and go back when it fails."""

import asyncio
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from backend.agents import computers, node_package
from backend.agents.accounts import Accounts
from backend.agents.node_update import Updater

LATEST = {"version": "new1", "commit": "b006548", "date": "2026-11-27T10:00:00+13:00"}


def install(folder: Path, package: bytes):
    folder.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as archive:
        archive.extractall(folder)


class FakeResponse:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("no", request=None, response=self)


class FakeClient:
    def __init__(self, content, status=200):
        self.content, self.status, self.calls = content, status, 0

    async def get(self, path, timeout=None):
        assert path == "/api/v1/node/package.tar.gz"
        self.calls += 1
        return FakeResponse(self.content, self.status)


class UpdaterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.package = node_package.package("tar.gz")
        self.latest = node_package.version()
        install(self.home / "app", self.package)
        (self.home / "app" / "OLD").write_text("the running version")
        self.config = self.home / "node.json"
        self.config.write_text("{}")
        self.restarts = []

    def tearDown(self):
        self.temp.cleanup()

    def updater(self, own="old", client=None):
        updater = Updater(
            self.config,
            client or FakeClient(self.package),
            own_version=own,
            app=self.home / "app",
            restart=lambda: self.restarts.append(True),
        )
        return updater

    async def finish(self, updater):
        for _ in range(200):
            if updater._task and updater._task.done():
                return
            await asyncio.sleep(0.05)
        raise AssertionError("the update never finished")

    async def test_an_allowed_update_swaps_keeps_the_one_before_and_restarts(self):
        updater = self.updater()
        self.assertTrue(updater.supported)
        updater.consider({"version": self.latest, "auto": False, "requested": 0})
        self.assertIsNone(updater._task)  # Not allowed: Update by itself is off.
        updater.consider({"version": self.latest, "auto": True, "requested": 0})
        await self.finish(updater)
        self.assertEqual(self.restarts, [True])
        self.assertFalse((self.home / "app" / "OLD").exists())
        self.assertTrue((self.home / "app.previous" / "OLD").exists())
        state = updater.state()
        self.assertEqual((state["state"], state["to"], state["from"]), ("restarting", self.latest, "old"))

    async def test_the_new_version_is_kept_once_it_reaches_the_server(self):
        updater = self.updater(own=self.latest)
        updater._save(state="restarting", to=self.latest, began=time.time(), **{"from": "old"})
        await updater.started()
        self.assertEqual(updater.state()["state"], "probation")
        self.assertEqual(updater.capabilities()["update"]["state"], "idle")
        updater.reached_server()
        self.assertEqual(updater.state()["state"], "current")
        self.assertEqual(self.restarts, [])

    async def test_a_new_version_that_never_reaches_the_server_goes_back(self):
        (self.home / "app.previous").mkdir()
        (self.home / "app.previous" / "BEFORE").write_text("the one before")
        updater = self.updater(own=self.latest)
        updater._save(state="probation", to=self.latest, began=time.time() - 3600, **{"from": "old"})
        await updater.started()
        self.assertEqual(self.restarts, [True])
        self.assertTrue((self.home / "app" / "BEFORE").exists())
        self.assertTrue((self.home / "app.failed").is_dir())
        state = updater.capabilities()["update"]
        self.assertEqual((state["state"], state["target"]), ("failed", self.latest))
        self.assertIn("one before is back", state["error"])

    async def test_a_failed_download_says_why_and_waits_to_be_asked_again(self):
        client = FakeClient(b"", status=500)
        updater = self.updater(client=client)
        updater.consider({"version": "new1", "auto": True, "requested": 0})
        await self.finish(updater)
        report = updater.capabilities()["update"]
        self.assertEqual((report["state"], report["target"]), ("failed", "new1"))
        self.assertIn("500", report["error"])
        self.assertTrue((self.home / "app" / "OLD").exists())  # Still the running version.
        updater._task = None
        updater.consider({"version": "new1", "auto": True, "requested": 0})
        self.assertIsNone(updater._task)
        updater.consider({"version": "new1", "auto": True, "requested": time.time() + 1})
        self.assertIsNotNone(updater._task)
        await self.finish(updater)
        self.assertEqual(client.calls, 2)

    async def test_a_package_that_does_not_start_is_never_switched_to(self):
        broken = io.BytesIO()
        with tarfile.open(fileobj=broken, mode="w:gz") as archive:
            for name, data in (("VERSION", b"broken1"), ("sparrow-node.py", b"raise SystemExit(3)\n")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        updater = self.updater(client=FakeClient(broken.getvalue()))
        updater.consider({"version": "broken1", "auto": True, "requested": 0})
        await self.finish(updater)
        self.assertEqual(self.restarts, [])
        self.assertTrue((self.home / "app" / "OLD").exists())
        self.assertFalse((self.home / "app.next").exists())
        self.assertEqual(updater.capabilities()["update"]["error"], "The new version didn't start.")

    def test_the_launcher_goes_back_when_a_new_version_cannot_even_import(self):
        install(self.home / "app.previous", self.package)
        (self.home / "app" / "backend" / "agents" / "node_client.py").write_text("this is not python(\n")
        (self.home / "update.json").write_text(json.dumps({"state": "restarting", "to": "bad1"}))
        env = {k: v for k, v in os.environ.items() if not k.startswith("SPARROW_NODE_")}
        result = subprocess.run(
            [sys.executable, str(self.home / "app" / "sparrow-node.py"), "--config", str(self.config), "version"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        # The version before is back and answered in its place.
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1])["version"], self.latest, result.stderr)
        self.assertTrue((self.home / "app.failed").is_dir())
        self.assertEqual(json.loads((self.home / "update.json").read_text())["state"], "failed")

    def test_only_a_node_installed_by_the_setup_command_updates_itself(self):
        self.assertFalse(Updater(self.config, own_version="old", app=self.home / "elsewhere").supported)
        self.assertFalse(Updater(self.config, own_version="", app=self.home / "app").supported)


class UpdateStatusTests(unittest.TestCase):
    def node(self, **caps):
        return {"id": "anti", "online": True, "capabilities": {"self_update": True, **caps}}

    def test_states_follow_what_the_computer_reports(self):
        auto = {"auto_update": True, "update_requested": 0}
        manual = {"auto_update": False, "update_requested": 0}
        release = {"commit": "7cda240", "date": "2026-11-20T10:00:00+13:00"}
        state = lambda caps, wanted=auto: computers.update_status(self.node(**caps), LATEST, wanted)["state"]
        self.assertEqual(state({"version": "new1", "release": release}), "current")
        self.assertEqual(state({"version": "old1"}, manual), "available")
        self.assertEqual(state({"version": "old1"}), "starting")
        self.assertEqual(state({"version": "old1", "update": {"state": "downloading"}}), "updating")
        failed = {"version": "old1", "update": {"state": "failed", "target": "new1", "at": time.time(), "error": "No room."}}
        self.assertEqual(state(failed), "failed")
        self.assertEqual(state(failed, {"auto_update": True, "update_requested": time.time() + 5}), "starting")
        self.assertEqual(state({}), "unsupported")  # Set up before updates existed.
        self.assertEqual(state({"version": "old1", "self_update": False}), "unsupported")
        report = computers.update_status(self.node(version="old1", release=release), LATEST, manual)
        self.assertEqual(report["running"], {"version": "old1", **release})
        local = computers.update_status({"id": "local", "capabilities": {}}, LATEST)
        self.assertEqual((local["state"], local["auto"]), ("current", None))

    def test_a_stuck_update_counts_as_needing_attention(self):
        report = computers.update_status(self.node(), LATEST)
        health = computers.health({"id": "anti", "online": True, "capabilities": {}}, computers.default_jobs("anti"), report)
        self.assertEqual(health["update"]["state"], "unsupported")
        self.assertGreaterEqual(health["attention"], 1)

    def test_settings_and_offers(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = Accounts(temp)
            self.assertEqual(
                computers.update_offer(accounts, "anti", LATEST),
                {"version": "new1", "auto": True, "requested": 0.0},
            )
            computers.set_settings(accounts, "anti", auto_update=False)
            computers.set_settings(accounts, "anti", update_requested=123.0)
            self.assertEqual(
                computers.update_offer(accounts, "anti", LATEST),
                {"version": "new1", "auto": False, "requested": 123.0},
            )


class UpdateApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import secrets
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.agents.node_executor import Executor
        from backend.agents.nodes import install_nodes
        from backend.models import SparrowConfig
        from backend.storage import Storage

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        storage = Storage(str(root / "server"))
        await storage.load_all()
        await storage.save_config(SparrowConfig())
        app = FastAPI()

        @app.middleware("http")
        async def administrator(request, call_next):
            request.state.user = {"id": "owner", "role": "admin", "disabled": False}
            return await call_next(request)

        self.nodes = install_nodes(app, storage, lambda: None)
        self.client = TestClient(app)
        self.executor = Executor(root / "node", {})
        code = self.nodes.enroll("anti")["code"]
        self.credential = secrets.token_urlsafe(32)
        self.node_id = self.nodes.pair(code, self.credential, self.executor.capabilities())["node_id"]

    async def asyncTearDown(self):
        self.temp.cleanup()

    def test_update_now_reaches_the_computer_and_it_can_fetch_the_package(self):
        auth = {"Authorization": "Bearer " + self.credential}
        self.assertEqual(
            self.client.put(f"/api/v1/admin/nodes/{self.node_id}/settings", json={"auto_update": False}).status_code,
            200,
        )
        listed = self.client.post(f"/api/v1/admin/nodes/{self.node_id}/update").json()
        self.assertIn(listed["health"]["update"]["state"], ("current", "starting", "unsupported"))
        polled = self.client.post(
            "/api/v1/node/poll", json={"capabilities": {**self.executor.capabilities(), "version": "old1"}}, headers=auth
        ).json()
        self.assertEqual(polled["update"]["version"], node_package.version())
        self.assertFalse(polled["update"]["auto"])
        self.assertGreater(polled["update"]["requested"], 0)
        package = self.client.get("/api/v1/node/package.tar.gz", headers=auth)
        self.assertEqual(package.status_code, 200)
        self.assertEqual(package.headers["x-sparrow-node-version"], node_package.version())
        with tarfile.open(fileobj=io.BytesIO(package.content)) as archive:
            names = archive.getnames()
            release = json.loads(archive.extractfile("RELEASE").read())
        self.assertIn("sparrow-node.py", names)
        self.assertEqual(release["version"], node_package.version())
        self.assertEqual(self.client.get("/api/v1/node/package.tar.gz").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/admin/nodes/local/update").status_code, 404)


if __name__ == "__main__":
    unittest.main()
