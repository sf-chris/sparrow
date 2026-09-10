import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.storage import Storage
from backend.models import Download, DownloadStatus
from backend.agents.account_api import COOKIE, install_accounts
from backend.agents.models import Job, JobStatus, Event
from backend.agents.operations import Operations, install_operations
from backend.agents.service import AgentService
from backend.agents.store import AgentStore
from backend.agents.nodes import Nodes


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(self.temp.name)
        asyncio.run(self.storage.load_all())
        self.app = FastAPI()
        self.accounts = install_accounts(self.app, self.storage, lambda: None)
        install_operations(self.app, self.storage)
        self.owner = self.accounts.create_user(
            "owner", "fixture-password", "Owner", bootstrap=True
        )
        token = self.accounts.invite("requester", ["local"])
        self.person = self.accounts.create_user(
            "person", "fixture-password", "Person", invitation=token
        )
        self.logs = self.storage.operations
        self.browser = TestClient(self.app)
        self.browser.cookies.set(COOKIE, self.accounts.new_session(self.person["id"]))

    def tearDown(self):
        self.browser.close()
        self.temp.cleanup()

    def test_scope_applies_to_rows_counts_search_and_details_after_revocation(self):
        self.logs.record(
            "request_submitted",
            user_id=self.person["id"],
            library_id="local",
            job_id="my-request",
            title="My film",
        )
        self.logs.record(
            "request_submitted",
            user_id=self.owner["id"],
            library_id="local",
            job_id="private",
            title="Private film",
        )
        self.logs.record("storage_offline", library_id="local", title="Shared storage")
        self.logs.record(
            "import_matched", library_id="elsewhere", title="Restricted film"
        )
        self.logs.record("client_down")
        response = self.browser.get("/api/v1/logs")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 2)
        self.assertNotIn("detail", response.json()["entries"][0])
        for query in ("q=Private", "job_id=private", "q=Restricted", "q=' OR 1=1 --"):
            self.assertEqual(
                self.browser.get("/api/v1/logs?" + query).json()["total"], 0
            )
        with self.accounts.connect() as db:
            db.execute(
                "UPDATE users SET library_scope='[]' WHERE id=?", (self.person["id"],)
            )
        self.assertEqual(self.browser.get("/api/v1/logs").json()["total"], 0)
        self.browser.cookies.set(COOKIE, self.accounts.new_session(self.owner["id"]))
        data = self.browser.get("/api/v1/logs").json()
        self.assertEqual(data["total"], 5)
        self.assertIn("detail", data["entries"][0])
        self.browser.cookies.clear()
        self.assertEqual(self.browser.get("/api/v1/logs").status_code, 401)

    def test_filters_repeats_and_snapshot_pagination_survive_new_events_and_restart(
        self,
    ):
        now = int(time.time() / 300) * 300 + 5
        context = dict(
            user_id=self.person["id"],
            library_id="local",
            title="Harbour Lights",
            job_id="request-1",
        )
        with patch("backend.agents.operations.time.time", return_value=now):
            for _ in range(3):
                self.logs.record("download_stalled", **context)
            self.logs.record("download_completed", **context)
        with patch("backend.agents.operations.time.time", return_value=now + 10):
            self.logs.record("import_matched", **context)
        first = self.browser.get("/api/v1/logs?q=Harbour&limit=1").json()
        self.assertEqual(first["total"], 3)
        with patch("backend.agents.operations.time.time", return_value=now + 11):
            self.logs.record("download_stalled", **context)
            self.logs.record("request_paused", **context)
        restored = Operations(self.temp.name)
        second = restored.page(
            self.person, q="Harbour", limit=1, page=2, snapshot=first["snapshot"]
        )
        third = restored.page(
            self.person, q="Harbour", limit=1, page=3, snapshot=first["snapshot"]
        )
        self.assertEqual(second["entries"][0]["category"], "download")
        self.assertEqual(third["entries"][0]["repeats"], 3)
        self.assertEqual(third["total"], 3)
        self.assertEqual(
            len(
                {
                    first["entries"][0]["id"],
                    second["entries"][0]["id"],
                    third["entries"][0]["id"],
                }
            ),
            3,
        )
        filtered = self.browser.get(
            f"/api/v1/logs?category=download&severity=warning&q=harbour&since={now}&until={now+1}"
        ).json()
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["entries"][0]["repeats"], 3)
        for query in (
            "page=0",
            "limit=101",
            "category=login",
            "since=NaN",
            "since=2&until=1",
            "snapshot=-1",
        ):
            self.assertEqual(
                self.browser.get("/api/v1/logs?" + query).status_code, 422, query
            )

    def test_job_and_download_transitions_record_facts_not_release_names_or_poll_updates(
        self,
    ):
        fixed_clock = patch(
            "backend.agents.operations.time.time", return_value=time.time()
        )
        fixed_clock.start()
        self.addCleanup(fixed_clock.stop)
        store = AgentStore(self.temp.name)
        job = Job(
            title="Harbour Lights",
            tmdb_id=123,
            user_id=self.person["id"],
            library_id="local",
        )
        store.save_job(job)
        store.save_job(job)
        job.status = JobStatus.PAUSED
        store.save_job(job)
        download = Download(
            id="test-download",
            magnet_url="magnet:?private",
            name="secret.release.1080p",
            status=DownloadStatus.DOWNLOADING,
            metadata={"job_id": job.id},
        )
        asyncio.run(self.storage.add_download(download))
        for progress in (0.1, 0.2, 0.3):
            asyncio.run(self.storage.update_download(download.id, progress=progress))
        asyncio.run(
            self.storage.update_download(
                download.id,
                status=DownloadStatus.ERROR,
                error_message="http://user:password@private/path",
            )
        )
        asyncio.run(
            self.storage.update_download(download.id, status=DownloadStatus.DOWNLOADING)
        )
        asyncio.run(
            self.storage.update_download(download.id, status=DownloadStatus.COMPLETED)
        )
        data = self.logs.page(self.person)
        self.assertEqual(data["total"], 5)  # Both starts group in the same window.
        text = json.dumps(data)
        self.assertNotIn("secret.release", text)
        self.assertNotIn("password@", text)
        self.assertEqual(
            next(
                row for row in data["entries"] if row["summary"] == "Download started."
            )["repeats"],
            2,
        )
        # A successful browser login creates no operational row.
        self.accounts.new_session(self.person["id"])
        self.assertEqual(self.logs.page(self.person)["total"], 5)

    def test_measured_storage_transitions_and_agent_stall_events(self):
        nodes = Nodes(self.storage, lambda: None)
        node = {
            "id": "local",
            "name": "Media room",
            "online": True,
            "disabled": False,
            "capabilities": {"roots": [{"id": "library", "available": True}]},
        }
        with patch.object(nodes, "list", return_value=[node]):
            nodes.observe_availability()
            node["online"] = False
            nodes.observe_availability()
            nodes.observe_availability()
            node["online"] = True
            nodes.observe_availability()
        self.assertEqual(self.logs.page(self.person)["total"], 2)
        with patch.object(nodes, "list", return_value=[node]):
            node["capabilities"]["roots"] = [{"available": True}]
            nodes.observe_availability()  # Incomplete reports cannot prove recovery.
            self.assertEqual(
                self.logs.page(self.person)["entries"][0]["severity"], "warning"
            )
        service = AgentService(self.storage, self.temp.name, AsyncMock())
        job = Job(
            title="Harbour Lights",
            tmdb_id=123,
            user_id=self.person["id"],
            library_id="local",
        )
        service.store.save_job(job)
        service._route = AsyncMock()
        asyncio.run(service.emit(Event(kind="download_stalled", job_id=job.id)))
        asyncio.run(service.emit(Event(kind="download_recovered", job_id=job.id)))
        self.assertEqual(self.logs.page(self.person, category="download")["total"], 2)
        self.assertEqual(service._route.await_count, 2)

    def test_retention_and_migration_snapshot(self):
        with self.logs.connect() as db:
            db.execute("INSERT INTO operational_state VALUES('old','failed',0)")
        with patch("backend.agents.operations.time.time", return_value=1):
            self.logs.record("client_down")
        self.logs.record("client_recovered")
        self.assertEqual(self.logs.page(self.owner)["total"], 1)
        with self.logs.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM operational_state").fetchone()[0], 0
            )
        # Reopening is idempotent; history survives and generation has a marker.
        self.assertEqual(Operations(self.temp.name).page(self.owner)["total"], 1)
        self.assertTrue(
            (Path(self.temp.name) / ".migration-operational-history-2").exists()
        )
