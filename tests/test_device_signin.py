"""Signing in on another device with a one-time code from a QR code."""

import tempfile
import time
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

from backend.agents.account_api import COOKIE, install_accounts
from backend.storage import Storage


class DeviceSignInTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self.temp.name) / "server"))
        await self.storage.load_all()
        self.app = FastAPI()
        self.accounts = install_accounts(self.app, self.storage, lambda: None)
        self.owner = self.accounts.create_user("owner", "fixture-password-123", "Owner", bootstrap=True)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def client(self, user=None):
        cookies = {COOKIE: self.accounts.new_session(user["id"])} if user else {}
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://home.test",
            headers={"X-Sparrow-Request": "1"},
            cookies=cookies,
        )

    async def test_a_code_signs_in_a_new_device_once(self):
        async with self.client(self.owner) as computer, self.client() as phone, self.client() as other:
            made = await computer.post("/api/v1/auth/device-link")
            self.assertEqual(made.status_code, 200, made.text)
            code = made.json()["code"]
            self.assertGreater(made.json()["expires"], time.time() + 500)
            used = await phone.post("/api/v1/auth/device-link/redeem", json={"code": code})
            self.assertEqual(used.status_code, 200, used.text)
            self.assertEqual(used.json()["user"]["username"], "owner")
            self.assertIn(COOKIE, phone.cookies)
            status = (await phone.get("/api/v1/auth/status")).json()
            self.assertEqual(status["user"]["username"], "owner")
            # The same code can't sign in a second device.
            again = await other.post("/api/v1/auth/device-link/redeem", json={"code": code})
            self.assertEqual(again.status_code, 401)
            self.assertNotIn(COOKIE, other.cookies)

    async def test_codes_point_at_a_working_connect_address_only(self):
        class Connect:
            def __init__(self, **state):
                self.saved = state

            def state(self):
                return self.saved

        viewer = self.accounts.create_user(
            "viewer", "fixture-password-456", "Viewer", invitation=self.accounts.invite("viewer", None)
        )
        async with self.client(viewer) as phone_owner:
            # Without Sparrow Connect the phone opens this same address.
            self.assertIsNone((await phone_owner.post("/api/v1/auth/device-link")).json()["link"])
            link = "https://streamsparrow.com/h/abc123"
            self.app.state.connect = Connect(enabled=True, reachable=False, link=link)
            self.assertIsNone((await phone_owner.post("/api/v1/auth/device-link")).json()["link"])
            # Anyone signed in, not just an administrator, gets the address.
            self.app.state.connect = Connect(enabled=True, reachable=True, link=link)
            self.assertEqual((await phone_owner.post("/api/v1/auth/device-link")).json()["link"], link)

    async def test_codes_lapse_and_need_a_signed_in_person_to_make(self):
        async with self.client() as stranger:
            self.assertEqual((await stranger.post("/api/v1/auth/device-link")).status_code, 401)
            self.assertEqual(
                (await stranger.post("/api/v1/auth/device-link/redeem", json={"code": "x" * 32})).status_code,
                401,
            )
        code, _ = self.accounts.device_link(self.owner["id"])
        with self.accounts.connect() as db:
            db.execute("UPDATE device_links SET expires=?", (time.time() - 1,))
        self.assertIsNone(self.accounts.redeem_device_link(code))
        # A disabled person's code signs no one in.
        code, _ = self.accounts.device_link(self.owner["id"])
        with self.accounts.connect() as db:
            db.execute("UPDATE users SET disabled=1 WHERE id=?", (self.owner["id"],))
        self.assertIsNone(self.accounts.redeem_device_link(code))


if __name__ == "__main__":
    unittest.main()
