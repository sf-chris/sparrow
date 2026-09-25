import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI

from backend.agents.account_api import COOKIE, install_accounts
from backend.agents.onboarding import install_onboarding
from backend.storage import Storage


def node(name="Library", **changes):
    value = {
        "name": name,
        "online": True,
        "disabled": False,
        "capabilities": {
            "probe": True,
            "download": True,
            "roots": [
                {"id": "library", "available": True, "writable": True},
                {"id": "staging", "available": True, "writable": True},
            ],
        },
    }
    value.update(changes)
    return value


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"TMDB_API_KEY": "", "ANTHROPIC_API_KEY": ""})
        self.env.start()
        self.storage = Storage(self.temp.name)
        await self.storage.load_all()
        self.app = FastAPI()
        self.accounts = install_accounts(self.app, self.storage, lambda: None)
        owner = self.accounts.create_user(
            "owner", "fixture-password", "Owner", bootstrap=True
        )
        self.accounts.set_preferences(owner["id"], {})
        self.nodes = Mock()
        self.nodes.list.return_value = []
        install_onboarding(self.app, self.storage, self.nodes)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
            headers={"X-Sparrow-Request": "1"},
            cookies={COOKIE: self.accounts.new_session(owner["id"])},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.env.stop()
        self.temp.cleanup()

    async def test_existing_owner_resumes_and_deferral_is_not_completion(self):
        auth = (await self.client.get("/api/v1/auth/status")).json()
        self.assertTrue(auth["user"]["welcomed"])
        self.assertEqual(auth["server_setup"], {"complete": False, "deferred": False})
        response = await self.client.patch(
            "/api/v1/admin/onboarding",
            json={"mode": "library", "step": "storage", "deferred": True},
        )
        self.assertEqual(response.status_code, 200)
        restarted = Storage(self.temp.name)
        await restarted.load_all()
        config = restarted.get_config()
        self.assertFalse(config.onboarding_complete)
        self.assertTrue(config.onboarding_deferred)
        self.assertEqual(
            (config.onboarding_mode, config.onboarding_step), ("library", "storage")
        )

    async def test_setup_is_admin_only_and_never_returns_keys(self):
        config = self.storage.get_config()
        config.anthropic_api_key = "private-reasoning-key"
        config.tmdb_api_key = "private-title-key"
        await self.storage.save_config(config)
        response = await self.client.get("/api/v1/admin/onboarding")
        self.assertNotIn("private-", response.text)
        self.assertTrue(response.json()["tmdb_configured"])
        self.client.cookies.clear()
        self.assertEqual(
            (await self.client.get("/api/v1/admin/onboarding")).status_code, 401
        )
        invitation = self.accounts.invite("viewer")
        viewer = self.accounts.create_user(
            "viewer", "fixture-password", "Viewer", invitation=invitation
        )
        self.client.cookies.set(COOKIE, self.accounts.new_session(viewer["id"]))
        self.assertIsNone(
            (await self.client.get("/api/v1/auth/status")).json()["server_setup"]
        )
        for method, path, kwargs in [
            ("GET", "", {}),
            ("PATCH", "", {"json": {"deferred": True}}),
            ("POST", "/finish", {}),
        ]:
            self.assertEqual(
                (
                    await self.client.request(
                        method, "/api/v1/admin/onboarding" + path, **kwargs
                    )
                ).status_code,
                403,
            )

    async def test_finish_requires_observed_storage_but_import_does_not_require_providers(
        self,
    ):
        self.assertEqual(
            (await self.client.post("/api/v1/admin/onboarding/finish")).status_code, 422
        )
        await self.client.patch("/api/v1/admin/onboarding", json={"mode": "library"})
        library = node()
        library["capabilities"]["download"] = False
        library["capabilities"]["roots"] = [
            {"id": "library", "available": True, "writable": False}
        ]
        self.nodes.list.return_value = [library]
        result = await self.client.post("/api/v1/admin/onboarding/finish")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["complete"])
        self.assertFalse(result.json()["reasoning_configured"])
        self.assertTrue(
            (await self.client.get("/api/v1/auth/status")).json()["server_setup"][
                "complete"
            ]
        )
        restarted = await self.client.patch(
            "/api/v1/admin/onboarding",
            json={"mode": "autopilot", "step": "providers", "deferred": True},
        )
        self.assertFalse(restarted.json()["complete"])
        self.assertTrue(restarted.json()["deferred"])

    async def test_autopilot_needs_keys_and_a_single_available_writable_destination(
        self,
    ):
        self.nodes.list.return_value = [node()]
        self.assertEqual(
            (await self.client.post("/api/v1/admin/onboarding/finish")).status_code, 422
        )
        with patch.dict(
            os.environ,
            {"TMDB_API_KEY": "env-title", "ANTHROPIC_API_KEY": "env-reasoning"},
        ):
            for change in (
                "offline",
                "disabled",
                "missing-library",
                "readonly",
                "missing-staging",
                "no-probe",
                "no-client",
                "split-nodes",
            ):
                candidate = node()
                caps = candidate["capabilities"]
                if change == "offline":
                    candidate["online"] = False
                elif change == "disabled":
                    candidate["disabled"] = True
                elif change == "missing-library":
                    caps["roots"][0]["available"] = False
                elif change == "readonly":
                    caps["roots"][0]["writable"] = False
                elif change == "missing-staging":
                    caps["roots"].pop()
                elif change == "no-probe":
                    caps["probe"] = False
                elif change == "no-client":
                    caps["download"] = False
                elif change == "split-nodes":
                    caps["roots"].pop()
                self.nodes.list.return_value = [candidate]
                if change == "split-nodes":
                    second = node("Download machine")
                    second["capabilities"]["roots"].pop(0)
                    self.nodes.list.return_value.append(second)
                with self.subTest(change=change):
                    self.assertEqual(
                        (
                            await self.client.post("/api/v1/admin/onboarding/finish")
                        ).status_code,
                        422,
                    )
            self.nodes.list.return_value = [node()]
            self.assertEqual(
                (await self.client.post("/api/v1/admin/onboarding/finish")).status_code,
                200,
            )

    async def test_invalid_progress_and_cross_origin_updates_are_rejected(self):
        for data in ({"complete": True}, {"mode": "anything"}, {"step": "missing"}):
            self.assertEqual(
                (
                    await self.client.patch("/api/v1/admin/onboarding", json=data)
                ).status_code,
                422,
            )
        self.assertEqual(
            (
                await self.client.patch(
                    "/api/v1/admin/onboarding",
                    json={"deferred": True},
                    headers={"Origin": "https://other.invalid"},
                )
            ).status_code,
            403,
        )
