"""One real title through Sparrow's subtitle pipeline, for comparing model setups.

The real video, the real speech recogniser and the real page checkers and
manager on whichever models the environment names; an isolated household in a
new state directory (never the installed server). The speech evidence is
built once and reused (--evidence-from), so two setups differ only in models.

    # 1. Verify a human-made track (it must be approved, never edited):
    SPARROW_FFMPEG=... SPARROW_FFPROBE=... SPARROW_TRANSCRIPTION_MODEL=.../whisper-medium \\
      .venv/bin/python tests/evals/title_subtitles.py --video FILM.mp4 --title "Four Lions" --year 2010 \\
      --track candidate.srt --state data/glm-bench/fl-verify-glm --output result.json
    # 2. No track: Sparrow writes one from the dialogue (compared with --reference):
    ... --state data/glm-bench/fl-write-glm --evidence-from data/glm-bench/fl-verify-glm --reference candidate.srt

Models come from the usual variables (SPARROW_SUBTITLE_MODEL, _CONTRACTOR,
_VERIFIER, _EFFORT) and keys from the environment. The online subtitle archive
is off unless --archive, so every setup sees the same sources.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DONE = ("ready", "needs_attention", "failed", "review_pending", "cancelled")


def cues(text):
    """(start, end, text) from SRT or WebVTT text."""
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = [x for x in block.strip().splitlines() if x.strip()]
        for i, line in enumerate(lines):
            m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", line)
            if not m:
                continue
            g = [int(x) for x in m.groups()]
            start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
            end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
            out.append((start, end, " ".join(lines[i + 1:])))
            break
    return out


def words(text):
    return re.findall(r"[a-z0-9']+", re.sub(r"<[^>]+>|\{[^}]*\}|\([^)]*\)|\[[^]]*\]", " ", text.lower()))


def compare(track, reference):
    """How a written track lines up with a human reference: the share of the
    reference's captions overlapped in time, and word recall/precision over
    the overlapping text (a proxy, not a quality verdict)."""
    if not track or not reference:
        return {}
    covered, hits, total_ref, total_new = 0, 0, 0, 0
    new_words_by_cue = [(s, e, words(t)) for s, e, t in track]
    for s, e, t in reference:
        near = [w for (a, b, w) in new_words_by_cue if a < e + 0.5 and b > s - 0.5]
        if near:
            covered += 1
        ref = words(t)
        pool = [x for w in near for x in w]
        total_ref += len(ref)
        hits += sum(1 for x in ref if x in pool)
    for a, b, w in new_words_by_cue:
        total_new += len(w)
    matched_new = sum(1 for a, b, w in new_words_by_cue for x in w
                      if any(x in words(t) for s, e, t in reference if s < b + 0.5 and e > a - 0.5))
    return {"reference_captions": len(reference), "captions": len(track),
            "reference_captions_overlapped": round(covered / len(reference), 3),
            "word_recall": round(hits / max(1, total_ref), 3), "word_precision": round(matched_new / max(1, total_new), 3)}


async def build(args, state):
    from fastapi import FastAPI
    import httpx

    from backend.agents import node_executor
    from backend.agents.account_api import COOKIE, install_accounts
    from backend.agents.catalogue import Catalogue
    from backend.agents.nodes import Nodes
    from backend.agents.playback import install_playback
    from backend.agents.service import AgentService
    from backend.agents.subtitles import install_subtitles
    from backend.models import LibraryItem, MediaType, SparrowConfig
    from backend.storage import Storage

    video = Path(args.video).resolve()
    storage = Storage(str(state))
    await storage.load_all()
    (state / "staging").mkdir(parents=True, exist_ok=True)
    await storage.save_config(SparrowConfig(library_dir=str(video.parent), staging_dir=str(state / "staging"),
                                            onboarding_complete=True))
    service = AgentService(storage, str(state), AsyncMock())
    service.runtime._api_key_getter = lambda: os.getenv("ZAI_API_KEY", "")
    accounts = service.accounts
    if accounts.has_users():
        raise SystemExit(f"{state} already holds a household; use a new --state for each run.")
    app = FastAPI()
    accounts_api = install_accounts(app, storage, lambda: service)
    owner = accounts_api.create_user("bench-owner", "bench-" + os.urandom(12).hex(), "Owner", bootstrap=True)
    nodes = Nodes(storage, lambda: service)
    catalogue = Catalogue(storage, nodes)
    install_playback(app, storage, accounts_api, nodes, catalogue)
    facts = await node_executor.probe_file(video)
    await storage.add_library_item(LibraryItem(id="title", title=args.title, year=args.year,
                                               media_type=MediaType.MOVIE, path=str(video)))
    asset = catalogue.save_asset("title", "local", "library", video.name, facts)
    subtitles = install_subtitles(app, accounts_api, nodes, catalogue, lambda: service)
    subtitles.attach(service)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                               headers={"X-Sparrow-Request": "1"},
                               cookies={COOKIE: accounts_api.new_session(owner["id"])}, timeout=60)
    return {"service": service, "subtitles": subtitles, "client": client, "asset": asset, "facts": facts,
            "nodes": nodes}


def evidence_dir(state):
    return state / "local-node" / "cache" / "subtitle-evidence"


def seed_evidence(source, state):
    """Reuse speech evidence built for the same file by an earlier run."""
    found = evidence_dir(Path(source))
    if not found.is_dir():
        raise SystemExit(f"No speech evidence under {found}.")
    target = evidence_dir(state)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(found, target, dirs_exist_ok=True)


def spend_by_phase(service):
    """Model calls in the cost ledger, by phase: calls, dollars, models, tokens."""
    with service.store._connect() as db:
        rows = db.execute("SELECT phase, model, cost, input_tokens, cached_tokens, output_tokens, latency, error "
                          "FROM ledger_calls").fetchall()
    phases = {}
    for phase, model, cost, fresh, cached, output, latency, error in rows:
        entry = phases.setdefault(phase or "?", {"calls": 0, "errors": 0, "dollars": 0.0, "models": set(),
                                                 "input": 0, "cached": 0, "output": 0, "seconds": 0.0})
        entry["calls"] += 1
        entry["errors"] += 1 if error else 0
        entry["dollars"] += float(cost or 0)
        entry["models"].add(model or "")
        entry["input"] += int(fresh or 0)
        entry["cached"] += int(cached or 0)
        entry["output"] += int(output or 0)
        entry["seconds"] += float(latency or 0)
    for entry in phases.values():
        entry["models"] = sorted(m for m in entry["models"] if m)
        entry["dollars"] = round(entry["dollars"], 4)
        entry["seconds"] = round(entry["seconds"], 1)
    return phases


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--year", type=int)
    parser.add_argument("--track", help="a subtitle file to verify (SRT); none: Sparrow writes one")
    parser.add_argument("--reference", help="a human track to compare a written track with")
    parser.add_argument("--language", default="en")
    parser.add_argument("--state", required=True, help="a new state directory")
    parser.add_argument("--evidence-from", help="an earlier run's state whose speech evidence to reuse")
    parser.add_argument("--archive", action="store_true", help="allow the online subtitle archive")
    parser.add_argument("--hours", type=float, default=3.0, help="stop waiting after this long")
    parser.add_argument("--output", help="write the result JSON here")
    args = parser.parse_args(argv)
    if not args.archive:
        os.environ["SPARROW_SUBTITLE_ARCHIVE"] = "off"
    state = Path(args.state).resolve()
    if state.exists() and any(state.iterdir()):
        raise SystemExit(f"{state} is not empty; use a new --state for each run.")
    state.mkdir(parents=True, exist_ok=True)
    if args.evidence_from:
        seed_evidence(Path(args.evidence_from).resolve(), state)

    from backend.agents import profiles, subtitles as subtitles_module

    world = await build(args, state)
    client, subtitles = world["client"], world["subtitles"]
    audio = [t for t in world["facts"].get("audio_tracks", [])]
    body = {"audio_index": audio[0]["index"], "verify": True, "language": args.language}
    if args.track:
        body["text"] = Path(args.track).read_text(errors="replace")
    started = time.time()
    response = await client.post(f"/api/v1/assets/{world['asset']}/subtitles/repair", json=body)
    if response.status_code != 200:
        raise SystemExit(f"Subtitles were not queued: HTTP {response.status_code} {response.text[:300]}")
    task_id = response.json()["id"]
    last = ""
    while time.time() - started < args.hours * 3600:
        task = subtitles.task(task_id)
        line = f"{task['state']}: {task['data'].get('message', '')}"
        if line != last:
            print(f"[{round(time.time() - started):>5}s] {line}", flush=True)
            last = line
        if task["state"] in DONE:
            break
        await asyncio.sleep(5)
    elapsed = round(time.time() - started, 1)
    task = subtitles.task(task_id)
    data = task["data"]
    track = subtitles.track(data.get("track_id")) if data.get("track_id") else None
    final_text = ""
    if track and track["state"] == "ready":
        got = await client.get(f"/api/v1/subtitles/tracks/{track['id']}.vtt")
        final_text = got.text if got.status_code == 200 else ""
    spend = spend_by_phase(world["service"])
    sessions = world["service"].store.get_sessions()
    track_data = (track or {}).get("data") or {}
    reference = cues(Path(args.reference).read_text(errors="replace")) if args.reference else (
        cues(body["text"]) if args.track else [])
    result = {
        "title": args.title, "scenario": "verify" if args.track else "write", "state": task["state"],
        "message": data.get("message", ""), "seconds": elapsed,
        "models": {p.id: {"model": p.model, "effort_sent": p.effort_sent} for p in profiles.all_profiles()
                   if p.kind == "subtitle"},
        "picked": {"manager": subtitles_module.review_model(), "checker": subtitles_module.contractor_model(),
                   "verifier": subtitles_module.verifier_model()},
        "track": {"source": track_data.get("source_id"), "state": (track or {}).get("state"),
                  "approved": bool((track_data.get("review") or {}).get("approved")),
                  "review": track_data.get("review"), "timing": track_data.get("timing"),
                  "correction": track_data.get("correction")},
        "attempts": data.get("attempts", []),
        "review_outcome": data.get("review_outcome"),
        "spend": spend, "dollars": round(sum(p["dollars"] for p in spend.values()), 4),
        "session_dollars": round(sum(s.spend.dollars for s in sessions), 4),
        "comparison": compare(cues(final_text), reference) if final_text and reference else {},
        "evidence_reused": bool(args.evidence_from),
    }
    if final_text:
        (state / "final.vtt").write_text(final_text)
    text = json.dumps(result, indent=1, default=str)
    print(text)
    if args.output:
        Path(args.output).write_text(text)
    await subtitles.stop()
    await client.aclose()
    await world["service"].shutdown()


if __name__ == "__main__":
    asyncio.run(main())
