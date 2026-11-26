"""Sparrow Connect from this server's side, end to end: switch on, register,
prove the name to a (fake) certificate authority through Connect's DNS, serve
HTTPS with the issued certificate, pass Connect's call-back, switch off."""

import asyncio
import base64
import datetime
import hashlib
import importlib.util
import json
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.x509.oid import NameOID
from fastapi import FastAPI

from backend.agents import connect as home_side
from backend.agents.account_api import COOKIE, install_accounts
from backend.storage import Storage

spec = importlib.util.spec_from_file_location(
    "connect_service_for_home", Path(__file__).resolve().parents[1] / "connect" / "app.py"
)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)

ACME = "https://acme.test"
PUBLIC = "9.9.9.9"


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class FakeDns:
    def __init__(self):
        self.records = {}

    def set(self, name, kind, values, ttl=60):
        self.records[(name, kind)] = values
        return "change"

    def remove(self, name, kind):
        self.records.pop((name, kind), None)

    def wait(self, change, seconds=90):
        return True


class FakeAuthority:
    """Just enough ACME: checks each request's signature, nonce and URL, and
    the DNS proof in Connect's records, then signs the CSR with a test CA."""

    def __init__(self, dns):
        self.dns = dns
        self.nonces = set()
        self.account = None
        self.name = None
        self.authorized = "pending"
        self.certificate = None
        self.ca_key = ec.generate_private_key(ec.SECP256R1())
        self.ca = self.issue(self.ca_key.public_key(), "Test authority", self.ca_key, ca=True)

    def issue(self, public_key, name, signer, ca=False):
        now = datetime.datetime.now(datetime.timezone.utc)
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test authority")]))
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=90))
        )
        if ca:
            builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        else:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False
            )
        return builder.sign(signer, hashes.SHA256())

    def nonce(self):
        self.issued = getattr(self, "issued", 0) + 1
        value = f"nonce-{self.issued}"
        self.nonces.add(value)
        return value

    def verify(self, request):
        body = json.loads(request.content)
        protected = json.loads(unb64(body["protected"]))
        assert protected["url"] == str(request.url), "signed for another URL"
        assert protected["nonce"] in self.nonces, "reused or unknown nonce"
        self.nonces.discard(protected["nonce"])
        if "jwk" in protected:
            jwk = protected["jwk"]
            key = ec.EllipticCurvePublicNumbers(
                int.from_bytes(unb64(jwk["x"]), "big"), int.from_bytes(unb64(jwk["y"]), "big"), ec.SECP256R1()
            ).public_key()
            self.account = (key, jwk)
        else:
            assert protected["kid"] == f"{ACME}/acct/1"
            key = self.account[0]
        raw = unb64(body["signature"])
        key.verify(
            encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
            f'{body["protected"]}.{body["payload"]}'.encode(),
            ec.ECDSA(hashes.SHA256()),
        )
        return json.loads(unb64(body["payload"])) if body["payload"] else None

    def handle(self, request):
        path = request.url.path
        headers = {"Replay-Nonce": self.nonce()}
        if path == "/directory":
            return httpx.Response(200, json={k: f"{ACME}/{k}" for k in ("newNonce", "newAccount", "newOrder")})
        if path == "/newNonce":
            return httpx.Response(200, headers=headers)
        payload = self.verify(request)
        if path == "/newAccount":
            assert payload == {"termsOfServiceAgreed": True}
            return httpx.Response(201, headers={**headers, "Location": f"{ACME}/acct/1"}, json={})
        if path == "/newOrder":
            self.name = payload["identifiers"][0]["value"]
            return httpx.Response(
                201,
                headers={**headers, "Location": f"{ACME}/order/1"},
                json={"status": "pending", "authorizations": [f"{ACME}/authz/1"], "finalize": f"{ACME}/finalize/1"},
            )
        if path == "/authz/1":
            return httpx.Response(200, headers=headers, json={
                "status": self.authorized,
                "challenges": [{"type": "dns-01", "url": f"{ACME}/challenge/1", "token": "token-1"}],
            })
        if path == "/challenge/1":
            jwk = json.dumps(self.account[1], sort_keys=True, separators=(",", ":"))
            thumb = base64.urlsafe_b64encode(hashlib.sha256(jwk.encode()).digest()).decode().rstrip("=")
            expected = base64.urlsafe_b64encode(
                hashlib.sha256(f"token-1.{thumb}".encode()).digest()
            ).decode().rstrip("=")
            published = self.dns.records.get((f"_acme-challenge.{self.name}", "TXT"))
            self.authorized = "valid" if published == [f'"{expected}"'] else "invalid"
            return httpx.Response(200, headers=headers, json={})
        if path == "/finalize/1":
            assert self.authorized == "valid"
            csr = x509.load_der_x509_csr(unb64(payload["csr"]))
            names = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            assert names.get_values_for_type(x509.DNSName) == [self.name]
            self.certificate = self.issue(csr.public_key(), self.name, self.ca_key)
            return httpx.Response(200, headers=headers, json={"status": "processing"})
        if path == "/order/1":
            ready = self.certificate is not None
            return httpx.Response(200, headers=headers, json={
                "status": "valid" if ready else "pending",
                "certificate": f"{ACME}/cert/1",
            })
        if path == "/cert/1":
            pem = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in (self.certificate, self.ca))
            return httpx.Response(200, headers=headers, content=pem)
        return httpx.Response(404, headers=headers)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ConnectHomeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.storage = Storage(str(root / "server"))
        await self.storage.load_all()
        self.app = FastAPI()
        self.accounts = install_accounts(self.app, self.storage, lambda: None)
        self.owner = self.accounts.create_user("owner", "fixture-password-123", "Owner", bootstrap=True)
        self.dns = FakeDns()
        self.authority = FakeAuthority(self.dns)
        self.port = free_port()
        self.reached = []
        self.answering = True

        async def call_back(name, address, port, nonce):
            # Connect calls this server back, as it would over the internet.
            if not self.answering:
                raise httpx.ConnectError("refused")
            self.reached.append((name, address, port))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://home") as home:
                response = await home.get(home_side.PROOF_PATH, params={"nonce": nonce})
                response.raise_for_status()
                return response.json()["proof"]

        connect_service = service.create_app(service.Store(root / "connect.db"), self.dns, call_back)
        service_transport = httpx.ASGITransport(app=connect_service)

        async def network(request):
            if request.url.host == "streamsparrow.test":
                return await service_transport.handle_async_request(request)
            if request.url.host == "acme.test":
                await request.aread()
                return self.authority.handle(request)
            if request.url.host == "checkip.amazonaws.com":
                return httpx.Response(200, text=PUBLIC + "\n")
            return httpx.Response(502)

        patcher = patch.object(home_side, "LISTEN_PORT", self.port)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.connect = home_side.Connect(
            self.app,
            root / "server",
            service="https://streamsparrow.test",
            directory=f"{ACME}/directory",
            transport=httpx.MockTransport(network),
        )
        home_side.install_connect(self.app, root / "server", self.connect)
        self.browser = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://home",
            headers={"X-Sparrow-Request": "1"},
            cookies={COOKIE: self.accounts.new_session(self.owner["id"])},
        )

    async def asyncTearDown(self):
        await self.connect.stop()
        await self.browser.aclose()
        self.temp.cleanup()

    async def settle(self):
        for _ in range(200):
            if not self.connect.work:
                return (await self.browser.get("/api/v1/admin/connect")).json()
            await asyncio.sleep(0.05)
        self.fail("Connect never finished switching")

    async def test_switching_on_gets_a_certificate_serves_https_and_passes_the_call_back(self):
        off = (await self.browser.get("/api/v1/admin/connect")).json()
        self.assertFalse(off["enabled"])
        # Nothing answers the call-back while Connect is off.
        self.assertEqual((await self.browser.get(home_side.PROOF_PATH, params={"nonce": "n" * 24})).status_code, 404)
        response = await self.browser.put("/api/v1/admin/connect", json={"enabled": True, "port": 8382})
        self.assertEqual(response.status_code, 200, response.text)
        status = await self.settle()
        self.assertTrue(status["enabled"])
        self.assertIsNone(status["problem"])
        self.assertTrue(status["reachable"], status)
        name = status["name"]
        self.assertRegex(name, r"^[a-z][a-z2-7]{9}\.h\.streamsparrow\.com$")
        self.assertEqual(status["address"], f"https://{name}:8382")
        self.assertEqual(self.reached, [(name, PUBLIC, 8382)])
        self.assertEqual(self.dns.records[(name, "A")], [PUBLIC])
        # The proof record was withdrawn once the authority had checked it.
        self.assertNotIn((f"_acme-challenge.{name}", "TXT"), self.dns.records)
        self.assertTrue(status["listening"])
        self.assertGreater(status["certificate_expires"], 0)
        # The listener serves this app over TLS with the issued certificate.
        context = ssl.create_default_context(
            cadata=self.authority.ca.public_bytes(serialization.Encoding.PEM).decode()
        )
        transport = httpx.AsyncHTTPTransport(verify=context)
        async with httpx.AsyncClient(transport=transport) as tls:
            served = await tls.get(
                f"https://127.0.0.1:{self.port}/api/v1/auth/status",
                extensions={"sni_hostname": name},
                headers={"Host": name},
            )
        self.assertEqual(served.status_code, 200)
        # The key never left this server, and nothing private reached the status.
        folder = Path(self.temp.name) / "server" / "connect"
        self.assertEqual(oct((folder / "key.pem").stat().st_mode & 0o777), "0o600")
        self.assertNotIn("token", json.dumps(status))
        # Switching off forgets this server everywhere.
        await self.browser.put("/api/v1/admin/connect", json={"enabled": False})
        off = await self.settle()
        self.assertFalse(off["enabled"])
        self.assertFalse(off["listening"])
        self.assertNotIn((name, "A"), self.dns.records)
        self.assertFalse((folder / "key.pem").exists())
        self.assertEqual((await self.browser.get(home_side.PROOF_PATH, params={"nonce": "n" * 24})).status_code, 404)

    async def test_an_unreachable_server_says_what_to_do_and_points_no_name_at_it(self):
        self.answering = False  # No port forward: the call-back can't get in.
        await self.browser.put("/api/v1/admin/connect", json={"enabled": True, "port": 8382})
        status = await self.settle()
        self.assertTrue(status["enabled"])
        self.assertFalse(status["reachable"])
        self.assertIsNone(status["address"])
        self.assertIn("port 8382", status["problem"])
        self.assertIn("UPnP", status["problem"])
        self.assertNotIn((status["name"], "A"), self.dns.records)
        # Once the router lets it in, checking again makes it reachable.
        self.answering = True
        checked = (await self.browser.post("/api/v1/admin/connect/check")).json()
        self.assertTrue(checked["reachable"], checked)
        self.assertEqual(self.dns.records[(status["name"], "A")], [PUBLIC])

    async def test_only_an_administrator_can_switch_it(self):
        invitation = self.accounts.invite("viewer", [])
        viewer = self.accounts.create_user("viewer", "fixture-password-456", "Viewer", invitation=invitation)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://home",
            headers={"X-Sparrow-Request": "1"},
            cookies={COOKIE: self.accounts.new_session(viewer["id"])},
        ) as other:
            self.assertEqual((await other.get("/api/v1/admin/connect")).status_code, 403)
            self.assertEqual(
                (await other.put("/api/v1/admin/connect", json={"enabled": True})).status_code, 403
            )


if __name__ == "__main__":
    unittest.main()
