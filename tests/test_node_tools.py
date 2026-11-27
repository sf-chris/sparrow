"""Sparrow Node's own media tools and download app: served, found first, kept on repair."""

import asyncio
import io
import json
import os
import secrets
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from backend.agents import node_package
from backend.agents.node_executor import Executor, executable
from backend.models import SparrowConfig
from backend.services import managed_transmission
from backend.storage import Storage


def tool(folder, name, text="#!/bin/sh\necho ok\n"):
    path = Path(folder) / "bin" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def archive(manifest=None, names=("ffmpeg", "ffprobe", "transmission-daemon")):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in names:
            data = b"#!/bin/sh\necho ok\n"
            info = tarfile.TarInfo(f"./bin/{name}")
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
        data = json.dumps(manifest or {"platform": "linux-x86_64", "version": "abc"}).encode()
        info = tarfile.TarInfo("./manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FindingTools(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ("SPARROW_TOOLS_DIR", "SPARROW_FFPROBE", "SPARROW_FFMPEG"):
            os.environ.pop(name, None)

    def test_a_nodes_own_tools_come_before_the_computers(self):
        own = Path(self.temp.name) / "tools"
        probe = tool(own, "ffprobe")
        daemon = tool(own, "transmission-daemon")
        os.environ["SPARROW_TOOLS_DIR"] = str(own)
        self.assertEqual(executable("ffprobe"), str(probe))
        self.assertEqual(managed_transmission.find_executable(), str(daemon))

    def test_a_configured_tool_still_wins(self):
        own = Path(self.temp.name) / "tools"
        tool(own, "ffprobe")
        os.environ["SPARROW_TOOLS_DIR"] = str(own)
        os.environ["SPARROW_FFPROBE"] = "/opt/sparrow/bin/ffprobe"
        self.assertEqual(executable("ffprobe"), "/opt/sparrow/bin/ffprobe")

    def test_capabilities_say_which_version_and_tools_a_computer_runs(self):
        own = Path(self.temp.name) / "tools"
        tool(own, "ffprobe")
        (own / "manifest.json").write_text(json.dumps({"platform": "linux-aarch64", "version": "v1"}))
        os.environ["SPARROW_TOOLS_DIR"] = str(own)
        os.environ["SPARROW_NODE_VERSION"] = "1234"
        caps = Executor(Path(self.temp.name) / "node", {}).capabilities()
        self.assertEqual(caps["version"], "1234")
        self.assertEqual(caps["tools"]["platform"], "linux-aarch64")
        del os.environ["SPARROW_NODE_VERSION"]
        # The server reports its own package version.
        self.assertEqual(Executor(Path(self.temp.name) / "n2", {}).capabilities()["version"], node_package.version())


class ServingTools(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.agents.nodes import install_nodes

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.tools = self.root / "node-tools"
        self.tools.mkdir()
        (self.tools / "tools-linux-x86_64.tar.gz").write_bytes(archive())
        self.patch = mock.patch.object(node_package, "TOOLS", self.tools)
        self.patch.start()
        node_package.tools_manifest.cache_clear()
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
        self.patch.stop()
        node_package.tools_manifest.cache_clear()
        self.temp.cleanup()

    async def test_a_setup_command_gets_tools_for_its_kind_of_computer(self):
        code = self.client.post("/api/v1/admin/nodes/enroll", json={}).json()["code"]
        served = self.client.get(f"/api/v1/node/setup/{code}/tools-linux-x86_64.tar.gz")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.content, (self.tools / "tools-linux-x86_64.tar.gz").read_bytes())
        self.assertEqual(node_package.tools_manifest("linux-x86_64")["version"], "abc")
        # No bundle for that platform, or a name that isn't one: a plain 404.
        missing = self.client.get(f"/api/v1/node/setup/{code}/tools-linux-aarch64.tar.gz")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("no media tools", missing.json()["detail"])
        self.assertEqual(self.client.get(f"/api/v1/node/setup/{code}/tools-..%2F..%2Fetc.tar.gz").status_code, 404)
        self.assertIsNone(node_package.tools("../../etc/passwd"))
        # An unknown or expired code gets nothing.
        self.assertEqual(
            self.client.get(f"/api/v1/node/setup/{secrets.token_urlsafe(24)}/tools-linux-x86_64.tar.gz").status_code,
            404,
        )

    async def test_a_paired_computer_fetches_tools_with_its_own_credential(self):
        enrollment = self.client.post("/api/v1/admin/nodes/enroll", json={}).json()
        credential = secrets.token_urlsafe(32)
        caps = Executor(self.root / "node", {}).capabilities()
        self.nodes.pair(enrollment["code"], credential, caps)
        self.assertEqual(self.client.get("/api/v1/node/tools/linux-x86_64.tar.gz").status_code, 401)
        served = self.client.get(
            "/api/v1/node/tools/linux-x86_64.tar.gz", headers={"Authorization": "Bearer " + credential}
        )
        self.assertEqual(served.status_code, 200)

    async def test_the_package_carries_its_version(self):
        code = self.client.post("/api/v1/admin/nodes/enroll", json={}).json()["code"]
        package = self.client.get(f"/api/v1/node/setup/{code}/package.tar.gz")
        with tarfile.open(fileobj=io.BytesIO(package.content)) as tar:
            stamp = tar.extractfile("VERSION").read().decode()
            launcher = tar.extractfile("sparrow-node.py").read().decode()
        self.assertEqual(stamp, node_package.version())
        self.assertIn("SPARROW_TOOLS_DIR", launcher)


class SettingUpAgain(unittest.IsolatedAsyncioTestCase):
    async def test_running_the_command_again_keeps_jobs_folders_and_the_download_app(self):
        from backend.agents import node_client

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "node.json"
            library, staging = Path(temp) / "Library", Path(temp) / "Incoming"
            library.mkdir()
            staging.mkdir()
            previous = {
                "server": "http://192.168.1.103:8081",
                "credential": "old" * 11,
                "enrollment_fingerprint": "x",
                "roots": {"library": str(library), "staging": str(staging)},
                "markers": {"library": "a", "staging": "b"},
                "downloader": {"type": "transmission", "managed": True, "host": "127.0.0.1",
                               "port": 9091, "username": "sparrow", "password": "kept"},
                "jobs": {"storage": True, "downloads": False, "subtitles": False},
                "node_id": "n1",
            }
            path.write_text(json.dumps(previous))

            class Answer:
                status_code = 200
                headers = {"content-type": "application/json"}

                @staticmethod
                def json():
                    return {"node_id": "n1"}

                @staticmethod
                def raise_for_status():
                    return None

            with mock.patch("httpx.AsyncClient.post", mock.AsyncMock(return_value=Answer())):
                await node_client.configure(SimpleNamespace(
                    config=str(path), server="http://192.168.1.103:8081", code="c" * 32, library=None, staging=None,
                    folder_root=None, node_id=None, downloader_config=None, managed_downloader=True,
                    allow_private_http=True,
                ))
            saved = json.loads(path.read_text())
            self.assertEqual(saved["roots"], previous["roots"])
            self.assertEqual(saved["jobs"], previous["jobs"])
            self.assertEqual(saved["downloader"]["password"], "kept")
            self.assertEqual(saved["node_id"], "n1")

    async def test_a_computer_without_tools_fetches_them_and_gets_a_download_app(self):
        from backend.agents import node_client

        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPARROW_TOOLS_DIR", None)
            path = Path(temp) / "node.json"
            config = {"downloader": None, "roots": {}}
            path.write_text(json.dumps(config))
            client = SimpleNamespace(get=mock.AsyncMock(return_value=SimpleNamespace(status_code=200, content=archive())))
            with mock.patch.object(node_client, "platform_name", return_value="linux-x86_64"):
                self.assertTrue(await node_client.ensure_tools(path, config, client))
            self.assertTrue(os.access(Path(temp) / "tools" / "bin" / "ffprobe", os.X_OK))
            self.assertEqual(os.environ["SPARROW_TOOLS_DIR"], str(Path(temp) / "tools"))
            self.assertTrue(json.loads(path.read_text())["downloader"]["managed"])
            # Next start: already there, nothing fetched.
            client.get.reset_mock()
            self.assertTrue(await node_client.ensure_tools(path, config, client))
            client.get.assert_not_called()

    async def test_no_bundle_for_this_computer_is_not_an_error(self):
        from backend.agents import node_client

        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPARROW_TOOLS_DIR", None)
            client = SimpleNamespace(get=mock.AsyncMock(return_value=SimpleNamespace(status_code=404, content=b"")))
            with mock.patch.object(node_client, "platform_name", return_value="linux-x86_64"):
                self.assertFalse(await node_client.ensure_tools(Path(temp) / "node.json", {}, client))
            self.assertFalse((Path(temp) / "tools").exists())


class Installer(unittest.TestCase):
    def test_the_installer_never_asks_for_sudo_or_apt(self):
        script = (Path(node_package.SCRIPTS) / "install.sh").read_text()
        body = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("apt-get", body)
        self.assertNotIn("sudo ", body.replace("without sudo", ""))
        self.assertIn("tools-$platform.tar.gz", body)
        self.assertIn("--managed-downloader", body)


if __name__ == "__main__":
    unittest.main()
