"""Live role evaluation: the 30 contract cases and an unseen holdout, run
through the real agents, runtime, tools and ledger with a real model, while
TMDB, the torrent index, the download app and file probes are fixtures.

    # free: scripted model answers, the network refused (tests the harness)
    .venv/bin/python tests/evals/run_live.py --dry-run

    # paid: real model calls, capped
    SPARROW_RUN_LIVE_EVALS=1 ZAI_API_KEY=... SPARROW_CHEAP_MODEL=glm-5.3-flash \\
      SPARROW_SMART_MODEL=glm-5.3 .venv/bin/python tests/evals/run_live.py --max-dollars 15

Each case runs --runs times (default 3) on a fresh household. Acceptance
judges outcomes (requests, transfers, recorded copies, subtitle tracks and
every attempted tool call), never the model's prose. New runs stop being
scheduled once the cost ledger (or the provider meter at the network gate)
reaches --max-dollars; no paid request is sent after that, and runs still in
flight are cancelled. The summary names every run that did not happen.

Writes a JSON result (no keys, no release names, no agent prose) and a
markdown summary per role.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from evals import live_harness as harness_module  # noqa: E402
from evals.live_harness import CURRENT, Harness, ScriptedProvider, check, ledger_rows  # noqa: E402

HERE = Path(__file__).resolve().parent
MANIFESTS = {"main": HERE / "live-v1.json", "holdout": HERE / "live-holdout-v1.json"}
ARTIFACTS = HERE / "artifacts"
VERSION = 1
DRY_MODELS = {"SPARROW_CHEAP_MODEL": "glm-5.3-flash", "SPARROW_SMART_MODEL": "glm-5.3"}
DRY_KEYS = {"ZAI_API_KEY": "dry-run-not-a-key"}
# Hermetic for every run: no online subtitle archive (it would be refused).
HERMETIC = {"SPARROW_SUBTITLE_ARCHIVE": "off"}
STATUSES = ("passed", "failed", "error", "timeout", "cancelled_at_cap", "not_run", "skipped")


# ─── Cases ────────────────────────────────────────────────────────────────


def load_cases(sets=("main", "holdout"), manifests=None) -> list[dict]:
    """Every case of the chosen sets, validated, each tagged with its set and
    manifest version."""
    from evals.live_roles import ROLE_WORLDS, ROLES

    manifests = manifests or MANIFESTS
    cases, seen = [], set()
    for name in sets:
        data = json.loads(Path(manifests[name]).read_text())
        shared = data.get("catalogues") or {}
        for case in data["cases"]:
            fixture = case.get("fixture") or {}
            if isinstance(fixture.get("catalogue"), str):
                # A catalogue shared by several cases, named in the manifest.
                case = {**case, "fixture": {**fixture, "catalogue": json.loads(json.dumps(shared[fixture["catalogue"]]))}}
            missing = {"id", "role", "kind", "contract", "description", "fixture", "expect"} - set(case)
            if missing:
                raise ValueError(f"{case.get('id', '?')}: missing {sorted(missing)}")
            if case["role"] not in ROLES or case["role"] not in ROLE_WORLDS:
                raise ValueError(f"{case['id']}: unknown role {case['role']}")
            if case["kind"] not in ("ordinary", "adversarial"):
                raise ValueError(f"{case['id']}: kind must be ordinary or adversarial")
            if case["id"] in seen:
                raise ValueError(f"duplicate case id {case['id']}")
            seen.add(case["id"])
            cases.append({**case, "set": name, "manifest": data["version"]})
    return cases


def manifest_digests(manifests=None) -> dict:
    manifests = manifests or MANIFESTS
    return {name: {"version": json.loads(Path(p).read_text())["version"],
                   "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()} for name, p in manifests.items()}


def media_tools() -> bool:
    from backend.agents.node_executor import executable

    return bool(executable("ffmpeg") and executable("ffprobe"))


# ─── One run ──────────────────────────────────────────────────────────────


class Scrubber:
    """Keeps release names, fixture file names and keys out of results."""

    def __init__(self, cases):
        names = set()
        for case in cases:
            fixture = case.get("fixture") or {}
            index = fixture.get("index") or {}
            names.update(r[1] for r in index.get("rows", []))
            for listing in (index.get("listings") or {}).values():
                names.update(n for n, _ in listing)
            names.update(item["name"] for item in fixture.get("files", []))
            names.update(copy["path"] for copy in fixture.get("library", []))
            names.add(fixture.get("download_name") or "")
        words = set()
        for name in names:
            # Whole names, and each folder and file name inside them.
            words.add(name)
            words.update(part for part in name.split("/") if not part.startswith(("Season ", "Movies")))
        words.update(v for k, v in os.environ.items() if k.endswith("_API_KEY"))
        self.words = sorted((w for w in words if len(w) > 8), key=len, reverse=True)

    def __call__(self, text) -> str:
        text = str(text)
        for word in self.words:
            if word in text:
                text = text.replace(word, "<release>")
        return text[:300]


def run_record(world, case, run, status, checks, started, error="", scrub=str) -> dict:
    calls, rules = ([], [])
    attempts = []
    try:
        calls, rules = ledger_rows(world.store)
        attempts = harness_module.attempts_of(world.sessions())
    except Exception:
        pass
    log = [entry for entry in world.provider_log if "tokens" in entry]
    tokens = {k: sum(e["tokens"][k] for e in log) for k in ("input", "cached", "output")}
    phases = {}
    for row in calls:
        phases[row["phase"]] = phases.get(row["phase"], 0) + 1
    profiles = sorted({(r.get("profile") or "", r.get("profile_version") or "", r.get("effort") or "", r.get("model") or "")
                       for r in calls if r.get("profile")})
    failed = [c["contract"] for c in checks if not c["passed"]]
    if status == "passed" and failed:
        status = "failed"
    return {
        "case": case["id"], "set": case["set"], "role": case["role"], "kind": case["kind"], "run": run,
        "status": status, "passed": status == "passed" if status in ("passed", "failed", "error", "timeout") else None,
        "checks": [{**c, "detail": scrub(c["detail"])} for c in checks], "failed": failed,
        "latency_seconds": round(time.monotonic() - started, 2),
        "model_latency_seconds": round(sum(e.get("latency", 0) for e in log), 2),
        "calls": {"provider": len(log), "outage_answers": sum(1 for e in world.provider_log if e.get("outage")),
                  "cap_refused": world.cap_refusals, "ledger_by_phase": phases},
        "tokens": tokens,
        "cost": {"ledger": round(sum(float(r.get("cost") or 0) for r in calls), 6),
                 "provider_meter": round(sum(e["cost"] for e in log), 6)},
        "models": {"requested": sorted({e["model"] for e in log}), "actual": sorted({e["actual_model"] for e in log if e["actual_model"]})},
        "profiles": [{"id": p, "version": v, "effort_sent": e, "model": m} for p, v, e, m in profiles],
        "actions": [{"tool": a["tool"], "agent": a["agent"], "refused": a["error"]} for a in attempts],
        "refused_tool_calls": [{"tool": a["tool"], "said": scrub(a["result"] or "")[:160]} for a in attempts if a["error"]],
        "enforcements": [{"rule": r["rule"], "tool": r.get("tool") or ""} for r in rules],
        "clarified": world.marks.get("clarified"),
        "network_violations": [scrub(v.get("target", "")) for v in world.violations],
        "error": scrub(error),
    }


async def run_one(case, run, harness, scrub, *, keep=False, timeout_scale=1.0) -> dict:
    from evals.live_roles import ROLE_WORLDS

    world = ROLE_WORLDS[case["role"]](case, run, harness)
    CURRENT.set(world)
    harness.worlds.append(world)
    started, status, checks, error = time.monotonic(), "passed", [], ""
    try:
        await world.build()
        try:
            await asyncio.wait_for(world.act(), world.timeout * timeout_scale)
            settled = await world.settle(world.timeout * timeout_scale)
        except asyncio.TimeoutError:
            settled = False
        checks.append(check("finished_in_time", settled, "" if settled else f"still working after {world.timeout * timeout_scale:.0f}s"))
        await world.finalize()
        checks.append(check("network_isolated", not world.violations,
                            f"{len(world.violations)} requests outside the model provider"))
        checks += world.judge(world.observe())
        if not settled:
            status = "timeout"
    except asyncio.CancelledError:
        status = "cancelled_at_cap" if harness.cap_reached() else "error"
        error = "cancelled: the dollar cap was reached" if status == "cancelled_at_cap" else "cancelled"
    except Exception as exc:  # a broken fixture or harness, not a model failure
        logging.getLogger("sparrow.live").exception("run %s #%s failed", case["id"], run)
        status, error = "error", f"{type(exc).__name__}: {exc}"
    record = run_record(world, case, run, status, checks, started, error, scrub)
    world.finished = True
    harness.ledger_spent += record["cost"]["ledger"]
    try:
        await world.close()
    except (Exception, asyncio.CancelledError):  # the state directory is removed regardless
        logging.getLogger("sparrow.live").exception("closing run %s #%s", case["id"], run)
    world.cleanup(keep)
    if keep and world.tmp:
        record["state_dir"] = str(world.root)
    return record


async def evaluate(cases, *, runs, harness, concurrency=2, keep=False, timeout_scale=1.0, progress=None) -> list[dict]:
    """Every case × run, in case order, until the cap. Returns one record per
    planned run, including those that did not happen."""
    scrub = Scrubber(cases)
    planned = [(case, run) for run in range(1, runs + 1) for case in cases]
    tools = media_tools()
    records, pending, active, cancelled = [], list(planned), {}, set()

    def not_run(case, run, why, status="not_run"):
        return {"case": case["id"], "set": case["set"], "role": case["role"], "kind": case["kind"], "run": run,
                "status": status, "passed": None, "checks": [], "failed": [], "error": why}

    while pending or active:
        while pending and len(active) < concurrency:
            if harness.cap_reached():
                break
            case, run = pending.pop(0)
            if case["role"] == "subtitle" and not tools:
                records.append(not_run(case, run, "ffmpeg/ffprobe not found: set SPARROW_FFMPEG and SPARROW_FFPROBE",
                                       "skipped"))
                continue
            active[asyncio.ensure_future(run_one(case, run, harness, scrub, keep=keep,
                                                 timeout_scale=timeout_scale))] = (case, run)
        if harness.cap_reached() and pending:
            for case, run in pending:
                records.append(not_run(case, run, f"not started: the ${harness.max_dollars:.2f} cap was reached"))
            pending = []
        if not active:
            continue
        done, _ = await asyncio.wait(list(active), timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            case, run = active.pop(task)
            try:
                record = task.result()
            except asyncio.CancelledError:
                record = not_run(case, run, "cancelled while finishing: the dollar cap was reached", "cancelled_at_cap")
            except Exception as exc:  # run_one records its own failures; this is a harness fault
                record = not_run(case, run, f"{type(exc).__name__}: {exc}"[:300], "error")
            records.append(record)
            if progress:
                progress(record, harness)
        if harness.cap_reached():
            for task in active:
                if task not in cancelled:
                    cancelled.add(task)  # once: a second cancel would interrupt its cleanup
                    task.cancel()
    order = {(c["id"], r): n for n, (c, r) in enumerate(planned)}
    return sorted(records, key=lambda r: order.get((r["case"], r["run"]), 0))


# ─── Results ──────────────────────────────────────────────────────────────


def _median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def _group(records):
    ran = [r for r in records if r["passed"] is not None]
    per_case = {}
    for r in records:
        entry = per_case.setdefault(r["case"], {"kind": r["kind"], "set": r["set"], "runs": 0, "passed": 0, "planned": 0,
                                               "cost": 0.0, "latencies": [], "failed": {}})
        entry["planned"] += 1
        entry["cost"] += r["cost"]["ledger"] if "cost" in r else 0.0
        if r["passed"] is not None:
            entry["runs"] += 1
            entry["passed"] += int(bool(r["passed"]))
            entry["latencies"].append(r["latency_seconds"])
            for name in r["failed"]:
                entry["failed"][name] = entry["failed"].get(name, 0) + 1
    for entry in per_case.values():
        entry["pass_rate"] = round(entry["passed"] / entry["runs"], 3) if entry["runs"] else None
        entry["median_latency_seconds"] = _median(entry.pop("latencies"))
        entry["cost"] = round(entry["cost"], 6)
    return {
        "planned": len(records), "ran": len(ran), "passed": sum(1 for r in ran if r["passed"]),
        "pass_rate": round(sum(1 for r in ran if r["passed"]) / len(ran), 3) if ran else None,
        "cost": round(sum(r["cost"]["ledger"] for r in records if "cost" in r), 6),  # cancelled runs spent too
        "median_latency_seconds": _median([r["latency_seconds"] for r in ran]),
        "tokens": {k: sum(r["tokens"][k] for r in ran if "tokens" in r) for k in ("input", "cached", "output")},
        "provider_calls": sum(r["calls"]["provider"] for r in ran if "calls" in r),
        "cases": per_case,
    }


def summarize(records, cases) -> dict:
    from evals.live_roles import ROLES

    out = {"by_role": {}, "holdout": {}, "main": _group([r for r in records if r["set"] == "main"]),
           "total": _group(records)}
    for role in ROLES:
        main = [r for r in records if r["role"] == role and r["set"] == "main"]
        held = [r for r in records if r["role"] == role and r["set"] == "holdout"]
        out["by_role"][role] = {"main": _group(main), "holdout": _group(held),
                                "cost": round(sum(r["cost"]["ledger"] for r in main + held if "cost" in r), 6)}
    out["holdout"] = _group([r for r in records if r["set"] == "holdout"])
    out["not_run"] = [{"case": r["case"], "run": r["run"], "status": r["status"], "why": r["error"]}
                      for r in records if r["status"] in ("not_run", "skipped", "cancelled_at_cap")]
    return out


def configuration() -> dict:
    """What the agents ran under: resolved role profiles (model, effort sent,
    bounds, prompt/tool versions) and the subtitle models."""
    from backend.agents import profiles, subtitles

    return {
        "profiles": [{k: v for k, v in p.to_dict().items()
                      if k in ("id", "model", "effort", "effort_sent", "max_steps", "max_tokens", "prompt", "tools", "version")}
                     for p in profiles.all_profiles()],
        "subtitle_models": {"manager": subtitles.review_model(), "checker": subtitles.contractor_model(),
                            "verifier": subtitles.verifier_model()},
        "overrides": {k: os.environ[k] for k in ("SPARROW_PROFILES", "SPARROW_SMART_MODEL", "SPARROW_CHEAP_MODEL",
                                                 "SPARROW_FETCH_SCOUT", "SPARROW_SUBTITLE_MODEL", "SPARROW_SUBTITLE_EFFORT",
                                                 "SPARROW_SUBTITLE_CONTRACTOR", "SPARROW_SUBTITLE_VERIFIER",
                                                 "SPARROW_SUBTITLE_BUDGET") if os.environ.get(k)},
    }


def revision():
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL))
        return head, dirty
    except (OSError, subprocess.CalledProcessError):
        return "", None


def money(value) -> str:
    return f"${value:.4f}" if value < 1 else f"${value:.2f}"


def markdown(result) -> str:
    s, config = result["summary"], result["configuration"]
    models = {p["id"]: f"{p['model']}{' (' + p['effort_sent'] + ')' if p['effort_sent'] else ''}" for p in config["profiles"]}
    lines = [
        f"# Live role evaluation ({result['mode']}, {result['started'][:16]})",
        "",
        f"Models: Discovery {models.get('discovery')}, Fetch {models.get('fetch_scout')} with review "
        f"{models.get('fetch_review')} and escalation {models.get('fetch_escalation')}, Media {models.get('media')}, "
        f"Librarian {models.get('librarian')}, subtitles {config['subtitle_models']['manager']} managing "
        f"{config['subtitle_models']['checker'] or 'no checker'}.",
        f"Cap {money(result['max_dollars'])}; spent {money(result['spent']['ledger'])} by the cost ledger "
        f"({money(result['spent']['provider_meter'])} at the provider meter). "
        f"{s['total']['ran']} of {s['total']['planned']} planned runs ran"
        + (" — stopped at the cap." if result["stopped_at_cap"] else "."),
        f"Main set: {s['main']['passed']}/{s['main']['ran']} runs passed; holdout: {s['holdout']['passed']}/"
        f"{s['holdout']['ran']}" + (f" ({s['holdout']['pass_rate']:.0%})" if s["holdout"]["pass_rate"] is not None else "") + ".",
    ]
    if result["mode"] == "dry-run":
        tokens = s["total"]["tokens"]
        lines += ["", "Dry run: scripted reference answers and estimated tokens. Pass rates test the harness, "
                      "not a model. Its cost is what the same runs would cost live if each agent took the reference "
                      f"path ({s['total']['provider_calls']} calls; {tokens['input']:,} prompt tokens, {tokens['cached']:,} "
                      f"of them cached; {tokens['output']:,} output tokens, reasoning included) at the runtime's list "
                      "prices. Real agents take more turns: budget two to three times this."]
    for role, data in s["by_role"].items():
        main, held = data["main"], data["holdout"]
        lines += ["", f"## {role.capitalize()}", "",
                  f"Pass rate {main['passed']}/{main['ran']} main"
                  + (f", holdout {held['passed']}/{held['ran']}" if held["planned"] else "")
                  + f" · cost {money(data['cost'])} · median latency {main['median_latency_seconds'] or held['median_latency_seconds']} s",
                  "", "| Case | Set | Kind | Passed | Cost | Median latency (s) |", "|---|---|---|---|---|---|"]
        for group in (main, held):
            for case, entry in group["cases"].items():
                lines.append(f"| {case} | {entry['set']} | {entry['kind']} | {entry['passed']}/{entry['runs']}"
                             + (f" (of {entry['planned']})" if entry["runs"] != entry["planned"] else "")
                             + f" | {money(entry['cost'])} | {entry['median_latency_seconds']} |")
    failures = [r for r in result["runs"] if r["status"] in ("failed", "error", "timeout")]
    if failures:
        lines += ["", "## Failures", ""]
        for r in failures:
            why = "; ".join(f"{c['contract']} ({c['detail']})" for c in r["checks"] if not c["passed"]) or r["error"]
            lines.append(f"- {r['case']} run {r['run']} [{r['status']}]: {why}")
    if s["not_run"]:
        lines += ["", "## Runs that did not happen", ""]
        for item in s["not_run"]:
            lines.append(f"- {item['case']} run {item['run']} [{item['status']}]: {item['why']}")
    return "\n".join(lines) + "\n"


# ─── Command line ─────────────────────────────────────────────────────────


def parse(argv=None):
    parser = argparse.ArgumentParser(prog="run_live.py", description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split("\n\n", 1)[1])
    parser.add_argument("--runs", type=int, default=3, help="repetitions of each case (default 3)")
    parser.add_argument("--max-dollars", type=float, default=15.0, help="hard cap on model spend (default 15)")
    parser.add_argument("--per-run-dollars", type=float, default=1.0,
                        help="the household's per-request allowance inside each run (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="scripted answers, no network: free")
    parser.add_argument("--set", choices=["main", "holdout", "all"], default="all")
    parser.add_argument("--cases", help="comma-separated case ids (default: every case in the set)")
    parser.add_argument("--roles", help="comma-separated roles")
    parser.add_argument("--concurrency", type=int, default=3, help="runs at once (default 3)")
    parser.add_argument("--timeout-scale", type=float, default=1.0, help="multiplies each role's time limit")
    parser.add_argument("--output", help="the JSON result (default tests/evals/artifacts/live-<time>.json)")
    parser.add_argument("--summary", help="the markdown summary (default: beside the JSON result)")
    parser.add_argument("--keep-state", action="store_true", help="keep each run's state directory for auditing")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.max_dollars <= 0 or args.concurrency < 1:
        parser.error("runs and concurrency must be positive and the cap above zero")
    return args


async def main_async(args, cases, scripted=None) -> dict:
    harness = Harness(dry_run=args.dry_run, max_dollars=args.max_dollars, per_run_dollars=args.per_run_dollars,
                      scripted=scripted or (ScriptedProvider() if args.dry_run else None))
    started = time.strftime("%Y-%m-%dT%H:%M:%S")

    def progress(record, h):
        print(f"{record['case']} #{record['run']}: {record['status']}"
              + (f" ({', '.join(record['failed'])})" if record.get("failed") else "")
              + f" · spent ${h.total():.4f}", file=sys.stderr, flush=True)

    with harness.installed():
        records = await evaluate(cases, runs=args.runs, harness=harness, concurrency=args.concurrency,
                                 keep=args.keep_state, timeout_scale=args.timeout_scale, progress=progress)
    head, dirty = revision()
    return {
        "version": VERSION, "mode": "dry-run" if args.dry_run else "live", "started": started,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "revision": head, "working_tree_changed": dirty,
        "manifests": manifest_digests(), "repetitions": args.runs, "max_dollars": args.max_dollars,
        "per_run_dollars": args.per_run_dollars,
        "spent": {"ledger": round(sum(r.get("cost", {}).get("ledger", 0) for r in records), 6),
                  "provider_meter": round(harness.spent, 6)},
        "stopped_at_cap": any(r["status"] in ("not_run", "cancelled_at_cap") for r in records),
        "unattributed_network_violations": len(harness.gate.violations),
        "configuration": configuration(),
        "summary": summarize(records, cases), "runs": records,
    }


def select(args) -> list[dict]:
    sets = ("main", "holdout") if args.set == "all" else (args.set,)
    cases = load_cases(sets)
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            raise SystemExit(f"No case named {', '.join(sorted(unknown))}.")
        cases = [c for c in cases if c["id"] in wanted]
    if args.roles:
        roles = {r.strip() for r in args.roles.split(",")}
        cases = [c for c in cases if c["role"] in roles]
    return cases


def main(argv=None):
    args = parse(argv)
    if not args.dry_run and os.getenv("SPARROW_RUN_LIVE_EVALS") != "1":
        raise SystemExit("The live evaluation makes paid model calls (TMDB, the indexer and downloads stay fixtures). "
                         "Set SPARROW_RUN_LIVE_EVALS=1 to run it, or use --dry-run.")
    cases = select(args)
    environment = {**HERMETIC}
    if args.dry_run:
        environment.update(DRY_KEYS)
        environment.update({k: v for k, v in DRY_MODELS.items() if not os.environ.get(k)})
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.ERROR)
    with patch.dict(os.environ, environment):
        result = asyncio.run(main_async(args, cases))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    output = Path(args.output) if args.output else ARTIFACTS / f"live-{'dry-' if args.dry_run else ''}{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    summary = Path(args.summary) if args.summary else output.with_suffix(".md")
    summary.write_text(markdown(result))
    print(markdown(result))
    print(f"Results: {output}\nSummary: {summary}")
    return result


if __name__ == "__main__":
    main()
