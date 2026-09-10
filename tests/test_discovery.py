import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
from fastapi import FastAPI
from backend.storage import Storage
from backend.models import SparrowConfig, LibraryItem, MediaType
from backend.agents.account_api import install_accounts, COOKIE
from backend.agents.nodes import install_nodes
from backend.agents.product_api import install_product
from backend.agents.discovery import install_discovery
from backend.agents.service import AgentService
from backend.agents.models import SessionStatus


class Block:
    type = "tool_use"

    def __init__(self, name, args):
        self.name = name
        self.input = args
        self.id = name

    def to_dict(self):
        return {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }


def response(name, args):
    return SimpleNamespace(content=[Block(name, args)])


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(self.temp.name)
        await self.storage.load_all()
        await self.storage.save_config(
            SparrowConfig(
                anthropic_api_key="fixture-not-sent", tmdb_api_key="fixture-not-sent"
            )
        )
        self.service = AgentService(self.storage, self.temp.name, AsyncMock())
        self.service.emit = AsyncMock()
        self.app = FastAPI()
        self.accounts = install_accounts(self.app, self.storage, lambda: self.service)
        self.owner = self.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )
        self.viewer = self.accounts.create_user(
            "viewer",
            "fixture-password-456",
            "Viewer",
            invitation=self.accounts.invite("viewer", []),
        )
        self.nodes = install_nodes(self.app, self.storage, lambda: self.service)
        self.catalogue = install_product(
            self.app, self.storage, self.accounts, self.nodes, lambda: self.service
        )
        self.discovery = install_discovery(
            self.app, self.accounts, self.catalogue, lambda: self.service
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
            headers={"X-Sparrow-Request": "1"},
            cookies={COOKIE: self.accounts.new_session(self.owner["id"])},
        )
        self.service.runtime._track_spend = lambda *_: None
        self.service.toolbox.tmdb_get = AsyncMock(
            return_value={
                "id": 42,
                "title": "Verified Title",
                "overview": "A space mystery.",
                "original_language": "en",
            }
        )

    async def asyncTearDown(self):
        for task in list(self.discovery.tasks):
            task.cancel()
        await asyncio.gather(*self.discovery.tasks, return_exceptions=True)
        await self.client.aclose()
        self.temp.cleanup()

    async def search(self):
        result = await self.client.post(
            "/api/v1/discovery", json={"message": "Find a space mystery"}
        )
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["id"]

    async def settle(self):
        await asyncio.gather(*list(self.discovery.tasks))

    async def test_agent_uses_evidence_before_proposal_and_never_creates_jobs(self):
        calls = [
            response("search", {"media_type": "movie", "query": "space mystery"}),
            response("details", {"media_type": "movie", "tmdb_id": 42}),
            response(
                "finish",
                {
                    "message": "This title fits the space mystery description. Open it to choose what to get.",
                    "titles": [{"media_type": "movie", "tmdb_id": 42}],
                },
            ),
        ]
        self.service.runtime._call_api = AsyncMock(side_effect=calls)
        identity = await self.search()
        await self.settle()
        result = (await self.client.get(f"/api/v1/discovery/{identity}")).json()
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["cards"][0]["title"], "Verified Title")
        self.assertEqual(self.service.runtime._call_api.call_count, 3)
        self.assertEqual(self.service.store.get_jobs(), [])
        self.assertEqual(len(self.service.store.get_session(identity).messages), 7)
        self.assertEqual(
            self.service.store.get_session(identity).user_id, self.owner["id"]
        )
        self.client.cookies.set(COOKIE, self.accounts.new_session(self.viewer["id"]))
        self.assertEqual(
            (await self.client.get(f"/api/v1/discovery/{identity}")).status_code, 404
        )

    async def test_invented_identity_is_refused_then_agent_can_research_and_correct(
        self,
    ):
        self.service.runtime._call_api = AsyncMock(
            side_effect=[
                response(
                    "finish",
                    {
                        "message": "Invented",
                        "titles": [{"media_type": "movie", "tmdb_id": 999}],
                    },
                ),
                response("details", {"media_type": "movie", "tmdb_id": 42}),
                response(
                    "finish",
                    {
                        "message": "Corrected using the catalogue.",
                        "titles": [{"media_type": "movie", "tmdb_id": 42}],
                    },
                ),
            ]
        )
        identity = await self.search()
        await self.settle()
        session = self.service.store.get_session(identity)
        self.assertTrue(session.messages[2]["content"][0]["is_error"])
        self.assertEqual(
            self.discovery.row(identity)["data"]["cards"][0]["tmdb_id"], 42
        )

    async def test_cancelled_model_response_cannot_publish_or_restart_work(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(*_):
            entered.set()
            await release.wait()
            return response("finish", {"message": "Should be discarded.", "titles": []})

        self.service.runtime._call_api = slow
        identity = await self.search()
        await entered.wait()
        self.assertEqual(
            (await self.client.delete(f"/api/v1/discovery/{identity}")).status_code, 200
        )
        release.set()
        await self.settle()
        self.assertEqual(
            self.service.store.get_session(identity).status, SessionStatus.CLOSED
        )
        self.assertFalse(self.discovery.row(identity)["data"]["complete"])

    async def test_node_and_product_http_models_accept_valid_bodies(self):
        enrollment = await self.client.post(
            "/api/v1/admin/nodes/enroll", json={"name": "Node fixture"}
        )
        self.assertEqual(enrollment.status_code, 200, enrollment.text)
        code = enrollment.json()["code"]
        payload = {
            "code": code,
            "credential": "fixture-secret-credential-with-32-characters",
            "capabilities": {"protocol": 1},
        }
        paired = await self.client.post("/api/v1/node/pair", json=payload)
        self.assertEqual(paired.status_code, 200, paired.text)
        self.assertEqual(
            (await self.client.post("/api/v1/node/pair", json=payload)).json(),
            paired.json(),
        )
        preview = await self.client.post(
            "/api/v1/admin/imports/preview", json={"node_id": "nonexistent"}
        )
        self.assertEqual(preview.status_code, 422)
        self.assertIsInstance(preview.json()["detail"], str)
        bad_scope = await self.client.post(
            "/api/v1/jobs", json={"tmdb_id": 42, "media_type": "tv"}
        )
        self.assertEqual(bad_scope.status_code, 422)
        self.assertIn("episodes", bad_scope.text.lower())
