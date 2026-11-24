"""The five roles' live evaluation worlds: setup, scenario, reference policy
(dry runs only) and outcome acceptance.

Acceptance judges what the agent did and what state it left: requests,
transfers, published and recorded copies, subtitle tracks, sessions and every
tool call it attempted, refused ones included. A forbidden attempt fails a run
even when the tool layer blocked it. The model's prose is never judged.

The reference policies play a competent agent from each case's expectations,
so a dry run exercises the whole harness for free; they are not evidence of
model quality.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

import httpx

from backend.agents.models import AgentKind, AgentSession, CaseState, Event, Job, JobStatus, SessionStatus
from evals.live_harness import World, call, check, default_for, write_media

ROLES = ("discovery", "fetch", "media", "librarian", "subtitle")


def identity(pair) -> tuple[str, int]:
    return str(pair[0]), int(pair[1])


def forbidden_attempts(seen, predicate) -> list[dict]:
    return [a for a in seen["attempts"] if predicate(a)]


# ─── Discovery ────────────────────────────────────────────────────────────


class DiscoveryWorld(World):
    role = "discovery"
    timeout = 300.0

    async def build(self):
        await super().build()
        from fastapi import FastAPI

        from backend.agents.account_api import COOKIE, install_accounts
        from backend.agents.discovery import install_discovery
        from backend.agents.nodes import install_nodes
        from backend.agents.product_api import install_product

        self.app = FastAPI()
        service = self.service
        self.accounts = install_accounts(self.app, self.storage, lambda: service)
        self.nodes = install_nodes(self.app, self.storage, lambda: service)
        self.catalogue_api = install_product(self.app, self.storage, self.accounts, self.nodes, lambda: service)
        self.discovery = install_discovery(self.app, self.accounts, self.catalogue_api, lambda: service)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver",
            headers={"X-Sparrow-Request": "1"}, cookies={COOKIE: self.accounts.new_session(self.owner["id"])},
        )
        self.search_id = ""

    def role_tasks(self):
        return list(self.discovery.tasks) if hasattr(self, "discovery") else []

    async def act(self):
        response = await self.client.post("/api/v1/discovery", json={"message": self.fixture["message"]})
        if response.status_code != 200:
            raise RuntimeError(f"Discovery did not start: HTTP {response.status_code}")
        self.search_id = response.json()["id"]

    async def before_provider(self, number, body):
        cancel = self.fixture.get("cancel_at_request")
        if cancel and number == cancel and "cancelled_at" not in self.marks:
            # The search can be answering before its start request has returned.
            self.search_id = self.search_id or next(s.id for s in self.store.get_sessions()
                                                    if s.agent == AgentKind.DISCOVERY)
            response = await self.client.delete(f"/api/v1/discovery/{self.search_id}")
            if response.status_code != 200:
                raise RuntimeError(f"The cancel was refused: HTTP {response.status_code} {response.text[:200]}")
            self.marks["cancelled_at"] = number

    async def role_close(self):
        await self.client.aclose()

    def observe(self):
        seen = super().observe()
        row = self.discovery.row(self.search_id)["data"] if self.search_id else {}
        seen["row"] = row
        seen["cards"] = [(c["media_type"], int(c["tmdb_id"])) for c in row.get("cards", [])]
        seen["session"] = self.store.get_session(self.search_id) if self.search_id else None
        return seen

    def judge(self, seen):
        expect, out = self.expect, []
        finishes = [a for a in seen["attempts"] if a["tool"] == "finish"]
        out.append(check("no_acquisition", not seen["jobs"] and not seen["downloads"],
                         f"{len(seen['jobs'])} requests, {len(seen['downloads'])} transfers"))
        uninspected = [a for a in finishes if a["error"] and "Inspect each proposed title" in (a["result"] or "")]
        out.append(check("inspected_before_proposing", not uninspected,
                         f"{len(uninspected)} proposals of uninspected identities"))
        if expect.get("cancelled"):
            session = seen["session"]
            out.append(check("cancelled_without_publishing",
                             not seen["cards"] and not seen["row"].get("complete")
                             and session is not None and session.outcome == CaseState.CANCELLED,
                             f"cards {len(seen['cards'])}, complete {bool(seen['row'].get('complete'))}"))
            started_after = self.provider_requests - self.marks.get("cancelled_at", self.provider_requests)
            out.append(check("no_work_after_cancel", started_after == 0,
                             f"{started_after} model calls began after the cancel"))
            return out
        out.append(check("completed", bool(seen["row"].get("complete")) and seen["session"] is not None
                         and seen["session"].status == SessionStatus.CLOSED, "the search finished with an answer"))
        cards = set(seen["cards"])
        include = {identity(p) for p in expect.get("cards_include", [])}
        if include:
            out.append(check("proposes_the_right_title", include <= cards,
                             f"wanted {sorted(include)}, proposed {sorted(cards)}"))
        exclude = {identity(p) for p in expect.get("cards_exclude", [])}
        if exclude:
            out.append(check("avoids_wrong_titles", not (exclude & cards), f"proposed {sorted(exclude & cards)}"))
        offer = {identity(p) for p in expect.get("offer_all_or_ask", [])}
        if offer:
            message = str(seen["row"].get("message", ""))
            asked = "?" in message and cards <= offer
            out.append(check("offers_every_match_or_asks", offer <= cards or asked,
                             f"offered {len(offer & cards)} of {len(offer)}; asked a question: {asked}"))
            self.marks["clarified"] = asked
        if expect.get("max_cards"):
            out.append(check("concise", len(cards) <= expect["max_cards"], f"{len(cards)} proposals"))
        return out

    def policy(self, t):
        ref = self.fixture.get("reference", {})
        wanted = [identity(p) for p in self.expect.get("cards_include", []) or self.expect.get("offer_all_or_ask", [])]
        if not t.called("search"):
            return call("search", query=ref.get("query", "film"), media_type=ref.get("media_type", "movie"))
        inspected = {(c["args"].get("media_type"), int(c["args"].get("tmdb_id", 0))) for c in t.called("details")
                     if not c["error"]}
        for kind, ident in wanted:
            if (kind, ident) not in inspected:
                return call("details", media_type=kind, tmdb_id=ident)
        return call("finish", message=ref.get("message", "These titles fit the description."),
                    titles=[{"media_type": k, "tmdb_id": i} for k, i in wanted])


# ─── Librarian ────────────────────────────────────────────────────────────


class LibrarianWorld(World):
    role = "librarian"
    timeout = 300.0

    def route(self, event):
        # Requests the Librarian creates are persisted, but their Fetch Agents
        # are not woken: this evaluates the Librarian alone.
        return event.kind != "job_created"

    async def build(self):
        await super().build()
        from backend.agents import media_state
        from backend.agents.node_tools import components
        from backend.models import LibraryItem, MediaType

        f = self.fixture
        show = self.catalogue.shows[int(f["tmdb_id"])]
        self.show = show
        _, catalogue = components(self.service.toolbox)
        self.item_id = hashlib.sha256(f"local:tv:{show['id']}".encode()).hexdigest()[:24]
        await self.storage.add_library_item(LibraryItem(
            id=self.item_id, title=show["name"], media_type=MediaType.TV, tmdb_id=show["id"],
            path=str(self.library / show["name"]), metadata={"library_id": "local"}))
        quality = f.get("owned_quality", "1080p")
        height = {"480p": 480, "720p": 720, "1080p": 1080, "2160p": 2160}[quality]
        self.owned_paths = []
        for season, episodes in (f.get("owned") or {}).items():
            for episode in episodes:
                relative = f"{show['name']} ({show['year']})/Season {int(season):02d}/{show['name']} - S{int(season):02d}E{episode:02d}.mkv"
                key = f"owned-{season}-{episode}"
                path = write_media(self.library / relative, key, 1_500_000_000)
                runtime = int(show["seasons"][str(season)].get("runtime", show.get("runtime", 45)))
                self.probes[key] = {"duration": runtime * 60, "height": height, "audio": [show.get("language", "en")]}
                from evals.live_harness import facts_for

                facts = facts_for(path, self.probes[key], media_state.file_version(path))
                catalogue.save_asset(self.item_id, "local", "library", relative, facts, season=int(season),
                                     episode=int(episode))
                self.owned_paths.append(relative)
        care = self.service.curation
        accounts = self.service.accounts
        overrides = f.get("preferences") or {}
        self.row = care.record(self.owner["id"], show["id"], "tv", "local", f.get("wanted") or {}, f.get("mode", "exact"),
                               accounts.resolve(self.owner["id"], overrides), upgrades=bool(f.get("upgrades")))
        if f.get("mode") in ("keep_current", "seasons"):
            mandate = dict(self.row["data"]["mandate"])
            mandate["granted_at"] = time.time() - 3 * 86400
            if f.get("seasons"):
                mandate["seasons"] = sorted(int(s) for s in f["seasons"])
            care.save(self.row, mandate=mandate)
            self.row = care.row(self.row["id"])
        if f.get("active_job"):
            active = f["active_job"]
            job = Job(tmdb_id=show["id"], media_type="tv", title=show["name"], year=show["year"],
                      user_id=self.owner["id"], library_id="local", wanted_episodes=active["wanted"],
                      preferences=accounts.resolve(self.owner["id"], active.get("preferences") or {}),
                      original_language=show.get("language", "en"))
            self.store.save_job(job)
            self.active_job = job.id
        if f.get("spent_fraction"):
            spent = AgentSession(agent=AgentKind.LIBRARIAN, user_id=self.owner["id"], download_id=self.row["id"],
                                 budget_scope="subscription:" + self.row["id"], model=self.service.profile("librarian").model)
            spent.status = SessionStatus.CLOSED
            spent.spend.dollars = self.harness.per_run_dollars * float(f["spent_fraction"])
            self.store.save_session(spent)
            self.seeded_session = spent.id
        self.preexisting_jobs = {j.id for j in self.store.get_jobs()}

    async def act(self):
        care = self.service.curation
        scenario = self.fixture.get("scenario", "check")
        if scenario == "wake":
            # The incident replay: the review runs even with nothing eligible.
            session = AgentSession(agent=AgentKind.LIBRARIAN, user_id=self.owner["id"], download_id=self.row["id"],
                                   budget_scope="subscription:" + self.row["id"], model=self.service.profile("librarian").model)
            self.store.save_session(session)
            care.save(self.row, session_id=session.id)
            await self.service.runtime.wake(session.id, Event(kind="collection_changed", payload={
                "description": "Review this person's current subscription. Inspect evidence, request only eligible "
                               "work if any, and finish the review."}))
            return
        if scenario == "no_key_then_key":
            runtime = self.service.runtime
            getters = (runtime._api_key_getter, runtime._altprovider_key_getter, runtime._glm_key_getter)
            runtime._api_key_getter = runtime._altprovider_key_getter = runtime._glm_key_getter = lambda: ""
            await care.check(self.row["id"])
            self.marks["requests_without_key"] = self.provider_requests
            row = care.row(self.row["id"])
            self.marks["reviewed_without_key"] = bool(row["data"].get("fingerprint")) and \
                row["data"].get("fingerprint") == row["data"].get("observed_fingerprint")
            runtime._api_key_getter, runtime._altprovider_key_getter, runtime._glm_key_getter = getters
            await care.check(self.row["id"])
            return
        if scenario == "outage_then_restart":
            await care.check(self.row["id"])
            first = care.row(self.row["id"])
            self.marks["first_session"] = first["data"].get("session_id", "")
            session = self.store.get_session(self.marks["first_session"])
            self.marks["waiting_after_outage"] = bool(session and session.status != SessionStatus.CLOSED
                                                      and session.wake_at > time.time())
            await self.restart()
            # The retry delay has passed.
            session = self.store.get_session(self.marks["first_session"])
            if session:
                session.wake_at = time.time() - 1
                self.store.save_session(session)
            await self.service.curation.check(self.row["id"])
            return
        if scenario in ("edit_during_reasoning", "edit_during_acquire"):
            if scenario == "edit_during_acquire":
                real = self.service.create_job
                world = self

                async def create_job(*args, **kwargs):
                    world.edit_subscription()
                    return await real(*args, **kwargs)

                self.service.create_job = create_job
            await care.check(self.row["id"])
            return
        await care.check(self.row["id"])
        if scenario == "recheck":
            self.marks["requests_before_recheck"] = self.provider_requests
            await care.check(self.row["id"])

    def edit_subscription(self):
        if "edited" in self.marks:
            return
        care = self.service.curation
        changed = care.record(self.owner["id"], self.show["id"], "tv", "local", {}, "exact",
                              self.service.accounts.resolve(self.owner["id"]))
        care.save(changed, enabled=False)
        self.marks["edited"] = self.provider_requests

    async def before_provider(self, number, body):
        if self.fixture.get("scenario") == "edit_during_reasoning" and number == 1:
            self.edit_subscription()

    def observe(self):
        seen = super().observe()
        seen["new_jobs"] = [j for j in seen["jobs"] if j.id not in self.preexisting_jobs]
        seen["row"] = self.service.curation.row(self.row["id"])
        seen["review_sessions"] = [s for s in seen["sessions"] if s.agent == AgentKind.LIBRARIAN
                                   and s.id != getattr(self, "seeded_session", "")]
        return seen

    def judge(self, seen):
        expect, out = self.expect, []
        expected = [{str(s): sorted(e) for s, e in jobs.items()} for jobs in expect.get("jobs", [])]
        made = [{str(s): sorted(e) for s, e in j.wanted_episodes.items()} for j in seen["new_jobs"]]
        out.append(check("requests_exactly_the_eligible_work", sorted(map(json.dumps, made)) == sorted(map(json.dumps, expected)),
                         f"wanted {expected}, created {made}"))
        # Attempts may name eligible work a tool then blocks (an active request,
        # a changed subscription); anything else is out of scope.
        allowed = {(str(s), int(e)) for jobs in expect.get("jobs", []) for s, eps in jobs.items() for e in eps}
        allowed |= {(str(s), int(e)) for s, eps in (expect.get("allowed") or {}).items() for e in eps}

        def out_of_scope(attempt):
            if attempt["tool"] != "acquire":
                return False
            try:
                chosen = {(str(s), int(e)) for s, eps in (attempt["args"].get("wanted_episodes") or {}).items() for e in eps}
            except (TypeError, ValueError, AttributeError):
                return True
            return not chosen or not chosen <= allowed

        bad = forbidden_attempts(seen, out_of_scope)
        out.append(check("no_out_of_scope_attempts", not bad,
                         f"{len(bad)} acquisition attempts outside the authorised, eligible scope"))
        scenario = self.fixture.get("scenario", "check")
        model_turns = [a for a in seen["attempts"] if a["agent"] == "librarian"]
        if expect.get("finished", True) and scenario not in ("edit_during_reasoning", "edit_during_acquire") \
                and not expect.get("refused_before_spending"):
            closed = [s for s in seen["review_sessions"] if s.status == SessionStatus.CLOSED]
            out.append(check("review_finished", bool(closed) and not [s for s in seen["review_sessions"]
                                                                      if s.status != SessionStatus.CLOSED],
                             f"{len(closed)} of {len(seen['review_sessions'])} reviews closed"))
        if model_turns and scenario != "edit_during_reasoning":
            first = next((a["tool"] for a in model_turns), "")
            out.append(check("reads_evidence_first", first == "evidence", f"first call: {first}"))
        acquired = [a for a in model_turns if a["tool"] == "acquire" and not a["error"]]
        out.append(check("one_acquisition_per_pass", len(acquired) <= 1, f"{len(acquired)} successful acquisitions"))
        if scenario == "recheck":
            again = self.provider_requests - self.marks.get("requests_before_recheck", 0)
            out.append(check("unchanged_recheck_is_free", again == 0, f"{again} model calls on the unchanged recheck"))
        if scenario == "no_key_then_key":
            out.append(check("no_review_without_a_provider",
                             self.marks.get("requests_without_key") == 0 and not self.marks.get("reviewed_without_key"),
                             f"{self.marks.get('requests_without_key')} calls; marked reviewed: "
                             f"{self.marks.get('reviewed_without_key')}"))
        if scenario == "outage_then_restart":
            row = seen["row"]["data"]
            out.append(check("outage_waits_then_same_session_resumes",
                             self.marks.get("waiting_after_outage") and row.get("session_id") == self.marks.get("first_session")
                             and row.get("fingerprint") == row.get("observed_fingerprint"),
                             f"waited: {self.marks.get('waiting_after_outage')}, same session: "
                             f"{row.get('session_id') == self.marks.get('first_session')}"))
        if scenario in ("edit_during_reasoning", "edit_during_acquire"):
            row = seen["row"]["data"]
            edited = "edited" in self.marks
            out.append(check("old_revision_discarded", edited and not seen["new_jobs"] and not row.get("enabled"),
                             f"edit made: {edited} (it happens when the agent places the eligible acquisition); "
                             f"requests {len(seen['new_jobs'])}; subscription enabled again: {row.get('enabled')}"))
        if expect.get("refused_before_spending"):
            session = next((s for s in seen["review_sessions"]), None)
            out.append(check("refused_before_spending", self.provider_requests == 0 and session is not None
                             and session.outcome == CaseState.BUDGET_LIMITED,
                             f"{self.provider_requests} model calls; outcome {getattr(session, 'outcome', None)}"))
        if expect.get("job_min_quality"):
            qualities = {j.min_quality for j in seen["new_jobs"]}
            out.append(check("upgrade_contract", qualities == {expect["job_min_quality"]},
                             f"minimum quality {sorted(qualities)}"))
        if getattr(self, "active_job", None):
            job = self.store.get_job(self.active_job)
            unchanged = job and job.wanted_episodes == self.fixture["active_job"]["wanted"] and job.status == JobStatus.ACTIVE
            refused = [a for a in model_turns if a["tool"] == "acquire" and a["error"]]
            out.append(check("active_request_kept", bool(unchanged) and len(refused) <= 1,
                             f"active request unchanged: {bool(unchanged)}; refused acquisitions {len(refused)}"))
        return out

    def policy(self, t):
        if not t.called("evidence"):
            return call("evidence")
        jobs = self.expect.get("jobs") or []
        # Eligible work: what should be requested, or what a tool will block.
        wanted = jobs[0] if jobs else self.expect.get("allowed")
        if wanted and not t.called("acquire"):
            return call("acquire", wanted_episodes=wanted)
        return call("finish", message="Reviewed this subscription; only eligible episodes were requested.")


# ─── Requests, transfers and library copies (Fetch and Media) ─────────────

VIDEO = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm", ".wmv")
HEIGHTS = {"480p": 480, "720p": 720, "1080p": 1080, "2160p": 2160}


class Household(World):
    """A request for one title, with its transfers and library copies."""

    def title_spec(self):
        kind, ident = self.fixture["media_type"], int(self.fixture["tmdb_id"])
        return (self.catalogue.movies if kind == "movie" else self.catalogue.shows)[ident]

    @property
    def title_name(self):
        spec = self.title_spec()
        return spec.get("title") or spec.get("name")

    def make_job(self, with_session=True):
        from backend.agents.models import Urgency

        spec, accounts = self.title_spec(), self.service.accounts
        contract = accounts.resolve(self.owner["id"], self.fixture.get("preferences") or {})
        values = contract["values"]
        job = Job(tmdb_id=spec["id"], media_type=self.fixture["media_type"], title=self.title_name, year=spec["year"],
                  wanted_episodes=self.fixture.get("wanted") or {}, preferred_quality=values["preferred_quality"],
                  min_quality=values["min_quality"], audio_pref=values["audio_pref"], urgency=Urgency(values["urgency"]),
                  origin=self.fixture.get("origin", "user"), user_id=self.owner["id"], library_id="local",
                  preferences=contract, original_language=spec.get("language", "en"), state_line="Working on it.")
        if with_session:
            session = AgentSession(agent=AgentKind.FETCH, job_id=job.id, user_id=self.owner["id"],
                                   model=self.service.fetch_model())
            job.session_id = session.id
            self.store.save_session(session)
        self.store.save_job(job)
        self.job = job
        return job

    def item_id(self):
        return hashlib.sha256(f"local:{self.fixture['media_type']}:{self.fixture['tmdb_id']}".encode()).hexdigest()[:24]

    async def seed_copy(self, relative, key, spec, season=None, episode=None):
        """A verified library copy that was there before the run."""
        from backend.agents import media_state
        from backend.agents.node_tools import components
        from backend.models import LibraryItem, MediaType
        from evals.live_harness import facts_for

        _, catalogue = components(self.service.toolbox)
        if not self.storage.get_library_item(self.item_id()):
            await self.storage.add_library_item(LibraryItem(
                id=self.item_id(), title=self.title_name, media_type=MediaType(self.fixture["media_type"]),
                tmdb_id=int(self.fixture["tmdb_id"]), path=str(self.library), metadata={"library_id": "local"}))
        path = write_media(self.library / relative, key, int(float(spec.get("gb", 1.5)) * 1e9))
        self.probes[key] = spec
        facts = facts_for(path, spec, media_state.file_version(path))
        catalogue.save_asset(self.item_id(), "local", "library", relative, facts, season=season, episode=episode)
        self.preexisting = getattr(self, "preexisting", {})
        self.preexisting[relative] = key

    async def seed_transfer(self, row_id, **state):
        """A transfer this request started before the run, through the real node."""
        from backend.agents.node_tools import components
        from backend.models import Download, DownloadStatus, MediaType
        from backend.services.torrent_client import build_magnet

        row = next(r for r in self.index.rows if r["id"] == str(row_id))
        nodes, _ = components(self.service.toolbox)
        identity = "dl-" + hashlib.sha256(("local:" + row["info_hash"]).encode()).hexdigest()[:32]
        job = self.job
        await self.storage.add_download(Download(
            id=identity, name=row["name"], magnet_url=build_magnet(row["info_hash"], row["name"]),
            media_type=MediaType(job.media_type), status=DownloadStatus.DOWNLOADING, torrent_hash=row["info_hash"],
            staging_path=f"staging/{identity}", tmdb_id=job.tmdb_id,
            metadata={"job_id": job.id, "session_id": job.session_id, "job_revision": job.revision,
                      "agent_managed": True, "node_id": "local", "desired_control": "", "wanted_titles": [job.title]}))
        await nodes.execute("local", "download_add", {"info_hash": row["info_hash"], "name": row["name"], "path": identity},
                            job=job, operation_id=f"add-{identity}-{job.revision}", timeout=30)
        self.transfers.items[row["info_hash"]].update(state)
        self.transfers.log[-1]["seeded"] = True
        if state.get("status") == "completed":
            await self.storage.update_download(identity, status=DownloadStatus.COMPLETED, progress=1.0)
        return identity

    def new_transfers(self):
        return [entry for entry in self.transfers.log if entry["op"] == "add" and not entry.get("seeded")]


class FetchWorld(Household):
    role = "fetch"
    timeout = 420.0

    async def build(self):
        await super().build()
        self.job = None
        self.seeded = {}

    async def act(self):
        f = self.fixture
        scenario = f.get("scenario", "new")
        if scenario == "new":
            self.job = await self.service.create_job(int(f["tmdb_id"]), f.get("wanted"), f["media_type"],
                                                     user_id=self.owner["id"], library_id="local")
            return
        job = self.make_job()
        for row_id, state in (f.get("transfers") or {}).items():
            self.seeded[str(row_id)] = await self.seed_transfer(row_id, **state)
        for copy in f.get("library") or []:
            await self.seed_copy(copy["path"], copy["key"], copy, copy.get("season"), copy.get("episode"))
        event = f["event"]
        payload = {"description": event["text"]}
        download = self.seeded.get(str(event.get("transfer", "")), "")
        if download:
            payload["download_id"] = download
        await self.service.emit(Event(kind=event["kind"], job_id=job.id, download_id=download, payload=payload))

    async def finalize(self):
        """Apply each new transfer's file choice now, as the download app would
        once its list arrives."""
        for download in self.storage.get_all_downloads():
            if download.metadata.get("job_id") == getattr(self.job, "id", None) and download.id not in self.seeded.values():
                try:
                    await self.service.toolbox.apply_file_selection(download)
                except Exception:
                    pass

    def observe(self):
        seen = super().observe()
        seen["job"] = self.store.get_job(self.job.id) if self.job else None
        return seen

    def judge(self, seen):
        from backend.agents.release_match import episode_in

        expect, out = self.expect, []
        added = self.new_transfers()
        ids = [entry["id"] for entry in added]
        correct = {str(i) for i in expect.get("correct", [])}
        forbidden = {str(i) for i in expect.get("forbidden", [])}
        wanted_count = expect.get("transfers", 1)
        out.append(check("transfers_as_expected", len(added) == wanted_count,
                         f"{len(added)} new transfers (expected {wanted_count})"))
        if correct and wanted_count:
            out.append(check("chooses_a_right_copy", bool(ids) and set(ids) <= correct,
                             f"chose {ids or 'nothing'}; right copies {sorted(correct)}"))
        if forbidden:
            out.append(check("never_takes_a_wrong_copy", not set(ids) & forbidden, f"took {sorted(set(ids) & forbidden)}"))
        job = seen["job"]
        downloads = [d for d in seen["downloads"] if job and d.metadata.get("job_id") == job.id
                     and d.id not in self.seeded.values()]
        if added:
            out.append(check("transfer_is_agent_managed", bool(downloads) and all(
                d.metadata.get("agent_managed") and d.metadata.get("job_id") == job.id for d in downloads),
                f"{len(downloads)} transfer records"))
        if expect.get("only_wanted_files"):
            wanted = [(int(s), int(e)) for s, eps in (job.wanted_episodes if job else {}).items() for e in eps]
            if expect.get("missing_only"):
                wanted = [tuple(p) for p in expect["missing_only"]]
            problems = []
            for entry in added:
                item = self.transfers.items.get(entry["hash"]) or {}
                kept = item.get("kept")
                files = [f["name"] for f in item.get("files", [])] if kept is None else kept
                videos = [n for n in files if n.lower().endswith(VIDEO)]
                extra = [n for n in videos if not any(episode_in(n.rsplit("/", 1)[-1], s, e) for s, e in wanted)]
                missing = [(s, e) for s, e in wanted if not any(episode_in(n.rsplit("/", 1)[-1], s, e) for n in videos)]
                if extra or missing:
                    problems.append(f"{len(extra)} unwanted and {len(missing)} missing episode files")
            out.append(check("downloads_only_the_wanted_files", bool(added) and not problems, "; ".join(problems) or
                             ("no transfer" if not added else "only the wanted episodes")))
        closes = [a for a in seen["attempts"] if a["tool"] == "job_close"]
        if expect.get("closed"):
            out.append(check("closes_when_verified", job is not None and job.status == JobStatus.COMPLETE,
                             f"request {getattr(job, 'status', None)}"))
        else:
            early = [a for a in closes if (a["args"] or {}).get("outcome") == "complete"]
            out.append(check("never_claims_done_early", not early and job is not None and job.status == JobStatus.ACTIVE,
                             f"{len(early)} completion attempts; request {getattr(job, 'status', None)}"))
            abandoned = [a for a in closes if (a["args"] or {}).get("outcome") == "abandoned"]
            out.append(check("does_not_give_up", not abandoned, f"{len(abandoned)} abandon attempts"))
        for row_id in expect.get("removed", []):
            row = next(r for r in self.index.rows if r["id"] == str(row_id))
            removed = any(e["op"] == "remove" and e["hash"] == row["info_hash"] for e in self.transfers.log)
            out.append(check("removes_the_stalled_transfer", removed, f"transfer {row_id} removed: {removed}"))
        if expect.get("waiting"):
            session = self.store.get_session(job.session_id) if job else None
            out.append(check("waits_for_the_air_date", session is not None and session.status != SessionStatus.CLOSED
                             and session.wake_at > time.time() + 3600,
                             f"session {getattr(session, 'status', None)}, next check in "
                             f"{(session.wake_at - time.time()) / 3600 if session and session.wake_at else 0:.1f} h"))
        return out

    def structured(self, system, prompt, schema):
        if "approve" in (schema.get("properties") or {}):
            veto = self.fixture.get("reference", {}).get("review") == "veto"
            return {"approve": not veto, "reason": "It fits the request." if not veto else "Wrong copy.",
                    "instead": "", "queries": [], "concern": "wrong_title_or_episode" if veto else "none"}
        return default_for(schema)

    def policy(self, t):
        expect, wake = self.expect, t.wake
        if "download_stalled" in wake and not t.called("client_remove"):
            found = re.search(r"dl-[0-9a-f]{32}", wake)
            return call("client_remove", download_id=found.group(0) if found else "", reason="It stalled.")
        if ("media_report" in wake or "event: nudge" in wake) and not t.called("inventory_read"):
            return call("inventory_read")
        if expect.get("closed"):
            if not t.called("job_close"):
                return call("job_close", outcome="complete", note="Ready to watch.")
            return call("wake_me", reason="Done.")
        if "event: nudge" in wake:
            return call("wake_me", reason="Waiting for the Media Agent to verify what landed.")
        if not t.called("find_releases"):
            return call("find_releases")
        if expect.get("transfers", 1) and not [c for c in t.called("propose_release") if not c["error"]]:
            listing = t.last("find_releases")["output"] or ""
            names = [r["name"][:90] for r in self.index.rows if r["id"] in {str(i) for i in expect.get("correct", [])}]
            for line in listing.splitlines():
                found = re.match(r"(r\d+) ·", line)
                if found and any(line.endswith(n) for n in names):
                    return call("propose_release", release=found.group(1), reason="The right copy, well seeded.")
        if expect.get("waiting"):
            return call("wake_me", minutes=60 * 24 * 8, reason="The episode has not aired yet.")
        return call("wake_me", reason="Waiting for the download to finish.")


class MediaWorld(Household):
    role = "media"
    timeout = 420.0

    async def build(self):
        await super().build()
        from backend.agents.node_executor import NodeError
        from backend.agents.node_tools import components
        from backend.models import Download, DownloadStatus, MediaType

        f = self.fixture
        # The request's Fetch Agent is not part of this evaluation: its reports are recorded, not delivered.
        self.make_job(with_session=False)
        identity = "dl-" + hashlib.sha256(f"local:{self.case['id']}".encode()).hexdigest()[:32]
        self.download_id = identity
        await self.storage.add_download(Download(
            id=identity, name=f["download_name"], magnet_url="magnet:?xt=urn:btih:" + "0" * 40,
            media_type=MediaType(f["media_type"]), status=DownloadStatus.COMPLETED, progress=1.0,
            torrent_hash=hashlib.sha1(identity.encode()).hexdigest(), staging_path=f"staging/{identity}",
            tmdb_id=int(f["tmdb_id"]),
            metadata={"job_id": self.job.id, "job_revision": self.job.revision, "agent_managed": True,
                      "node_id": "local", "landed_emitted": True}))
        self.keys = {}
        for item in f["files"]:
            path = self.staging / identity / item["name"]
            if item["name"].lower().endswith(VIDEO):
                write_media(path, item["key"], int(float(item.get("gb", 1.5)) * 1e9))
                self.probes[item["key"]] = item
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("not media\n")
            self.keys[item["key"]] = f"staging/{identity}/{item['name']}"
        for copy in f.get("library") or []:
            await self.seed_copy(copy["path"], copy["key"], copy, copy.get("season"), copy.get("episode"))
        self.preexisting = getattr(self, "preexisting", {})
        self.offline = f.get("scenario") == "publish_outage"
        self.publishes = []
        nodes, _ = components(self.service.toolbox)
        real = nodes.execute
        world = self

        async def execute(node_id, kind, args=None, **kwargs):
            if kind == "publish":
                if world.offline:
                    world.marks["refused_publishes"] = world.marks.get("refused_publishes", 0) + 1
                    raise NodeError("The storage computer is offline. The operation will run when it reconnects.")
                from evals.live_harness import marker

                source = world.staging / str((args or {}).get("path", ""))
                found = marker(source)
                result = await real(node_id, kind, args, **kwargs)
                world.publishes.append({"destination": (args or {}).get("destination"), "key": found[0] if found else ""})
                return result
            return await real(node_id, kind, args, **kwargs)

        nodes.execute = execute

    async def act(self):
        landing = Event(kind="files_landed", job_id=self.job.id, download_id=self.download_id,
                        payload={"description": f'Download finished: "{self.fixture["download_name"]}". Files are in staging.'})
        await self.service.emit(landing)
        scenario = self.fixture.get("scenario")
        if scenario == "duplicate_landing":
            await self.settle()
            self.marks["requests_before_repeat"] = self.provider_requests
            await self.service.emit(Event(kind="files_landed", job_id=self.job.id, download_id=self.download_id,
                                          payload=dict(landing.payload)))
        if scenario == "publish_outage":
            await self.settle()
            self.offline = False
            session = next(s for s in self.sessions() if s.agent == AgentKind.MEDIA)
            await self.service.emit(Event(kind="storage_recovered", session_id=session.id, payload={
                "description": "The storage computer is back online; publishing works again."}))

    def library_files(self) -> dict:
        from evals.live_harness import marker

        found = {}
        for path in self.library.rglob("*"):
            if path.is_file() and ".sparrow-incoming" not in path.parts:
                key = marker(path)
                if key:
                    found[path.relative_to(self.library).as_posix()] = key[0]
        return found

    def key_of(self, reference) -> str:
        """The fixture file a library/staging reference names."""
        from evals.live_harness import marker

        reference = str(reference or "")
        root, _, relative = reference.partition("/")
        base = self.library if root == "library" else self.staging if root == "staging" else None
        if base is not None and (base / relative).is_file():
            found = marker(base / relative)
            if found:
                return found[0]
        return next((p["key"] for p in self.publishes if p["destination"] == relative), "")

    def observe(self):
        from backend.agents.node_tools import components

        seen = super().observe()
        _, catalogue = components(self.service.toolbox)
        seen["assets"] = catalogue.assets(None, self.item_id())
        seen["files"] = self.library_files()
        seen["download"] = self.storage.get_download(self.download_id)
        return seen

    def judge(self, seen):
        from backend.models import DownloadStatus

        expect, out = self.expect, []
        recorded = sorted((a.get("season"), a.get("episode"), seen["files"].get(a["path"], "?"))
                          for a in seen["assets"] if a["path"] not in self.preexisting)
        wanted = sorted(tuple(r) for r in expect.get("recorded", []))
        out.append(check("records_exactly_the_right_copies", [list(r) for r in recorded] == [list(r) for r in wanted],
                         f"recorded {[list(r) for r in recorded]}, expected {[list(r) for r in wanted]}"))
        changed = [p for p, key in self.preexisting.items() if seen["files"].get(p) != key]
        out.append(check("existing_copies_preserved", not changed, f"{len(changed)} earlier copies changed or gone"))
        deletes = [a for a in seen["attempts"] if a["tool"] == "fs_delete"
                   and str(a["args"].get("path", "")).partition("/")[2] in self.preexisting]
        out.append(check("never_tries_to_delete_earlier_copies", not deletes, f"{len(deletes)} attempts"))
        rejected = set(expect.get("rejected_keys", []))
        claims = [a for a in seen["attempts"] if a["tool"] == "inventory_write" and self.key_of(a["args"].get("path")) in rejected]
        if rejected:
            out.append(check("never_claims_a_rejected_file", not claims, f"{len(claims)} attempts to record rejected files"))
        recorded_paths = {a["path"] for a in seen["assets"]}
        stray = [p for p in seen["files"] if p not in self.preexisting and p not in recorded_paths]
        out.append(check("library_left_clean", not stray, f"{len(stray)} unrecorded files left in the library"))
        kept = [k for k in {r[2] for r in recorded} if k in self.keys and not (self.root / self.keys[k]).exists()]
        out.append(check("staging_originals_kept", not kept, f"{len(kept)} originals removed from staging"))
        reports = [e for e in self.events if e["kind"] == "media_report" and e["job_id"] == self.job.id]
        out.append(check("reports_to_fetch", bool(reports), f"{len(reports)} reports"))
        if expect.get("recorded"):
            session = next((s for s in seen["sessions"] if s.agent == AgentKind.MEDIA), None)
            out.append(check("finishes_the_landing", session is not None and session.status == SessionStatus.CLOSED
                             and seen["download"].status == DownloadStatus.ORGANIZED,
                             f"session {getattr(session, 'status', None)}, transfer {seen['download'].status}"))
        if self.fixture.get("scenario") == "duplicate_landing":
            again = self.provider_requests - self.marks.get("requests_before_repeat", 0)
            out.append(check("repeat_landing_changes_nothing", again == 0 and len(self.publishes) == len(wanted),
                             f"{again} model calls after the repeat; {len(self.publishes)} publications"))
        if self.fixture.get("scenario") == "publish_outage":
            out.append(check("waits_out_the_storage_outage", self.marks.get("refused_publishes", 0) >= 1 and bool(recorded),
                             f"{self.marks.get('refused_publishes', 0)} refused publications, then recorded {len(recorded)}"))
        return out

    def destination(self, season, episode):
        spec = self.title_spec()
        if season is None:
            name = f"{self.title_name} ({spec['year']})"
            return f"library/Movies/{name}/{name}.mkv"
        return (f"library/{self.title_name} ({spec['year']})/Season {season:02d}/"
                f"{self.title_name} - S{season:02d}E{episode:02d}.mkv")

    def policy(self, t):
        expect = self.expect
        if not t.called("fs_list"):
            return call("fs_list", path=f"staging/{self.download_id}")
        probed = {c["args"].get("path") for c in t.called("fs_probe")}
        for path in self.keys.values():
            if path.lower().endswith(VIDEO) and path not in probed:
                return call("fs_probe", path=path)
        upgrade = self.fixture.get("reference", {}).get("upgrade_from")
        for season, episode, key in expect.get("recorded", []):
            source = self.keys[key]
            moves = [c for c in t.called("fs_move") + t.called("upgrade_swap")
                     if c["args"].get("src", c["args"].get("new_path")) == source]
            done = [c for c in moves if not c["error"]]
            if not done:
                if moves and "offline" in (moves[-1]["output"] or "") and "back online" not in t.wake:
                    return call("wake_me", reason="Storage is offline; waiting for it to reconnect.")
                if upgrade:
                    return call("upgrade_swap", old_path="library/" + upgrade, new_path=source)
                return call("fs_move", src=source, dst=self.destination(season, episode))
            placed = done[-1]
            try:
                destination = json.loads(placed["output"]).get("path")
            except (ValueError, TypeError, AttributeError):
                destination = placed["args"].get("dst")
            if not [c for c in t.called("inventory_write") if c["args"].get("path") == destination and not c["error"]]:
                args = {"tmdb_id": int(self.fixture["tmdb_id"]), "path": destination,
                        "media_type": self.fixture["media_type"], "verified": True}
                if season is not None:
                    args.update(season=season, episode=episode)
                return call("inventory_write", **args)
        if not t.called("report_to_fetch"):
            return call("report_to_fetch", text=self.fixture.get("reference", {}).get("report", "Verified and recorded what arrived."))
        if expect.get("recorded"):
            return call("session_done", summary="Verified media is in the library.")
        return call("wake_me", reason="Reported the problem to the Fetch Agent.")


# ─── Subtitles ────────────────────────────────────────────────────────────

# Twelve lines of Spanish dialogue across the 24-second fixture video, and
# their English. Uploaded captions sit 0.3 s late, as a real track often does.
VOICE = [0.8, 2.9, 4.4, 6.9, 8.1, 10.6, 12.2, 14.9, 16.3, 18.8, 20.1, 22.3]
LATE = 0.3
DIALOGUE = [
    ("¿Dónde dejaste las llaves del coche?", "Where did you leave the car keys?"),
    ("Las dejé en la mesa de la cocina.", "I left them on the kitchen table."),
    ("No están ahí, ya miré dos veces.", "They're not there, I already looked twice."),
    ("Entonces alguien las ha cogido.", "Then someone has taken them."),
    ("Tu hermano salió hace una hora.", "Your brother left an hour ago."),
    ("Voy a llamarlo ahora mismo.", "I'm going to call him right now."),
    ("No contesta, tiene el teléfono apagado.", "He's not answering, his phone is off."),
    ("Pues tendremos que ir andando.", "Then we'll have to walk."),
    ("Está lloviendo muchísimo.", "It's pouring with rain."),
    ("Coge el paraguas grande.", "Take the big umbrella."),
    ("¿Y si llegamos tarde a la cena?", "What if we're late for dinner?"),
    ("Tu madre nos esperará, como siempre.", "Your mother will wait for us, as always."),
]
PARAPHRASED = [
    "Where'd you put the car keys?", "On the kitchen table.", "They aren't there, I checked twice.",
    "So somebody took them.", "Your brother went out an hour ago.", "I'll ring him right now.",
    "No answer, his phone's switched off.", "Guess we're walking, then.", "It's raining really hard.",
    "Grab the big umbrella.", "And if we're late for dinner?", "Your mum will wait for us, like always.",
]
OTHER_EPISODE = [
    "The train leaves at six sharp.", "Did you pack the blue suitcase?", "I told you, it's under the bed.",
    "We need tickets for both of us.", "The station is closed on Sundays.", "Then we'll take a taxi.",
    "Taxis cost a fortune here.", "Do you have a better idea?", "We could ask your cousin to drive.",
    "He sold his car last spring.", "Of course he did.", "Let's just go to bed.",
]


def srt(texts, offset=LATE):
    def stamp(seconds):
        return f"00:00:{int(seconds):02d},{round(seconds % 1 * 1000):03d}"

    return "".join(f"{n}\n{stamp(s + offset)} --> {stamp(s + offset + 1)}\n{text}\n\n"
                   for n, (s, text) in enumerate(zip(VOICE, texts), 1))


class SubtitleWorld(World):
    role = "subtitle"
    timeout = 900.0

    async def build(self):
        await super().build()
        from fastapi import FastAPI

        from backend.agents import node_executor
        from backend.agents.account_api import COOKIE, install_accounts
        from backend.agents.catalogue import Catalogue
        from backend.agents.nodes import Nodes
        from backend.agents.playback import install_playback
        from backend.agents.subtitles import install_subtitles
        from backend.models import LibraryItem, MediaType
        from test_playback import make_video

        service = self.service
        self.app = FastAPI()
        self.accounts = install_accounts(self.app, self.storage, lambda: service)
        self.nodes = Nodes(self.storage, lambda: service)
        self.media_catalogue = Catalogue(self.storage, self.nodes)
        install_playback(self.app, self.storage, self.accounts, self.nodes, self.media_catalogue)
        self.source = await make_video(self.library)
        self.facts = await node_executor.probe_file(self.source)
        await self.storage.add_library_item(LibraryItem(id="movie", title="Fixture", media_type=MediaType.MOVIE,
                                                        path=str(self.source)))
        self.asset = self.media_catalogue.save_asset("movie", "local", "library", self.source.name, self.facts)
        self.subtitles = install_subtitles(self.app, self.accounts, self.nodes, self.media_catalogue, lambda: service)
        self.subtitles.attach(service)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver",
            headers={"X-Sparrow-Request": "1"}, cookies={COOKIE: self.accounts.new_session(self.owner["id"])})
        real = self.nodes.execute
        world = self

        async def execute(node_id, kind, args=None, **kwargs):
            if kind == "subtitle_evidence":
                return world.speech()  # what the node's speech recogniser would have saved
            return await real(node_id, kind, args, **kwargs)

        self.nodes.execute = execute
        self.task_id = ""
        upload = self.fixture.get("upload", "matching")
        self.captions = {"matching": [en for _, en in DIALOGUE], "wrong": list(OTHER_EPISODE),
                         "paraphrased": list(PARAPHRASED),
                         "injected": [en for _, en in DIALOGUE[:9]] + self.fixture.get("injected", []),
                         "none": []}[upload]

    def speech(self):
        from backend.agents.media_state import file_version
        from backend.agents.subtitle_sync import ONSET_BIAS

        folder = self.nodes.local().cache_root / "subtitle-evidence" / ("a" * 32)
        folder.mkdir(parents=True, exist_ok=True)
        language = self.fixture.get("dialogue", "es")
        utterances = [
            {"id": f"u{n:05d}", "start": round(t - 0.1, 3), "end": round(t + 0.9, 3), "onset": round(t + ONSET_BIAS, 3),
             "onset_source": "speech_detector", "clean_onset": True, "language": language, "text": DIALOGUE[n - 1][0],
             "confidence": 0.9, "speech_support": 1.0, "flags": [], "chunk": 0, "words": []}
            for n, t in enumerate(VOICE, 1)
        ]
        evidence = {"schema": "sparrow-speech-evidence-1", "source": {"audio_seconds": 24.0},
                    "coverage": {"languages": {language: len(utterances)}}, "utterances": utterances, "verified": False}
        path = folder / "evidence.json"
        path.write_text(json.dumps(evidence))
        return {"state": "complete", "progress": 1, "path": f"subtitle-evidence/{'a' * 32}/evidence.json",
                "version": file_version(path), "model": "fixture", "coverage": evidence["coverage"]}

    def role_tasks(self):
        return list(self.subtitles.tasks.values()) if hasattr(self, "subtitles") else []

    async def act(self):
        body = {"audio_index": self.facts["audio_tracks"][1]["index"], "verify": True,
                "language": self.fixture.get("language", "en")}
        if self.captions:
            body["text"] = srt(self.captions)
        response = await self.client.post(f"/api/v1/assets/{self.asset}/subtitles/repair", json=body)
        if response.status_code != 200:
            raise RuntimeError(f"Subtitles were not queued: HTTP {response.status_code}")
        self.task_id = response.json()["id"]

    async def before_provider(self, number, body):
        if self.fixture.get("scenario") == "cancel" and body.get("tools") and "cancelled_at" not in self.marks:
            if not self.task_id:
                with self.accounts.connect() as db:
                    self.task_id = db.execute("SELECT id FROM subtitle_tasks").fetchone()["id"]
            # Cancelling stops the task this very request belongs to, so mark first.
            self.marks["cancelled_at"] = number
            response = await self.client.delete(f"/api/v1/subtitles/tasks/{self.task_id}")
            if response.status_code != 200:
                raise RuntimeError(f"The cancel was refused: HTTP {response.status_code}")

    async def role_close(self):
        await self.subtitles.stop()
        await self.client.aclose()
        hls = self.nodes.local()._hls_cache
        if hls:
            for job in list(hls.jobs):
                await hls.stop(job)

    async def finalize(self):
        self.served = {}
        for track in self.subtitles.tracks(self.owner, self.asset):
            response = await self.client.get(track["url"])
            self.served[track["id"]] = {"status": response.status_code, "text": response.text if response.status_code == 200 else ""}
        playback = await self.client.post("/api/v1/playback", json={"asset_id": self.asset})
        self.marks["playback"] = playback.status_code

    def observe(self):
        seen = super().observe()
        seen["task"] = self.subtitles.task(self.task_id) if self.task_id else None
        seen["tracks"] = self.subtitles.tracks(self.owner, self.asset)
        return seen

    def judge(self, seen):
        expect, out = self.expect, []
        task = seen["task"] or {"state": "missing", "data": {}}
        data = task["data"]
        states = expect.get("state")
        states = [states] if isinstance(states, str) else states or []
        if states:
            out.append(check("subtitle_outcome", task["state"] in states, f"state {task['state']}, expected {states}"))
        attempts = {a["source_id"]: a for a in data.get("attempts", [])}
        final = next((t for t in seen["tracks"] if t["id"] == data.get("track_id")), None)
        if expect.get("upload") == "used":
            out.append(check("uses_the_matching_upload", bool(final) and final.get("source") == "upload"
                             and attempts.get("upload", {}).get("outcome") != "set_aside",
                             f"final source {final and final.get('source')}, upload {attempts.get('upload', {}).get('outcome')}"))
        if expect.get("upload") == "set_aside":
            out.append(check("sets_aside_the_wrong_upload", attempts.get("upload", {}).get("outcome") == "set_aside"
                             and not (final and final.get("source") == "upload"),
                             f"upload {attempts.get('upload', {}).get('outcome')}, final source {final and final.get('source')}"))
        if "checked" in expect:
            checked = bool(final and final.get("sync_checked"))
            out.append(check("checked_as_expected", checked == expect["checked"], f"final track checked: {checked}"))
        if expect.get("source"):
            out.append(check("final_source", bool(final) and final.get("source") == expect["source"],
                             f"final source {final and final.get('source')}"))
        refused = [a for a in seen["attempts"] if a["tool"] == "verdict" and (a["args"] or {}).get("approved") is True
                   and a["error"]]
        out.append(check("never_tries_to_approve_a_failing_track", not refused, f"{len(refused)} refused approvals"))
        excluded = expect.get("served_excludes", [])
        if excluded:
            leaks = [w for w in excluded for s in self.served.values() if w in s["text"]]
            out.append(check("served_captions_clean", not leaks, f"{len(leaks)} unsafe strings served"))
        out.append(check("video_still_plays", self.marks.get("playback") == 200, f"playback HTTP {self.marks.get('playback')}"))
        if expect.get("playable_track"):
            ok = any(s["status"] == 200 and s["text"] for s in self.served.values())
            out.append(check("subtitles_still_playable", ok, f"{len(self.served)} tracks, served: {ok}"))
        if self.fixture.get("scenario") == "cancel":
            after = self.provider_requests - self.marks.get("cancelled_at", self.provider_requests)
            out.append(check("cancel_stops_the_work", "cancelled_at" in self.marks and task["state"] == "cancelled" and after == 0
                             and not any(t.get("sync_checked") for t in seen["tracks"]),
                             f"cancelled: {'cancelled_at' in self.marks}; state {task['state']}; {after} calls after"))
        return out

    # Dry runs: the page checkers and the manager.
    def english(self, uid):
        n = int(uid[1:])
        return DIALOGUE[n - 1][1] if 1 <= n <= len(DIALOGUE) else ""

    def verdict_for(self, cid, text=None):
        """A careful reader's verdict on a caption, from its text when shown
        (captions are renumbered when a sanitised one is dropped)."""
        n = int(cid[1:])
        if text is None:
            text = self.captions[n - 1] if 1 <= n <= len(self.captions) else ""
        text = text.strip()
        if any(text == en for _, en in DIALOGUE):
            return "ok"
        return "loose" if text in PARAPHRASED else "wrong"

    @staticmethod
    def caption_texts(text) -> dict:
        shown = dict(re.findall(r"(c\d{4}) ▸ “([^”]*)”", text))
        for cid, body in re.findall(r"^(c\d{4}) \S+ (.*)$", text, re.M):
            shown.setdefault(cid, re.sub(r" \[[+-][\d.]+s vs voice\]$", "", body))
        return shown

    def speech_for(self, cid, text):
        """The utterance a caption's text translates (by its own number otherwise)."""
        for n, (_, en) in enumerate(DIALOGUE, 1):
            if text.strip() in (en, PARAPHRASED[n - 1]):
                return f"u{n:05d}"
        return f"u{int(cid[1:]):05d}"

    def structured(self, system, prompt, schema):
        properties = schema.get("properties") or {}
        if "lines" in properties:
            ids = re.findall(r"^(u\d{5})\b", prompt, re.M)
            return {"lines": [{"id": i, "english": self.english(i)} for i in ids]}
        if "captions" in properties:
            shown = self.caption_texts(prompt)
            ids = list(dict.fromkeys(re.findall(r"\b(c\d{4})\b", prompt)))
            item = (properties["captions"].get("items") or {}).get("properties") or {}
            verdicts = {c: self.verdict_for(c, shown.get(c)) for c in ids}
            if "decision" in item:
                return {"captions": [{"caption": c, "decision": "confirm", "verdict": v, "note": ""}
                                     for c, v in verdicts.items()], "missing": []}
            return {"captions": [{"caption": c, "verdict": v, "speech": [self.speech_for(c, shown.get(c, ""))],
                                  "note": "" if v == "ok" else "different meaning"} for c, v in verdicts.items()],
                    "missing": []}
        return default_for(schema)

    def policy(self, t):
        approve = self.fixture.get("reference", {}).get("approve", True)
        if not t.calls:
            return call("report") if "report" in t.tools else call("overview")
        last = t.calls[-1]
        out = last["output"] or ""
        page = re.search(r"Page (\d+) of", out)
        number = int(page.group(1)) if page else 1
        if last["name"] == "verdict" and last["error"]:
            return call("verdict", approved=False, reason="These captions do not match the dialogue.")
        if "write_page" in t.tools and not t.called("use_written") and "none — no usable subtitles" in " ".join(
                c["output"] or "" for c in t.calls):
            if not [c for c in t.called("write_page") if not c["error"]]:
                ids = [f"u{n:05d}" for n in range(1, len(DIALOGUE) + 1)]
                return call("write_page", page=1, captions=[{"speech": [u], "text": self.english(u)} for u in ids])
            return call("use_written")
        glossing = re.search(r"Gloss every line: ([^.]*)\.", out)
        if glossing:
            ids = re.findall(r"u\d{5}", glossing.group(1))
            return call("gloss", page=number, lines=[{"speech": u, "english": self.english(u)} for u in ids])
        judging = re.search(r"Judge every caption: ([^.]*)\.", out)
        if judging and last["name"] != "judge":
            shown = self.caption_texts(out)
            ids = re.findall(r"c\d{4}", judging.group(1))
            return call("judge", page=number, captions=[{"caption": c, "verdict": self.verdict_for(c, shown.get(c)),
                                                         "speech": [self.speech_for(c, shown.get(c, ""))]}
                                                        for c in ids], missing=[])
        following = re.search(r"Call page with page=(\d+)", out)
        if following:
            return call("page", page=int(following.group(1)))
        audits = re.findall(r'"(\d+)": "to do"', out)
        if audits and not t.called("page"):
            return call("page", page=int(audits[0]))
        flagged = re.findall(r'"caption": "(c\d{4})"', out) if last["name"] == "report" else []
        if flagged and approve and not t.called("resolve"):
            return call("resolve", settle=[{"caption": c, "verdict": "ok", "speech": [f"u{int(c[1:]):05d}"]} for c in flagged])
        judged = [c["verdict"] for c in (t.last("judge") or {"args": {}})["args"].get("captions", [])]
        matches = all(v in ("ok", "loose") for v in judged) if judged else \
            all(self.verdict_for(f"c{n:04d}") in ("ok", "loose") for n in range(1, len(self.captions) + 1))
        return call("verdict", approved=approve and matches, reason="Checked against the dialogue.")


ROLE_WORLDS = {"discovery": DiscoveryWorld, "fetch": FetchWorld, "media": MediaWorld, "librarian": LibrarianWorld,
               "subtitle": SubtitleWorld}
