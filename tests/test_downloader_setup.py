import asyncio
import json
import os
import socket
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from backend.agents.account_api import COOKIE, install_accounts
from backend.agents.downloader_setup import install_downloader_setup
from backend.models import TorrentClientConfig, TorrentClientType
from backend.services import managed_transmission as mt
from backend.services import torrent_client as tc
from backend.storage import Storage

SPA = '<!doctype html><html><head><title>Sparrow</title></head><body><div id="root"></div></body></html>'

# A stand-in transmission-daemon: reads Sparrow's settings.json and answers RPC
# with Transmission's session handshake and Basic authentication.
FAKE_DAEMON = r'''
import base64, json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
config = sys.argv[sys.argv.index("--config-dir") + 1]
settings = json.load(open(config + "/settings.json"))
token = base64.b64encode(f"{settings['rpc-username']}:{settings['rpc-password']}".encode()).decode()
open(config + "/argv.json", "w").write(json.dumps(sys.argv[1:]))
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("Authorization") != "Basic " + token:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Transmission"')
            self.end_headers()
            return
        if self.headers.get("X-Transmission-Session-Id") != "fixture":
            self.send_response(409)
            self.send_header("X-Transmission-Session-Id", "fixture")
            self.end_headers()
            return
        body = json.dumps({"result": "success", "arguments": {"version": "3.00 (fixture)", "torrents": []}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
HTTPServer((settings["rpc-bind-address"], settings["rpc-port"]), Handler).serve_forever()
'''


def fake_daemon(directory) -> str:
    path = Path(directory) / "transmission-daemon"
    path.write_text(f"#!{sys.executable}\n{FAKE_DAEMON}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def responder(routes):
    """MockTransport from {(method, port, path): response factory}."""

    def handle(request):
        factory = routes.get((request.method, request.url.port, request.url.path))
        if factory is None:
            factory = routes.get(("*", request.url.port, "*"))
        if factory is None:
            raise httpx.ConnectError("refused", request=request)
        return factory(request)

    return httpx.MockTransport(handle)


class FingerprintTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tc._default_login_failed.clear()

    async def test_qbittorrent_default_login_bypass_and_saved_password(self):
        logins = []

        def login(request):
            logins.append(request)
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; path=/"})

        def version(request):
            if "SID=abc" in request.headers.get("cookie", ""):
                return httpx.Response(200, text="v4.6.2")
            return httpx.Response(403, text="Forbidden")

        async with httpx.AsyncClient(transport=responder({
            ("GET", 8080, "/api/v2/app/version"): version,
            ("POST", 8080, "/api/v2/auth/login"): login,
        })) as client:
            found = await tc.probe_qbittorrent(client, "127.0.0.1", 8080)
        self.assertEqual(
            found,
            {"type": "qbittorrent", "host": "127.0.0.1", "port": 8080, "version": "v4.6.2", "needs_login": False, "problem": ""},
        )

        async with httpx.AsyncClient(transport=responder({
            ("GET", 8080, "/api/v2/app/version"): lambda r: httpx.Response(200, text="v5.0.1"),
        })) as client:
            found = await tc.probe_qbittorrent(client, "127.0.0.1", 8080)
        self.assertEqual((found["version"], found["needs_login"]), ("v5.0.1", False))

    async def test_qbittorrent_with_its_own_password_is_tried_once(self):
        logins = []

        def login(request):
            logins.append(request)
            return httpx.Response(200, text="Fails.")

        transport = responder({
            ("GET", 8080, "/api/v2/app/version"): lambda r: httpx.Response(403, text="Forbidden"),
            ("POST", 8080, "/api/v2/auth/login"): login,
            ("GET", 8080, "/"): lambda r: httpx.Response(200, text="<title>qBittorrent Web UI</title>"),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            first = await tc.probe_qbittorrent(client, "127.0.0.1", 8080)
            second = await tc.probe_qbittorrent(client, "127.0.0.1", 8080)
        self.assertTrue(first["needs_login"])
        self.assertEqual(first, second)
        self.assertEqual(len(logins), 1)

    async def test_transmission_open_password_and_refusals(self):
        def rpc(request):
            if request.headers.get("X-Transmission-Session-Id") == "s1":
                return httpx.Response(200, json={"arguments": {"version": "4.0.5"}, "result": "success"})
            return httpx.Response(409, headers={"X-Transmission-Session-Id": "s1"})

        cases = {
            9091: rpc,
            9092: lambda r: httpx.Response(401, headers={"WWW-Authenticate": 'Basic realm="Transmission"', "Server": "Transmission"}),
            9093: lambda r: httpx.Response(421, headers={"Server": "Transmission"}),
            9094: lambda r: httpx.Response(403, headers={"Server": "Transmission"}),
        }
        transport = responder({("POST", port, "/transmission/rpc"): handler for port, handler in cases.items()})
        async with httpx.AsyncClient(transport=transport) as client:
            open_, locked, misnamed, refused = [
                await tc.probe_transmission(client, "127.0.0.1", port) for port in cases
            ]
        self.assertEqual((open_["version"], open_["needs_login"], open_["problem"]), ("4.0.5", False, ""))
        self.assertTrue(locked["needs_login"])
        self.assertIn("IP address", misnamed["problem"])
        self.assertIn("refusing", refused["problem"])

    async def test_other_web_services_are_never_reported(self):
        # Sparrow's own app answers every path with its page (the owner's 8081).
        transport = responder({
            ("*", 8081, "*"): lambda r: httpx.Response(200, text=SPA, headers={"content-type": "text/html"}),
            ("*", 9091, "*"): lambda r: httpx.Response(404, json={"detail": "Not Found"}),
            ("*", 8080, "*"): lambda r: httpx.Response(401, text="Unauthorized", headers={"WWW-Authenticate": 'Basic realm="Router"'}),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            for port in (8081, 9091, 8080):
                self.assertIsNone(await tc.probe_qbittorrent(client, "127.0.0.1", port))
                self.assertIsNone(await tc.probe_transmission(client, "127.0.0.1", port))

    async def test_discovery_searches_one_brand_wider_and_dedupes(self):
        def rpc(request):
            return httpx.Response(401, headers={"WWW-Authenticate": 'Basic realm="Transmission"'})

        transport = responder({
            ("POST", 9091, "/transmission/rpc"): rpc,
            ("POST", 9093, "/transmission/rpc"): rpc,
            ("GET", 8080, "/api/v2/app/version"): lambda r: httpx.Response(200, text="v4.6.2"),
        })
        hosts = [
            ("127.0.0.1", "127.0.0.1", (TorrentClientType.TRANSMISSION, TorrentClientType.QBITTORRENT)),
            ("transmission", "127.0.0.1", (TorrentClientType.TRANSMISSION,)),
        ]
        async with httpx.AsyncClient(transport=transport) as client:
            quick = await tc.discover_download_apps(hosts=hosts, container=True, client=client)
            wide = await tc.discover_download_apps(
                TorrentClientType.TRANSMISSION, hosts=hosts, container=True, client=client
            )
        self.assertEqual(
            sorted((app["type"], app["host"], app["port"]) for app in quick),
            [("qbittorrent", "127.0.0.1", 8080), ("transmission", "127.0.0.1", 9091)],
        )
        self.assertEqual(
            sorted((app["type"], app["port"]) for app in wide),
            [("transmission", 9091), ("transmission", 9093)],
        )

    async def test_misnamed_transmission_is_offered_by_its_ip(self):
        def rpc(request):
            if request.url.host == "transmission":
                return httpx.Response(421, headers={"Server": "Transmission"})
            return httpx.Response(409, headers={"X-Transmission-Session-Id": "s"})

        transport = responder({("POST", 9091, "/transmission/rpc"): rpc})
        async with httpx.AsyncClient(transport=transport) as client:
            found = await tc.discover_download_apps(
                hosts=[("transmission", "172.20.0.5", (TorrentClientType.TRANSMISSION,))],
                container=True,
                client=client,
            )
        self.assertEqual([(a["host"], a["problem"]) for a in found], [("172.20.0.5", "")])

    def test_default_gateway_is_read_from_the_route_table(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write(
                "Iface\tDestination\tGateway \tFlags\n"
                "eth0\t0012A8C0\t00000000\t0001\n"
                "eth0\t00000000\t010012AC\t0003\n"
            )
        try:
            self.assertEqual(tc.default_gateway(handle.name), "172.18.0.1")
        finally:
            os.unlink(handle.name)
        self.assertIsNone(tc.default_gateway("/nonexistent/route"))

    async def test_legacy_discovery_keeps_only_ready_apps(self):
        found = [
            {"type": "transmission", "host": "127.0.0.1", "port": 9091, "version": "3.00", "needs_login": False, "problem": ""},
            {"type": "qbittorrent", "host": "127.0.0.1", "port": 8080, "version": "", "needs_login": True, "problem": ""},
        ]
        with patch.object(tc, "discover_download_apps", AsyncMock(return_value=found)):
            clients = await tc.discover_torrent_clients()
        self.assertEqual([(c.type, c.port, c.reachable) for c in clients], [(TorrentClientType.TRANSMISSION, 9091, True)])


class SettingsTests(unittest.TestCase):
    def test_settings_merge_keeps_other_keys_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transmission" / "settings.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"speed-limit-down": 500, "rpc-port": 1}))
            daemon = mt.ManagedTransmission(tmp)
            required = daemon.required_settings(
                {"download_dir": "/media/Incoming", "username": "sparrow", "password": "pw", "rpc_port": 9100, "peer_port": 51500}
            )
            mt.write_settings(path, required)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["speed-limit-down"], 500)
            self.assertEqual(saved["rpc-port"], 9100)
            self.assertEqual(saved["rpc-bind-address"], "127.0.0.1")
            self.assertTrue(saved["rpc-authentication-required"])
            self.assertFalse(saved["ratio-limit-enabled"])
            self.assertEqual(saved["umask"], 18)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_ports_skip_ones_in_use(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            taken = busy.getsockname()[1]
            self.assertFalse(mt.port_free(taken))
            chosen = mt.choose_port(taken, range(taken, taken + 20))
            self.assertNotEqual(chosen, taken)
            self.assertTrue(mt.port_free(chosen))

    def test_install_hint_only_when_missing(self):
        with patch.object(mt, "find_executable", return_value="/usr/bin/transmission-daemon"):
            self.assertEqual(mt.install_hint(), "")
        with patch.object(mt, "find_executable", return_value=None), patch.object(mt, "in_container", return_value=True):
            self.assertEqual(mt.install_hint(), "docker compose build")

    def test_old_saved_connections_are_not_managed(self):
        old = {"type": "transmission", "host": "nas", "port": 9091, "username": "", "password": "", "url": ""}
        self.assertFalse(TorrentClientConfig.from_dict(old).managed)
        self.assertTrue(TorrentClientConfig.from_dict({**old, "managed": True}).to_dict()["managed"])


class ManagedDaemonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.incoming = Path(self.temp.name) / "incoming"
        self.incoming.mkdir()
        self.daemon = mt.ManagedTransmission(Path(self.temp.name) / "data", fake_daemon(self.temp.name))

    async def asyncTearDown(self):
        await self.daemon.stop()
        self.temp.cleanup()

    async def test_starts_answers_restarts_after_a_crash_and_stops(self):
        port = await self.daemon.start(download_dir=str(self.incoming), username="sparrow", password="secret")
        self.assertTrue(await self.daemon.wait_ready(10))
        argv = json.loads((self.daemon.config_dir / "argv.json").read_text())
        self.assertIn("--foreground", argv)
        # Same credentials: no restart.
        pid = self.daemon._proc.pid
        self.assertEqual(await self.daemon.start(download_dir=str(self.incoming), username="sparrow", password="secret"), port)
        self.assertEqual(self.daemon._proc.pid, pid)

        self.daemon._proc.kill()
        for _ in range(100):
            await asyncio.sleep(0.1)
            if self.daemon.running and self.daemon._proc.pid != pid:
                break
        self.assertNotEqual(self.daemon._proc.pid, pid)
        self.assertTrue(await self.daemon.wait_ready(10))
        self.assertEqual(self.daemon.rpc_port, port)

        await self.daemon.stop()
        self.assertFalse(self.daemon.running)
        await asyncio.sleep(1.2)
        self.assertFalse(self.daemon.running, "a deliberate stop is not restarted")


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "data")
        await self.storage.load_all()
        self.app = FastAPI()
        self.accounts = install_accounts(self.app, self.storage, lambda: None)
        owner = self.accounts.create_user("owner", "fixture-password", "Owner", bootstrap=True)
        self.accounts.set_preferences(owner["id"], {})
        install_downloader_setup(self.app, self.storage)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
            headers={"X-Sparrow-Request": "1"},
            cookies={COOKIE: self.accounts.new_session(owner["id"])},
        )
        self.fake = fake_daemon(self.temp.name)

    async def asyncTearDown(self):
        await self.client.aclose()
        await mt.shutdown()
        self.temp.cleanup()

    async def save_client(self, **values):
        config = self.storage.get_config()
        config.torrent_client = TorrentClientConfig(**values)
        await self.storage.save_config(config)

    async def test_status_is_admin_only_and_never_returns_the_password(self):
        await self.save_client(type=TorrentClientType.QBITTORRENT, host="nas", port=8080, username="me", password="private-password")
        with patch("backend.agents.downloader_setup.TorrentManager.get_info", AsyncMock(return_value=tc.TorrentClientInfo(TorrentClientType.QBITTORRENT, "nas", 8080, True, "v4.6.2"))):
            response = await self.client.get("/api/v1/admin/downloader")
        self.assertNotIn("private-password", response.text)
        data = response.json()
        self.assertEqual(
            set(data),
            {"type", "host", "port", "username", "managed", "reachable", "version", "managed_available", "install_hint", "in_container"},
        )
        self.assertEqual((data["type"], data["reachable"], data["managed"]), ("qbittorrent", True, False))
        self.client.cookies.clear()
        self.assertEqual((await self.client.get("/api/v1/admin/downloader")).status_code, 401)
        self.assertEqual((await self.client.post("/api/v1/admin/downloader/managed")).status_code, 401)

    async def test_connect_saves_a_working_app_and_keeps_its_saved_password(self):
        await self.save_client(type=TorrentClientType.QBITTORRENT, host="nas", port=8080, username="me", password="kept-password")
        config = self.storage.get_config()
        config.max_active_transfers = 5
        await self.storage.save_config(config)
        tried = []

        async def connect(manager):
            tried.append(manager.config.password)
            return True

        with patch("backend.agents.downloader_setup.TorrentManager.connect", connect), patch(
            "backend.agents.downloader_setup.TorrentManager.get_info",
            AsyncMock(return_value=tc.TorrentClientInfo(TorrentClientType.QBITTORRENT, "nas", 8080, True)),
        ):
            same = await self.client.post(
                "/api/v1/admin/downloader/connect",
                json={"type": "qbittorrent", "host": "http://nas:8080/", "port": 8080, "username": "me", "password": ""},
            )
            moved = await self.client.post(
                "/api/v1/admin/downloader/connect",
                json={"type": "qbittorrent", "host": "nas2", "port": 8080, "username": "me", "password": ""},
            )
        self.assertEqual(same.status_code, 200, same.text)
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(tried, ["kept-password", ""])
        self.assertNotIn("kept-password", same.text)
        saved = self.storage.get_config()
        self.assertEqual((saved.torrent_client.host, saved.torrent_client.managed), ("nas2", False))
        self.assertEqual(saved.max_active_transfers, 5)

    async def test_connect_failures_say_what_answered(self):
        found = {"type": "transmission", "host": "nas", "port": 9091, "version": "", "needs_login": True, "problem": ""}
        cases = [
            ((found, None), "transmission", "Transmission answered but didn’t accept that username and password."),
            ((None, {**found, "type": "qbittorrent"}), "transmission", "That’s qBittorrent, not Transmission."),
            ((None, None), "qbittorrent", "Nothing answered at nas:9091."),
            (({**found, "problem": "Transmission is refusing this server."}, None), "transmission", "Transmission is refusing this server."),
        ]
        for (transmission, qbittorrent), kind, message in cases:
            with patch("backend.agents.downloader_setup.TorrentManager.connect", AsyncMock(return_value=False)), patch(
                "backend.agents.downloader_setup.probe_transmission", AsyncMock(return_value=transmission)
            ), patch("backend.agents.downloader_setup.probe_qbittorrent", AsyncMock(return_value=qbittorrent)):
                response = await self.client.post(
                    "/api/v1/admin/downloader/connect",
                    json={"type": kind, "host": "nas", "port": 9091, "username": "u", "password": "p"},
                )
            self.assertEqual((response.status_code, response.json()["detail"]), (422, message))
        self.assertEqual(self.storage.get_config().torrent_client.type, TorrentClientType.NONE)

    async def test_managed_needs_an_incoming_folder_and_the_program(self):
        response = await self.client.post("/api/v1/admin/downloader/managed")
        self.assertEqual((response.status_code, response.json()["detail"]), (422, "Choose an incoming folder in Storage first."))
        incoming = Path(self.temp.name) / "incoming"
        incoming.mkdir()
        config = self.storage.get_config()
        config.staging_dir = str(incoming)
        await self.storage.save_config(config)
        with patch.object(mt, "find_executable", return_value=None), patch.object(mt, "in_container", return_value=False), patch.object(
            mt.sys, "platform", "darwin"
        ):
            response = await self.client.post("/api/v1/admin/downloader/managed")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Install Transmission first: brew install transmission-cli")

    async def test_managed_starts_transmission_and_saves_a_private_connection(self):
        incoming = Path(self.temp.name) / "incoming"
        incoming.mkdir()
        config = self.storage.get_config()
        config.staging_dir = str(incoming)
        await self.storage.save_config(config)
        with patch.object(mt, "find_executable", return_value=self.fake):
            response = await self.client.post("/api/v1/admin/downloader/managed")
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            saved = self.storage.get_config().torrent_client
            self.assertTrue(saved.managed and data["managed"] and data["reachable"])
            self.assertEqual((saved.host, saved.username, data["port"]), ("127.0.0.1", "sparrow", saved.port))
            self.assertGreater(len(saved.password), 20)
            self.assertNotIn(saved.password, response.text)
            # Again: same password and port, no new secret.
            again = await self.client.post("/api/v1/admin/downloader/managed")
            self.assertEqual(self.storage.get_config().torrent_client.password, saved.password)
            self.assertEqual(again.json()["port"], saved.port)
            # Its own Transmission is not offered as something found.
            with patch("backend.agents.downloader_setup.discover_download_apps", AsyncMock(return_value=[
                {"type": "transmission", "host": "127.0.0.1", "port": saved.port, "version": "", "needs_login": True, "problem": ""},
            ])):
                found = (await self.client.post("/api/v1/admin/downloader/discover", json={})).json()
            self.assertEqual(found, {"found": []})
            # Recovery restarts it through the shared client helper.
            ok, message = await tc.start_configured_client(saved)
            self.assertTrue(ok, message)


if __name__ == "__main__":
    unittest.main()
