"""Computers and their jobs: what the household wants, what works, in plain words."""

import secrets
import tempfile
import unittest
from pathlib import Path

from backend.agents import computers
from backend.agents.accounts import Accounts
from backend.agents.node_executor import Executor
from backend.models import SparrowConfig
from backend.storage import Storage

GB = 1024**3


def node(node_id="anti", online=True, **caps):
    roots = caps.pop(
        "roots",
        [
            {"id": "library", "available": True, "writable": True, "free_bytes": 63 * GB, "total_bytes": 471 * GB},
            {"id": "staging", "available": True, "writable": True, "free_bytes": 63 * GB, "total_bytes": 471 * GB},
        ],
    )
    return {
        "id": node_id,
        "name": "antivac",
        "online": online,
        "disabled": False,
        "last_seen": 0,
        "capabilities": {"roots": roots, "probe": True, "transcode": True, **caps},
    }


def rows(report):
    return {r["job"]: (r["state"], r["line"], r["fix"]) for r in report["rows"]}


class HealthTests(unittest.TestCase):
    def test_a_computer_without_a_download_app_says_so_and_offers_the_fix(self):
        report = computers.health(node(download=False), computers.default_jobs("anti"))
        self.assertEqual(rows(report)["downloads"], ("attention", "No download app", "repair"))
        self.assertEqual(rows(report)["storage"][0], "ready")
        self.assertEqual(rows(report)["storage"][1], "68 GB free of 506 GB")
        self.assertEqual(rows(report)["subtitles"], ("off", "Off", None))
        self.assertEqual(report["attention"], 1)
        # On this server the fix is its own download app settings.
        local = computers.health(node("local", download=False), computers.default_jobs("local"))
        self.assertEqual(rows(local)["downloads"][2], "download_app")

    def test_a_storage_only_computer_is_all_good(self):
        jobs = {"storage": True, "downloads": False, "subtitles": False}
        report = computers.health(node(download=False), jobs)
        self.assertEqual(report["attention"], 0)
        self.assertEqual(rows(report)["downloads"], ("off", "Off", None))
        self.assertEqual(rows(report)["playback"][0], "ready")

    def test_offline_computers_wait_and_problems_name_their_fix(self):
        report = computers.health(node(online=False, download=True), computers.default_jobs("anti"))
        self.assertEqual(report["state"], "offline")
        self.assertTrue(all(r["state"] in ("waiting", "off") for r in report["rows"]))
        missing = computers.health(
            node(download=True, roots=[{"id": "library", "available": False}]),
            computers.default_jobs("anti"),
        )
        self.assertEqual(rows(missing)["storage"], ("attention", "Library folder not found", "folders"))
        self.assertEqual(rows(missing)["downloads"], ("attention", "Choose an incoming folder", "folders"))
        tools = computers.health(node(download=True, transcode=False), computers.default_jobs("anti"))
        self.assertEqual(rows(tools)["playback"][0], "attention")

    def test_subtitles_name_the_graphics_card_and_show_setting_up(self):
        jobs = {"storage": False, "downloads": False, "subtitles": True}
        gpu = {"ready": True, "device": "cuda", "gpu": "GeForce RTX 4080"}
        report = computers.health(node(roots=[], speech=gpu), jobs)
        self.assertEqual(rows(report)["subtitles"], ("ready", "Uses the GeForce RTX 4080", None))
        self.assertNotIn("playback", rows(report))  # Nothing kept, nothing to play.
        busy = computers.health(node(roots=[], speech={"installing": True, "progress": 40}), jobs)
        self.assertEqual(rows(busy)["subtitles"], ("waiting", "Setting up. 40%", None))
        # A graphics card turns Subtitles on by default.
        self.assertTrue(computers.default_jobs("pc", {"speech": {"gpu": "RTX"}})["subtitles"])
        self.assertFalse(computers.default_jobs("pc", {})["subtitles"])

    def test_wanted_jobs_are_remembered_per_computer(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = Accounts(temp)
            self.assertEqual(
                computers.wanted(accounts, "anti"),
                {"storage": True, "downloads": True, "subtitles": False},
            )
            computers.set_wanted(accounts, "anti", {"downloads": False})
            self.assertFalse(computers.wanted(accounts, "anti")["downloads"])
            self.assertTrue(computers.wanted(accounts, "local")["subtitles"])
            with self.assertRaises(ValueError):
                computers.set_wanted(accounts, "anti", {"cooking": True})


class JobsApiTests(unittest.IsolatedAsyncioTestCase):
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
            request.state.user = {"id": "owner", "role": "admin", "disabled": False}
            return await call_next(request)

        self.nodes = install_nodes(app, self.storage, lambda: None)
        self.client = TestClient(app, base_url="http://192.168.1.103:8081")
        for name in ("lib", "in"):
            (self.root / name).mkdir()
        self.executor = Executor(
            self.root / "node", {"library": str(self.root / "lib"), "staging": str(self.root / "in")}
        )
        code = self.nodes.enroll("nas")["code"]
        self.credential = secrets.token_urlsafe(32)
        self.node_id = self.nodes.pair(code, self.credential, self.executor.capabilities())["node_id"]

    async def asyncTearDown(self):
        self.temp.cleanup()

    def test_every_computer_lists_its_jobs_and_health(self):
        listed = {n["id"]: n for n in self.client.get("/api/v1/nodes").json()}
        self.assertTrue(listed["local"]["health"]["runs_sparrow"])
        nas = listed[self.node_id]["health"]
        self.assertEqual(nas["state"], "online")
        self.assertEqual(rows(nas)["downloads"][:2], ("attention", "No download app"))

    def test_turning_jobs_off_reaches_the_computer_on_its_next_check(self):
        response = self.client.put(
            f"/api/v1/admin/nodes/{self.node_id}/jobs", json={"downloads": False}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(rows(response.json()["health"])["downloads"][:2], ("off", "Off"))
        polled = self.client.post(
            "/api/v1/node/poll",
            json={"capabilities": self.executor.capabilities()},
            headers={"Authorization": "Bearer " + self.credential},
        ).json()
        self.assertEqual(polled["jobs"], {"storage": True, "downloads": False, "subtitles": False})
        self.assertEqual(
            self.client.put(f"/api/v1/admin/nodes/{self.node_id}/jobs", json={"cooking": True}).status_code,
            422,
        )
        self.assertEqual(
            self.client.put("/api/v1/admin/nodes/nobody/jobs", json={"storage": True}).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
