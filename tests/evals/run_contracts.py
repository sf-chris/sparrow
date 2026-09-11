"""Versioned offline outcome baseline. This does not select live model profiles."""

import argparse
import hashlib
import io
import json
import logging
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from backend.agents.runtime import AgentRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("runs must be positive")
    manifest = Path(__file__).with_name("contracts-v1.json")
    data = json.loads(manifest.read_text())
    source_hash = hashlib.sha256()
    for source in sorted([*ROOT.glob("backend/**/*.py"), *ROOT.glob("tests/**/*.py")]):
        source_hash.update(str(source.relative_to(ROOT)).encode())
        source_hash.update(source.read_bytes())
    report = {
        "version": data["version"],
        "source_sha256": source_hash.hexdigest(),
        "python_version": sys.version.split()[0],
        "mode": data["mode"],
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "working_tree_changed": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            )
        ),
        "repetitions": args.runs,
        "real_model_calls": 0,
        "provider_cost_usd": 0,
        "model_selection_valid": False,
        "holdout": "not evaluated; separate live holdout still required",
        "results": [],
    }
    logging.getLogger("asyncio").setLevel(logging.ERROR)
    # Even an accidentally unmocked test cannot make a paid provider request.
    with patch.object(
        AgentRuntime,
        "_client",
        side_effect=AssertionError("Live calls forbidden in controlled baseline"),
    ):
        for case in data["cases"]:
            for run in range(args.runs):
                captured = io.StringIO()
                suite = unittest.defaultTestLoader.loadTestsFromName(case["test"])
                started = time.monotonic()
                result = unittest.TextTestRunner(stream=captured).run(suite)
                passed = (
                    result.wasSuccessful()
                    and not result.skipped
                    and result.testsRun == 1
                )
                report["results"].append(
                    {
                        **case,
                        "run": run + 1,
                        "passed": passed,
                        "latency_seconds": time.monotonic() - started,
                        "failures": result.failures + result.errors,
                        "skipped": result.skipped,
                        "evidence": "Outcome assertions in the versioned test; controlled responses are not model-quality evidence.",
                    }
                )
                if not passed:
                    print(captured.getvalue(), file=sys.stderr)
    # unittest error tuples include TestCase objects; serialize only their IDs.
    for item in report["results"]:
        for key in ("failures", "skipped"):
            item[key] = [[test.id(), reason] for test, reason in item[key]]
    report["passed"] = all(item["passed"] for item in report["results"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "cases": len(data["cases"]),
                "runs": len(report["results"]),
                "mode": report["mode"],
            }
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
