"""Sparrow Connect, the shared service: registration, proofs, check-ins and
the front door, with DNS and the call-back to the home replaced."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

import httpx

spec = importlib.util.spec_from_file_location(
    "connect_service", Path(__file__).resolve().parents[1] / "connect" / "app.py"
)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


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


class ConnectServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dns = FakeDns()
        self.homes = {}  # (address, port) -> token: who really answers where

        async def call_back(name, address, port, nonce):
            token = self.homes.get((address, port))
            if token is None:
                raise httpx.ConnectError("refused")
            return service.proof(service.digest(token), nonce)

        app = service.create_app(
            service.Store(Path(self.temp.name) / "connect.db"), self.dns, call_back
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://streamsparrow.com"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def register(self):
        response = await self.client.post("/api/v1/homes", json={"version": 1})
        self.assertEqual(response.status_code, 200, response.text)
        home = response.json()
        self.assertRegex(home["id"], r"^[a-z][a-z2-7]{9}$")
        self.assertEqual(home["name"], f'{home["id"]}.h.streamsparrow.com')
        return home, {"Authorization": f'Bearer {home["token"]}'}

    async def test_a_name_points_at_a_home_only_after_it_answers_there(self):
        home, auth = await self.register()
        name = home["name"]
        check = f'/api/v1/homes/{home["id"]}/check-in'
        # Nobody answers yet: no record, and a plain explanation.
        response = await self.client.post(
            check, json={"address": "9.9.9.9", "port": 8382}, headers=auth
        )
        self.assertFalse(response.json()["reachable"])
        self.assertIn("port 8382", response.json()["problem"])
        self.assertNotIn((name, "A"), self.dns.records)
        # Someone else's server at that address can't make the proof.
        self.homes[("9.9.9.9", 8382)] = "another-token"
        response = await self.client.post(
            check, json={"address": "9.9.9.9", "port": 8382}, headers=auth
        )
        self.assertIn("Something else", response.json()["problem"])
        self.assertNotIn((name, "A"), self.dns.records)
        # The home itself answers: the name points there.
        self.homes[("9.9.9.9", 8382)] = home["token"]
        response = await self.client.post(
            check, json={"address": "9.9.9.9", "port": 8382}, headers=auth
        )
        self.assertTrue(response.json()["reachable"], response.json())
        self.assertEqual(self.dns.records[(name, "A")], ["9.9.9.9"])
        # Private addresses are never published.
        response = await self.client.post(
            check, json={"address": "192.168.1.103", "port": 8382}, headers=auth
        )
        self.assertEqual(response.json()["problem"], "That isn't a public internet address.")
        self.assertEqual(self.dns.records[(name, "A")], ["9.9.9.9"])

    async def test_only_the_home_changes_its_records_and_forgetting_removes_them(self):
        home, auth = await self.register()
        other, _ = await self.register()
        proof = f'/api/v1/homes/{home["id"]}/acme'
        response = await self.client.put(proof, json={"value": "a" * 43}, headers=auth)
        self.assertTrue(response.json()["ready"])
        challenge = (f'_acme-challenge.{home["name"]}', "TXT")
        self.assertEqual(self.dns.records[challenge], ['"' + "a" * 43 + '"'])
        for headers in ({}, {"Authorization": f'Bearer {other["token"]}'}):
            self.assertEqual(
                (await self.client.put(proof, json={"value": "b" * 43}, headers=headers)).status_code,
                401,
            )
        self.assertEqual(
            (await self.client.put(proof, json={"value": 'x" evil'}, headers=auth)).status_code,
            422,
        )
        self.homes[("9.9.9.9", 8382)] = home["token"]
        await self.client.post(
            f'/api/v1/homes/{home["id"]}/check-in',
            json={"address": "9.9.9.9", "port": 8382},
            headers=auth,
        )
        self.assertEqual(
            (await self.client.delete(f'/api/v1/homes/{home["id"]}', headers=auth)).json(),
            {"forgotten": True},
        )
        self.assertNotIn((home["name"], "A"), self.dns.records)
        self.assertNotIn(challenge, self.dns.records)
        self.assertEqual(
            (await self.client.get(f'/api/v1/homes/{home["id"]}', headers=auth)).status_code,
            401,
        )

    async def test_a_phone_learns_its_home_once_then_the_front_door_sends_it_there(self):
        home, auth = await self.register()
        # Not reachable yet: the link says so instead of sending you nowhere.
        self.assertEqual((await self.client.get(f'/h/{home["id"]}')).status_code, 404)
        self.homes[("9.9.9.9", 8382)] = home["token"]
        await self.client.post(
            f'/api/v1/homes/{home["id"]}/check-in',
            json={"address": "9.9.9.9", "port": 8382},
            headers=auth,
        )
        linked = await self.client.get(f'/h/{home["id"]}')
        self.assertEqual(linked.status_code, 303)
        self.assertEqual(linked.headers["location"], f'https://{home["name"]}:8382/login')
        cookie = linked.headers["set-cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        front = await self.client.get("/")
        self.assertEqual(front.status_code, 303)
        self.assertEqual(front.headers["location"], f'https://{home["name"]}:8382/login')
        await self.client.get("/forget")
        self.assertEqual((await self.client.get("/")).status_code, 200)

    async def test_registrations_have_an_hourly_allowance(self):
        original = service.REGISTRATIONS_PER_HOUR
        service.REGISTRATIONS_PER_HOUR = 2
        try:
            await self.register()
            await self.register()
            self.assertEqual(
                (await self.client.post("/api/v1/homes", json={"version": 1})).status_code, 429
            )
        finally:
            service.REGISTRATIONS_PER_HOUR = original


if __name__ == "__main__":
    unittest.main()
