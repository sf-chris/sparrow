"""Plumbing for the live role evaluations (run_live.py): every external effect
becomes a fixture, at the boundary where Sparrow meets the outside world.

- Network. Every httpx request passes through Gate. The model providers
  (https://retired-provider.invalid, api.provider.com, api.z.ai) reach the real network in a live run
  and ScriptedProvider in a dry run; TMDB and the torrent index answer from
  the case's catalogue and index; anything else is refused and recorded as a
  violation, which fails the run. A dry run also refuses raw socket
  connections, so nothing at all leaves the machine.
- Downloads. The download app is FakeTorrentManager: transfers are recorded
  with the files they would fetch, never started.
- Files and probes. Media files a case writes start with a marker naming
  their synthetic probe answer and declared size; the real storage node
  lists, publishes, withdraws and stats them in a temporary jail. Unmarked
  files (the subtitle fixture's video) are probed for real.

The patches are installed once for the whole run. Each case runs in its own
asyncio task with CURRENT set to its World, and context variables follow the
tasks it starts, so every intercepted call is charged to the right case.
"""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import contextvars
import copy
import hashlib
import json
import math
import re
import socket
import time
import urllib.parse
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.agents import runtime as runtime_module

CURRENT: contextvars.ContextVar = contextvars.ContextVar("sparrow_live_world", default=None)
PROVIDERS = {"https://retired-provider.invalid": "altprovider", "api.provider.com": "provider", "api.z.ai": "glm"}
MARKER = b"SPARROW-FIXTURE:"
# The real transport: only a live run's provider requests ever reach it.
_ORIGINAL_ASYNC = httpx.AsyncHTTPTransport.handle_async_request


class SpendCapReached(Exception):
    """The run's dollar cap is spent: no further paid call is sent."""


class NetworkRefused(httpx.ConnectError):
    pass


# ─── Fixture files ────────────────────────────────────────────────────────


def write_media(path: Path, key: str, declared_bytes: int) -> Path:
    """A placeholder media file: tiny on disk, the declared size everywhere
    Sparrow measures it, and probed as the case's facts for `key`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MARKER + f"{key}:{int(declared_bytes)}\n".encode() + b"\0" * 64)
    return path


def marker(path) -> tuple[str, int] | None:
    try:
        with open(path, "rb") as stream:
            head = stream.read(160)
    except OSError:
        return None
    if not head.startswith(MARKER):
        return None
    try:
        key, size = head[len(MARKER):].split(b"\n", 1)[0].decode().rsplit(":", 1)
        return key, int(size)
    except ValueError:
        return None


def quality_for(width: int, height: int) -> str:
    short = min(width, height)
    return "2160p" if short >= 1600 else "1080p" if short >= 800 else "720p" if short >= 570 else "480p"


def facts_for(path, spec: dict, version: dict) -> dict:
    """What ffprobe would report for a fixture file (the same shape as
    node_executor.probe_file)."""
    height = int(spec.get("height", 1080))
    width = int(spec.get("width", round(height * 16 / 9)))
    audio = [
        {"index": n + 1, "codec": "aac", "language": lang, "title": "", "default": n == 0, "forced": False,
         "hearing_impaired": False}
        for n, lang in enumerate(spec.get("audio", ["en"]))
    ]
    subtitles = [
        {"index": len(audio) + n + 1, "codec": "subrip", "language": lang, "title": "", "default": False,
         "forced": False, "hearing_impaired": False}
        for n, lang in enumerate(spec.get("subtitles", []))
    ]
    return {
        "version": version, "size_bytes": version["size_bytes"], "duration": float(spec.get("duration", 0)),
        "width": width, "height": height, "video_codec": spec.get("codec", "h264"), "video_index": 0,
        "format": "matroska,webm" if str(path).endswith(".mkv") else "mov,mp4,m4a,3gp,3g2,mj2",
        "quality": quality_for(width, height), "audio_tracks": audio,
        "audio_languages": [t["language"] for t in audio], "subtitle_tracks": subtitles,
    }


# ─── The catalogue and the index ──────────────────────────────────────────


def _words(text) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).lower())


def _date(base: str, days: float) -> str:
    stamp = calendar.timegm(time.strptime(base, "%Y-%m-%d")) + days * 86400
    return time.strftime("%Y-%m-%d", time.gmtime(stamp))


def _today(days: float = 0) -> str:
    # Sparrow reads air dates as UTC midnights.
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + days * 86400))


class Catalogue:
    """TMDB as a case describes it. Paths and answers follow the real API."""

    def __init__(self, spec: dict):
        self.movies = {int(m["id"]): m for m in spec.get("movie", [])}
        self.shows = {int(s["id"]): s for s in spec.get("tv", [])}
        self.seen: list[str] = []

    def answer(self, path: str, params: dict):
        self.seen.append(path)
        parts = [p for p in path.split("/") if p]
        try:
            if parts[0] == "search" and len(parts) == 2:
                return self.search(parts[1], params.get("query", ""))
            kind, ident = parts[0], int(parts[1])
            if kind == "movie" and ident in self.movies:
                if len(parts) == 2:
                    return self.movie(self.movies[ident])
                if parts[2:] == ["alternative_titles"]:
                    return {"id": ident, "titles": self._alternatives(self.movies[ident])}
            if kind == "tv" and ident in self.shows:
                show = self.shows[ident]
                if len(parts) == 2:
                    return self.show(show)
                if parts[2:] == ["alternative_titles"]:
                    return {"id": ident, "results": self._alternatives(show)}
                if len(parts) == 4 and parts[2] == "season" and str(int(parts[3])) in show.get("seasons", {}):
                    return {"season_number": int(parts[3]), "episodes": self.episodes(show, int(parts[3]))}
        except (IndexError, ValueError):
            pass
        return None

    @staticmethod
    def _alternatives(item):
        return [{"iso_3166_1": a.get("country", "US"), "title": a["title"], "type": a.get("type", "")}
                for a in item.get("alt", [])]

    def movie(self, m):
        return {"id": m["id"], "title": m["title"], "original_title": m.get("original_title", m["title"]),
                "release_date": m.get("date") or f"{m['year']}-06-01", "runtime": m.get("runtime", 100),
                "original_language": m.get("language", "en"), "overview": m.get("overview", ""),
                "genres": [{"id": n, "name": g} for n, g in enumerate(m.get("genres", []))],
                "poster_path": None, "status": "Released"}

    def episodes(self, show, season):
        spec = show["seasons"][str(season)]
        names = spec.get("names", {})
        recent, future = set(spec.get("recent", [])), set(spec.get("future", []))
        runtimes = spec.get("runtimes", {})
        out = []
        for number in range(1, int(spec["episodes"]) + 1):
            if number in future:
                aired = _today(7 * (sorted(future).index(number) + 1))
            elif number in recent:
                aired = _today(-1)
            else:
                aired = _date(spec.get("air", f"{show['year']}-01-01"), 7 * (number - 1))
            out.append({"episode_number": number, "season_number": season,
                        "name": names.get(str(number), f"Episode {number}"), "air_date": aired,
                        "runtime": runtimes.get(str(number), spec.get("runtime", show.get("runtime", 45)))})
        return out

    def show(self, s):
        seasons = sorted(s.get("seasons", {}), key=int)
        episodes = [e for n in seasons for e in self.episodes(s, int(n))]
        aired = [e for e in episodes if e["air_date"] <= _today()]
        upcoming = [e for e in episodes if e["air_date"] > _today()]
        return {
            "id": s["id"], "name": s["name"], "original_name": s.get("original_name", s["name"]),
            "first_air_date": s.get("date") or f"{s['year']}-01-01", "episode_run_time": [s.get("runtime", 45)],
            "original_language": s.get("language", "en"), "overview": s.get("overview", ""),
            "genres": [{"id": n, "name": g} for n, g in enumerate(s.get("genres", []))], "poster_path": None,
            "status": "Returning Series" if upcoming else "Ended", "in_production": bool(upcoming),
            "number_of_seasons": len(seasons), "number_of_episodes": len(episodes),
            "seasons": [{"season_number": int(n), "episode_count": int(s["seasons"][n]["episodes"]),
                         "air_date": s["seasons"][n].get("air", f"{s['year']}-01-01"), "name": f"Season {n}"}
                        for n in seasons],
            "last_episode_to_air": aired[-1] if aired else None,
            "next_episode_to_air": upcoming[0] if upcoming else None,
        }

    def search(self, kind, query):
        items = self.movies.values() if kind == "movie" else self.shows.values() if kind == "tv" else []
        wanted = [w for w in _words(query) if w not in {"the", "a", "an", "of", "and"}]
        scored = []
        for item in items:
            names = [item.get("title") or item.get("name")] + [a["title"] for a in item.get("alt", [])]
            title_words = set(_words(" ".join(names)))
            hits = sum(w in title_words for w in wanted)
            if wanted and hits == len(wanted):
                score = 10 + hits
            elif hits:
                score = hits
            else:
                # Not TMDB behaviour: a descriptive query falls back to the
                # overview so a description can still find a title.
                score = 0.1 * sum(w in set(_words(item.get("overview", ""))) for w in wanted if len(w) > 3)
            if score > 0.15:
                scored.append((score, item))
        scored.sort(key=lambda pair: -pair[0])
        results = []
        for _, item in scored[:20]:
            card = self.movie(item) if kind == "movie" else self.show(item)
            results.append({k: card.get(k) for k in ("id", "title", "name", "original_language", "overview",
                                                    "release_date", "first_air_date", "poster_path") if k in card})
        return {"page": 1, "results": results, "total_results": len(results)}


NO_RESULTS = {"id": "0", "name": "No results returned", "info_hash": "0" * 40, "seeders": "0", "leechers": "0",
              "num_files": "0", "size": "0", "username": "", "added": "0", "category": "0"}


class Index:
    """The torrent index as a case describes it: rows [id, name, seeders, GB,
    files] and pack listings {id: [[file, MB], ...]}. Searches follow the
    index's own rule (every word in the name, -word excludes)."""

    def __init__(self, spec: dict, key: str):
        self.key = key
        self.rows = [self.row(r) for r in spec.get("rows", [])]
        self.listings = {str(k): [(n, int(float(mb) * 1e6)) for n, mb in v] for k, v in spec.get("listings", {}).items()}
        self.by_hash = {r["info_hash"]: r for r in self.rows}
        self.queries: list[str] = []
        self.peeks: list[str] = []

    def row(self, values):
        ident, name, seeders, gigabytes, files = values
        return {"id": str(ident), "name": name, "info_hash": self.hash_of(ident), "seeders": str(seeders),
                "leechers": "0", "num_files": str(files), "size": str(int(gigabytes * 1e9)), "username": "fixture",
                "added": "1760000000", "category": "208"}

    def hash_of(self, ident) -> str:
        return hashlib.sha1(f"{self.key}:{ident}".encode()).hexdigest()

    def search(self, query):
        self.queries.append(query)
        need, without = set(), set()
        for part in str(query).split():
            if part.startswith("-") and len(part) > 1:
                without.update(_words(part[1:]))
            else:
                need.update(_words(part))
        hits = [r for r in self.rows if need <= set(_words(r["name"])) and not without & set(_words(r["name"]))]
        return sorted(hits, key=lambda r: -int(r["seeders"]))[:100] or [dict(NO_RESULTS)]

    def files(self, ident) -> list[tuple[str, int]]:
        """A torrent's real file list (what the download app learns), listed or not."""
        ident = str(ident)
        if ident in self.listings:
            return list(self.listings[ident])
        row = next((r for r in self.rows if r["id"] == ident), None)
        if not row:
            return []
        return [(row["name"].replace(" ", ".") + ".mkv", int(row["size"]))]


# ─── The download app ─────────────────────────────────────────────────────


class Transfers:
    """One case's download app: what was added, which files each transfer
    would fetch, and every control. Nothing is ever downloaded."""

    def __init__(self, world):
        self.world = world
        self.items: dict[str, dict] = {}
        self.log: list[dict] = []

    def add(self, info_hash, path):
        row = self.world.index.by_hash.get(info_hash) if self.world.index else None
        files = self.world.index.files(row["id"]) if row else []
        self.items[info_hash] = {"hash": info_hash, "path": path, "id": row["id"] if row else "",
                                 "files": [{"name": n, "size": s} for n, s in files], "kept": None,
                                 "progress": 0.0, "status": "downloading", "removed": False}
        self.items[info_hash].update(self.world.transfer_state(info_hash) or {})
        self.log.append({"op": "add", "hash": info_hash, "id": row["id"] if row else ""})
        return info_hash

    def status(self, info_hash):
        from backend.models import DownloadStatus

        item = self.items.get(info_hash)
        if not item or item["removed"]:
            return None
        return {"hash": info_hash, "name": "", "status": DownloadStatus(item["status"]), "progress": item["progress"],
                "size_bytes": sum(f["size"] for f in item["files"]), "downloaded_bytes": 0, "download_speed": 0,
                "eta_seconds": -1, "save_path": item["path"], "upload_ratio": 0.0, "seeding_time": 0}

    def control(self, op, info_hash, **extra):
        self.log.append({"op": op, "hash": info_hash, **extra})
        item = self.items.get(info_hash)
        if item and op == "remove":
            item["removed"] = True
        if item and op == "skip":
            skipped = set(extra.get("indices") or [])
            item["kept"] = [f["name"] for n, f in enumerate(item["files"]) if n not in skipped]
        return True


class FakeTorrentManager:
    """Stands in for services.torrent_client.TorrentManager everywhere."""

    def __init__(self, config=None):
        self.world = CURRENT.get()

    @property
    def app(self):
        return self.world.transfers if self.world else None

    async def connect(self):
        return self.app is not None

    async def add_magnet(self, magnet_url, save_path):
        found = re.search(r"btih:([0-9a-fA-F]{40})", magnet_url)
        return self.app.add(found.group(1).lower(), save_path) if self.app and found else ""

    async def get_torrent_status(self, info_hash):
        return self.app.status(info_hash) if self.app else None

    async def get_files(self, info_hash):
        item = self.app.items.get(info_hash) if self.app else None
        return [dict(f, index=n) for n, f in enumerate(item["files"])] if item else []

    async def skip_files(self, info_hash, indices):
        return bool(self.app) and self.app.control("skip", info_hash, indices=list(indices))

    async def stop_torrent(self, info_hash):
        return bool(self.app) and self.app.control("stop", info_hash)

    async def start_torrent(self, info_hash):
        return bool(self.app) and self.app.control("start", info_hash)

    async def delete_torrent(self, info_hash, delete_files=False):
        return bool(self.app) and self.app.control("remove", info_hash, delete_files=delete_files)


async def _no_client_start(config):
    return False, "The fixture download app needs no starting."


# ─── Provider accounting ──────────────────────────────────────────────────


def usage_of(kind: str, data: dict) -> dict:
    """Tokens from a provider answer, as {input, cached, cache_write, output}
    (input counts every prompt token, cached ones included)."""
    usage = data.get("usage") or {}
    if kind == "glm":
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        return {"input": usage.get("prompt_tokens", 0) or 0, "cached": cached, "cache_write": 0,
                "output": usage.get("completion_tokens", 0) or 0}
    if kind == "altprovider":
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
        return {"input": usage.get("input_tokens", 0) or 0, "cached": cached, "cache_write": 0,
                "output": usage.get("output_tokens", 0) or 0}
    cached = usage.get("cache_read_input_tokens", 0) or 0
    written = usage.get("cache_creation_input_tokens", 0) or 0
    return {"input": (usage.get("input_tokens", 0) or 0) + cached + written, "cached": cached,
            "cache_write": written, "output": usage.get("output_tokens", 0) or 0}


def cost_of(model: str, tokens: dict) -> float:
    cost, _ = runtime_module.calculate_usage_cost(
        model, max(0, tokens["input"] - tokens["cached"] - tokens["cache_write"]), tokens["output"],
        tokens["cache_write"], tokens["cached"])
    return cost


# ─── The gate ─────────────────────────────────────────────────────────────


class Gate:
    """The one door to the network for the whole evaluation."""

    def __init__(self, harness):
        self.harness = harness
        self.violations: list[dict] = []  # outside any case

    def _refuse(self, world, where):
        record = {"target": where, "ts": time.time()}
        (world.violations if world else self.violations).append(record)
        raise NetworkRefused(f"The evaluation fixture refused a network request to {where}.")

    async def handle(self, transport, request):
        world = CURRENT.get()
        host = request.url.host
        if host in PROVIDERS:
            return await self.provider(transport, request, world, PROVIDERS[host])
        if world is not None:
            if host == "api.themoviedb.org":
                return world.tmdb_response(request)
            if host == "apibay.org":
                return world.index_response(request)
        self._refuse(world, host)

    def handle_sync(self, transport, request):
        self._refuse(CURRENT.get(), request.url.host)

    async def provider(self, transport, request, world, kind):
        harness = self.harness
        raw = await request.aread()
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if world is None:
            self._refuse(None, request.url.host)
        if harness.cap_reached():
            world.cap_refusals += 1
            raise SpendCapReached(f"The evaluation's ${harness.max_dollars:.2f} cap is spent.")
        number = world.provider_requests = world.provider_requests + 1
        await world.before_provider(number, body)
        outage = world.outage(number, body)
        started = time.monotonic()
        if outage:
            world.provider_log.append({"n": number, "model": body.get("model", ""), "outage": outage})
            return httpx.Response(outage, json={"error": {"message": "Fixture outage: the provider is unavailable."}},
                                  request=request)
        if harness.dry_run:
            data = harness.scripted.answer(kind, body, world)
            response = httpx.Response(200, json=data, request=request)
        else:
            response = await _ORIGINAL_ASYNC(transport, request)
            content = await response.aread()
            headers = {k: v for k, v in response.headers.items()
                       if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
            response = httpx.Response(response.status_code, headers=headers, content=content, request=request)
            try:
                data = response.json()
            except ValueError:
                data = {}
        latency = time.monotonic() - started
        tokens = usage_of(kind, data) if response.status_code == 200 else {"input": 0, "cached": 0, "cache_write": 0,
                                                                            "output": 0}
        requested = body.get("model", "")
        cost = cost_of(requested, tokens) if response.status_code == 200 else 0.0
        world.provider_log.append({
            "n": number, "provider": kind, "model": requested, "actual_model": data.get("model", ""),
            "status": response.status_code, "latency": round(latency, 3), "tokens": tokens, "cost": cost,
            "call": "agent" if body.get("tools") else "structured",
        })
        harness.charge(cost)
        await world.after_provider(number, body, data)
        return response


def _guard_sockets(harness):
    real_connect = socket.socket.connect

    def connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if sock.family in (socket.AF_INET, socket.AF_INET6) and host not in ("127.0.0.1", "::1", "localhost"):
            world = CURRENT.get()
            (world.violations if world else harness.gate.violations).append({"target": f"socket {host}"})
            raise ConnectionRefusedError(f"The dry run refuses network connections ({host}).")
        return real_connect(sock, address)

    return patch.object(socket.socket, "connect", connect)


# ─── Synthetic probes and declared sizes ──────────────────────────────────


def _file_version_wrapper(original):
    def file_version(path):
        version = original(path)
        found = marker(path)
        if found:
            version = {**version, "size_bytes": found[1]}
        return version

    return file_version


def _probe_wrapper(original):
    async def probe_file(path):
        found = marker(path)
        world = CURRENT.get()
        if not found:
            return await original(path)
        from backend.agents.node_executor import NodeError, file_version

        spec = (world.probes if world else {}).get(found[0])
        if not spec or spec.get("unreadable"):
            raise NodeError("This file does not contain playable video with a known duration.")
        return facts_for(path, spec, file_version(path))

    return probe_file


def _list_wrapper(original):
    async def dispatch(self, kind, args, operation_id):
        result = await original(self, kind, args, operation_id)
        if kind == "list" and isinstance(result, list):
            for row in result:
                try:
                    found = marker(self.roots[row["root_id"]] + "/" + row["path"])
                except (KeyError, TypeError):
                    found = None
                if found:
                    row["size_bytes"] = found[1]
                    row["version"] = {**row["version"], "size_bytes": found[1]}
        return result

    return dispatch


# ─── The harness ──────────────────────────────────────────────────────────


class Harness:
    """Shared state for one evaluation: the dollar cap, the provider (real or
    scripted) and the installed patches."""

    def __init__(self, *, dry_run: bool, max_dollars: float, scripted=None, per_run_dollars: float = 1.0):
        self.dry_run = dry_run
        self.max_dollars = float(max_dollars)
        self.per_run_dollars = float(per_run_dollars)
        self.spent = 0.0  # provider spend seen at the gate, all cases
        self.ledger_spent = 0.0  # the cost ledger's total over finished and running cases
        self.scripted = scripted
        self.gate = Gate(self)
        self.worlds: list = []

    def charge(self, dollars: float) -> None:
        self.spent += float(dollars or 0)

    def total(self) -> float:
        running = sum(w.ledger_cost() for w in self.worlds if not w.finished)
        return max(self.spent, self.ledger_spent + running)

    def cap_reached(self) -> bool:
        return self.total() >= self.max_dollars

    @contextlib.contextmanager
    def installed(self):
        from backend.agents import (hls_cache, media_state, node_executor, service, subtitle_audio,
                                    subtitle_investigation, tools)
        from backend.services import torrent_client

        gate = self.gate

        async def handle_async(transport, request):
            return await gate.handle(transport, request)

        def handle_sync(transport, request):
            return gate.handle_sync(transport, request)

        version = _file_version_wrapper(media_state.file_version)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", handle_async))
            stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", handle_sync))
            for module in (torrent_client, service, tools):
                stack.enter_context(patch.object(module, "TorrentManager", FakeTorrentManager))
            for module in (service, tools):
                stack.enter_context(patch.object(module, "start_configured_client", _no_client_start))
            for module in (media_state, node_executor, tools, hls_cache, subtitle_audio, subtitle_investigation):
                stack.enter_context(patch.object(module, "file_version", version))
            stack.enter_context(patch.object(node_executor, "probe_file", _probe_wrapper(node_executor.probe_file)))
            stack.enter_context(patch.object(node_executor.Executor, "_dispatch",
                                             _list_wrapper(node_executor.Executor._dispatch)))
            if self.dry_run:
                stack.enter_context(_guard_sockets(self))
            yield self


# ─── Reading what happened ────────────────────────────────────────────────


def attempts_of(sessions) -> list[dict]:
    """Every tool call a model made, refused ones included, with its result."""
    out = []
    for session in sessions:
        pending = {}
        for message in session.messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    attempt = {"session": session.id, "agent": session.agent.value, "id": block.get("id"),
                               "tool": block.get("name"), "args": block.get("input") or {}, "result": None,
                               "error": False}
                    out.append(attempt)
                    pending[block.get("id")] = attempt
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in pending:
                    attempt = pending.pop(block["tool_use_id"])
                    content_text = block.get("content")
                    if isinstance(content_text, list):
                        content_text = " ".join(str(b.get("text", "")) for b in content_text if isinstance(b, dict))
                    attempt.update(result=str(content_text or ""), error=bool(block.get("is_error")))
    return out


def ledger_rows(store) -> tuple[list[dict], list[dict]]:
    from backend.agents import ledger

    with ledger._db(store) as db:
        calls = [dict(r) for r in db.execute("SELECT * FROM ledger_calls ORDER BY ts")]
        rules = [dict(r) for r in db.execute("SELECT * FROM ledger_enforcements ORDER BY ts")]
    return calls, rules


class World:
    """One run of one case: an isolated Sparrow with fixture boundaries.

    Role worlds (live_roles.py) build on it: build() makes the household,
    act() triggers the agent (and any scenario steps), observe() and judge()
    read the outcome."""

    role = ""
    timeout = 600.0

    def __init__(self, case: dict, run: int, harness: Harness):
        self.case, self.run_no, self.harness = case, run, harness
        self.fixture = case.get("fixture") or {}
        self.expect = case.get("expect") or {}
        self.catalogue = Catalogue(self.fixture.get("catalogue") or {})
        self.index = Index(self.fixture.get("index") or {}, case["id"])
        self.transfers = Transfers(self)
        self.probes: dict[str, dict] = {}
        self.provider_log: list[dict] = []
        self.provider_requests = 0
        self.cap_refusals = 0
        self.violations: list[dict] = []
        self.events: list[dict] = []
        self.tasks: set = set()
        self.marks: dict = {}  # scenario bookkeeping (e.g. the request count at a cancel)
        self.finished = False
        self.service = None
        self.tmp = None

    # Boundaries a role or case may refine.
    def tmdb_response(self, request):
        path = request.url.path
        path = path[2:] if path.startswith("/3/") else path
        params = dict(urllib.parse.parse_qsl(request.url.query.decode() if isinstance(request.url.query, bytes)
                                             else request.url.query))
        params.pop("api_key", None)
        answer = self.catalogue.answer(path, params)
        if answer is None:
            return httpx.Response(404, json={"status_code": 34, "status_message": "The resource you requested could not be found."},
                                  request=request)
        return httpx.Response(200, json=copy.deepcopy(answer), request=request)

    def index_response(self, request):
        params = dict(urllib.parse.parse_qsl(request.url.query.decode() if isinstance(request.url.query, bytes)
                                             else request.url.query))
        if request.url.path.endswith("/q.php"):
            return httpx.Response(200, json=self.index.search(params.get("q", "")), request=request)
        if request.url.path.endswith("/f.php"):
            self.index.peeks.append(params.get("id", ""))
            listing = self.index.listings.get(str(params.get("id", "")))
            if not listing:
                return httpx.Response(200, json=[{"name": ["Filelist not found"], "size": [0]}], request=request)
            return httpx.Response(200, json=[{"name": [n], "size": [s]} for n, s in listing], request=request)
        return httpx.Response(404, json={}, request=request)

    def transfer_state(self, info_hash) -> dict | None:
        return None

    def outage(self, number, body) -> int:
        """An HTTP status to answer instead of the provider (a scripted outage)."""
        plan = self.fixture.get("outage") or {}
        if plan.get("all") or number <= int(plan.get("first", 0)):
            return int(plan.get("status", 503))
        return 0

    async def before_provider(self, number, body):
        return None

    async def after_provider(self, number, body, data):
        return None

    # Lifecycle.
    async def build(self):
        import os
        import tempfile

        from backend.agents.service import AgentService
        from backend.models import SparrowConfig, TorrentClientConfig, TorrentClientType
        from backend.storage import Storage

        self.tmp = tempfile.TemporaryDirectory(prefix="sparrow-live-")
        self.root = Path(self.tmp.name)
        self.library, self.staging = self.root / "library", self.root / "staging"
        self.library.mkdir()
        self.staging.mkdir()
        self.state_dir = self.root / "state"
        self.storage = Storage(str(self.state_dir))
        await self.storage.load_all()
        # Provider keys stay in the environment; nothing secret is saved in the state.
        await self.storage.save_config(SparrowConfig(
            library_dir=str(self.library), staging_dir=str(self.staging), tmdb_api_key="fixture-tmdb-key",
            torrent_client=TorrentClientConfig(type=TorrentClientType.QBITTORRENT), max_active_transfers=10,
            onboarding_complete=True,
        ))
        self.service = AgentService(self.storage, str(self.state_dir), _quiet)
        self.store = self.service.store
        # Keys come from the environment (never the run's state); a run on
        # AltProvider or GLM models needs no Provider key.
        self.service.runtime._api_key_getter = lambda: os.getenv("PROVIDER_API_KEY", "")
        accounts = self.service.accounts
        settings = accounts.server_settings()
        accounts.set_defaults(settings["defaults"], {**settings["policy"],
                                                     "max_agent_dollars": self.harness.per_run_dollars})
        self.owner = accounts.create_user("eval-owner", "eval-" + uuid.uuid4().hex, "Owner", bootstrap=True)
        self.wrap_emit()

    def wrap_emit(self):
        """Record every event, and route only those this case lets through."""
        real_emit = self.service.emit

        async def emit(event):
            routed = self.route(event)
            self.events.append({"kind": event.kind, "job_id": event.job_id, "download_id": event.download_id,
                                "session_id": event.session_id, "routed": routed})
            if routed:
                await real_emit(event)

        self.service.emit = emit
        self.service.toolbox.emit = emit

    def route(self, event) -> bool:
        return True

    async def restart(self):
        """A new server process on the same state (the old one shut down)."""
        from backend.agents.service import AgentService

        await self.service.shutdown()
        getter = self.service.runtime._api_key_getter
        self.service = AgentService(self.storage, str(self.state_dir), _quiet)
        self.store = self.service.store
        self.service.runtime._api_key_getter = getter
        self.wrap_emit()

    def policy(self, transcript):
        """The reference agent for dry runs (live_roles)."""
        return "No reference policy for this case."

    def structured(self, system, prompt, schema):
        """A dry run's answer to a structured (single-turn) model call."""
        return default_for(schema)

    async def act(self):
        raise NotImplementedError

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        return task

    def busy(self) -> bool:
        tasks = [*self.tasks, *(self.service._wake_tasks if self.service else ())]
        tasks += self.role_tasks()
        if any(not t.done() for t in tasks):
            return True
        if self.service and any(lock.locked() for lock in self.service.runtime._locks.values()):
            return True
        return False

    def role_tasks(self) -> list:
        return []

    async def settle(self, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (timeout or self.timeout)
        quiet = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            quiet = 0 if self.busy() else quiet + 1
            if quiet >= 4:
                return True
        return False

    async def finalize(self):
        """Settle anything the outcome depends on before it is read."""
        return None

    def sessions(self):
        return self.store.get_sessions()

    def ledger_cost(self) -> float:
        if not self.service:
            return 0.0
        try:
            calls, _ = ledger_rows(self.store)
        except Exception:
            return 0.0
        return sum(float(c.get("cost") or 0) for c in calls)

    def observe(self) -> dict:
        sessions = self.sessions()
        calls, rules = ledger_rows(self.store)
        return {
            "sessions": sessions, "attempts": attempts_of(sessions), "calls": calls, "rules": rules,
            "jobs": self.store.get_jobs(), "downloads": self.storage.get_all_downloads(),
        }

    def judge(self, seen: dict) -> list[dict]:
        return []

    async def close(self):
        for task in [*self.tasks, *(self.service._wake_tasks if self.service else ()), *self.role_tasks()]:
            task.cancel()
        await asyncio.gather(*self.tasks, *(self.service._wake_tasks if self.service else ()), *self.role_tasks(),
                             return_exceptions=True)
        if self.service:
            with contextlib.suppress(Exception):
                await self.service.shutdown()
        await self.role_close()

    async def role_close(self):
        return None

    def cleanup(self, keep: bool = False):
        if self.tmp and not keep:
            self.tmp.cleanup()


async def _quiet(*_args, **_kwargs):
    return None


def check(name: str, passed: bool, detail: str = "") -> dict:
    return {"contract": name, "passed": bool(passed), "detail": detail}


# ─── Dry-run usage estimates ──────────────────────────────────────────────

# Reasoning tokens a dry run assumes per call: reasoning models bill them as
# output. "" is a model's default effort (nothing sent).
REASONING = {"": 400, "none": 0, "minimal": 100, "low": 250, "medium": 800, "high": 2500, "xhigh": 5000, "max": 8000}


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


# ─── The scripted provider (dry runs) ─────────────────────────────────────


class Transcript:
    """A provider request read back as the conversation so far: the tool
    calls made with their results, the tools on offer and the user text."""

    def __init__(self):
        self.calls: list[dict] = []
        self.tools: set[str] = set()
        self.texts: list[str] = []
        self.system = ""

    @classmethod
    def from_altprovider(cls, body):
        t = cls()
        t.system = body.get("instructions") or ""
        t.tools = {tool.get("name") for tool in body.get("tools") or []}
        by_id = {}
        for item in body.get("input") or []:
            kind = item.get("type")
            if kind == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except ValueError:
                    args = {}
                entry = {"id": item.get("call_id"), "name": item.get("name"), "args": args, "output": None,
                         "error": False}
                t.calls.append(entry)
                by_id[entry["id"]] = entry
            elif kind == "function_call_output":
                entry = by_id.get(item.get("call_id"))
                if entry is not None:
                    output = str(item.get("output") or "")
                    entry["error"] = output.startswith("Error: ")
                    entry["output"] = output[7:] if entry["error"] else output
            elif item.get("role") == "user":
                t.texts.append(str(item.get("content") or ""))
        return t

    @classmethod
    def from_provider(cls, body):
        t = cls()
        system = body.get("system") or ""
        t.system = system if isinstance(system, str) else " ".join(b.get("text", "") for b in system)
        t.tools = {tool.get("name") for tool in body.get("tools") or []}
        by_id = {}
        for message in body.get("messages") or []:
            content = message.get("content")
            if isinstance(content, str):
                if message.get("role") == "user":
                    t.texts.append(content)
                continue
            for block in content or []:
                if block.get("type") == "tool_use":
                    entry = {"id": block.get("id"), "name": block.get("name"), "args": block.get("input") or {},
                             "output": None, "error": False}
                    t.calls.append(entry)
                    by_id[entry["id"]] = entry
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                    output = block.get("content")
                    if isinstance(output, list):
                        output = " ".join(str(b.get("text", "")) for b in output if isinstance(b, dict))
                    by_id[block["tool_use_id"]].update(output=str(output or ""), error=bool(block.get("is_error")))
                elif block.get("type") == "text" and message.get("role") == "user":
                    t.texts.append(block.get("text", ""))
        return t

    @classmethod
    def from_glm(cls, body):
        t = cls()
        t.tools = {(tool.get("function") or {}).get("name") for tool in body.get("tools") or []}
        by_id = {}
        for message in body.get("messages") or []:
            role, content = message.get("role"), message.get("content")
            if role == "system":
                t.system = str(content or "")
            elif role == "assistant":
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    try:
                        args = json.loads(function.get("arguments") or "{}")
                    except (TypeError, ValueError):
                        args = {}
                    entry = {"id": call.get("id"), "name": function.get("name"), "args": args, "output": None,
                             "error": False}
                    t.calls.append(entry)
                    by_id[entry["id"]] = entry
            elif role == "tool" and message.get("tool_call_id") in by_id:
                output = str(content or "")
                error = output.startswith("Error: ")
                by_id[message["tool_call_id"]].update(output=output[7:] if error else output, error=error)
            elif role == "user":
                t.texts.append(content if isinstance(content, str) else
                               " ".join(str(b.get("text", "")) for b in content or [] if isinstance(b, dict)))
        return t

    def called(self, name) -> list[dict]:
        return [c for c in self.calls if c["name"] == name]

    def last(self, name=None):
        found = [c for c in self.calls if name is None or c["name"] == name]
        return found[-1] if found else None

    @property
    def wake(self) -> str:
        """The latest wake message (the events delivered to this turn)."""
        return self.texts[-1] if self.texts else ""


def call(name, **args) -> dict:
    return {"name": name, "args": args}


def default_for(schema):
    """A minimal answer that satisfies a JSON schema."""
    kind = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object":
        return {k: default_for(v) for k, v in (schema.get("properties") or {}).items()
                if k in (schema.get("required") or [])}
    return {"array": [], "string": "", "boolean": True, "integer": 0, "number": 0}.get(kind)


class ScriptedProvider:
    """Answers model requests from each case's reference policy, in the
    provider's own wire format, with usage estimated from the real request
    (prompt tokens ~ characters/4, prompt caching per conversation, and a
    reasoning allowance by effort; see REASONING)."""

    def __init__(self):
        self.previous: dict[str, int] = {}

    def answer(self, kind, body, world):
        if kind == "glm":
            return self._glm_answer(body, world)
        if kind == "altprovider":
            if body.get("tools"):
                output = self._altprovider_calls(world.policy(Transcript.from_altprovider(body)))
            else:
                system, prompt = "", ""
                for item in body.get("input") or []:
                    if item.get("role") == "system":
                        system = str(item.get("content"))
                    elif item.get("role") == "user":
                        prompt = str(item.get("content"))
                schema = ((body.get("text") or {}).get("format") or {}).get("schema") or {}
                text = json.dumps(world.structured(system, prompt, schema))
                output = [{"type": "message", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": text, "annotations": []}]}]
            effort = (body.get("reasoning") or {}).get("effort", "")
            used, cached, produced = self._usage(body, output, effort,
                                                 body.get("prompt_cache_key") or str(body.get("input", ""))[:200])
            return {"id": "resp_dry_" + uuid.uuid4().hex[:12], "object": "response", "status": "completed",
                    "model": body.get("model", ""), "output": output,
                    "usage": {"input_tokens": used, "input_tokens_details": {"cached_tokens": cached},
                              "output_tokens": produced, "total_tokens": used + produced}}
        if body.get("tools"):
            content = self._provider_calls(world.policy(Transcript.from_provider(body)))
        else:
            system = body.get("system")
            system = system if isinstance(system, str) else " ".join(b.get("text", "") for b in system or [])
            messages = body.get("messages") or [{}]
            schema = (((body.get("output_config") or {}).get("format")) or {}).get("schema") or {}
            content = [{"type": "text", "text": json.dumps(world.structured(system, str(messages[-1].get("content", "")),
                                                                            schema))}]
        effort = (body.get("output_config") or {}).get("effort", "none")
        used, cached, produced = self._usage(body, content, effort, json.dumps(body.get("system", ""))[:200])
        return {"id": "msg_dry_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant",
                "model": body.get("model", ""), "content": content,
                "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": used - cached, "output_tokens": produced, "cache_read_input_tokens": cached,
                          "cache_creation_input_tokens": 0}}

    def _glm_answer(self, body, world):
        message = {"role": "assistant", "content": ""}
        if body.get("tools"):
            decision = world.policy(Transcript.from_glm(body))
            if isinstance(decision, str):
                message["content"] = decision
            else:
                message["tool_calls"] = [
                    {"id": "call_" + uuid.uuid4().hex[:16], "type": "function", "index": i,
                     "function": {"name": d["name"], "arguments": json.dumps(d.get("args") or {})}}
                    for i, d in enumerate(self._decisions(decision))]
        else:
            messages = body.get("messages") or [{}]
            system = str(messages[0].get("content", "")) if messages[0].get("role") == "system" else ""
            system, _, schema_text = system.partition("It must match this JSON Schema:\n")
            try:
                schema = json.loads(schema_text) if schema_text else {}
            except ValueError:
                schema = {}
            prompt = messages[-1].get("content", "")
            prompt = prompt if isinstance(prompt, str) else " ".join(
                str(b.get("text", "")) for b in prompt if isinstance(b, dict))
            message["content"] = json.dumps(world.structured(system, prompt, schema))
        used, cached, produced = self._usage(body, message, body.get("reasoning_effort", "low"),
                                             json.dumps(body.get("messages", [{}])[:1])[:200])
        return {"id": "glm_dry_" + uuid.uuid4().hex[:12], "object": "chat.completion", "model": body.get("model", ""),
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": used, "prompt_tokens_details": {"cached_tokens": cached},
                          "completion_tokens": produced, "total_tokens": used + produced}}

    @staticmethod
    def _decisions(decision):
        if isinstance(decision, dict):
            return [decision]
        return list(decision or [])

    def _altprovider_calls(self, decision):
        if isinstance(decision, str):
            return [{"type": "message", "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": decision, "annotations": []}]}]
        return [{"type": "function_call", "id": "fc_" + uuid.uuid4().hex[:12], "call_id": "call_" + uuid.uuid4().hex[:16],
                 "name": d["name"], "arguments": json.dumps(d.get("args") or {}), "status": "completed"}
                for d in self._decisions(decision)]

    def _provider_calls(self, decision):
        if isinstance(decision, str):
            return [{"type": "text", "text": decision}]
        return [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:20], "name": d["name"],
                 "input": d.get("args") or {}} for d in self._decisions(decision)]

    def _usage(self, body, output, effort, cache_key):
        prompt = estimate_tokens(json.dumps({k: v for k, v in body.items() if k != "model"}, ensure_ascii=False))
        previous = self.previous.get(cache_key, 0)
        # Providers cache a repeated prefix of at least 1,024 tokens, in 128-token steps.
        cached = (min(previous, prompt) // 128) * 128 if previous >= 1024 else 0
        self.previous[cache_key] = prompt
        produced = estimate_tokens(json.dumps(output, ensure_ascii=False)) + REASONING.get(effort or "", 400)
        return prompt, cached, produced
