"""Sparrow installation and configuration doctor."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .configuration import effective_anthropic_key, effective_tmdb_key, normalize_config
from .models import SparrowConfig, TorrentClientType
from .services.torrent_client import TorrentManager
from .storage import Storage


ROOT = Path(__file__).resolve().parent.parent


@dataclass
class DoctorCheck:
    id: str
    ok: bool
    title: str
    detail: str
    required: bool = True
    fix: str = ""


def _directory_check(check_id: str, title: str, raw: str,
                     *, required: bool = True) -> DoctorCheck:
    if not raw:
        return DoctorCheck(check_id, False, title, "Not configured.", required=required,
                           fix="Choose a folder in Settings.")
    path = Path(raw)
    if not path.exists():
        return DoctorCheck(check_id, False, title, f"{path} does not exist.",
                           required=required, fix="Create it or choose another folder.")
    if not path.is_dir():
        return DoctorCheck(check_id, False, title, f"{path} is not a directory.",
                           required=required)
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        return DoctorCheck(check_id, False, title,
                           f"{path} is not readable and writable by Sparrow.",
                           required=required)
    return DoctorCheck(check_id, True, title, f"Ready: {path}", required=required)


async def build_report(storage: Storage, *, check_client: bool = True,
                       runtime_only: bool = False) -> dict:
    config = normalize_config(storage.get_config())
    checks: list[DoctorCheck] = []

    checks.append(DoctorCheck(
        "python", sys.version_info >= (3, 11), "Python 3.11+",
        f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        fix="Install Python 3.11 or newer.",
    ))
    node = shutil.which("node")
    node_version = ""
    node_ok = False
    if node:
        try:
            node_version = subprocess.run(
                [node, "--version"], check=True, capture_output=True, text=True,
                timeout=5,
            ).stdout.strip().lstrip("v")
            node_ok = int(node_version.split(".", 1)[0]) >= 20
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    checks.append(DoctorCheck(
        "node", node_ok, "Node.js 20+",
        f"Node.js {node_version}" if node_version else "Not found or unreadable in PATH.",
        required=not (ROOT / "frontend" / "dist" / "index.html").exists(),
        fix="Install Node.js 20 or newer to build the frontend.",
    ))
    ffprobe = shutil.which("ffprobe")
    checks.append(DoctorCheck(
        "ffprobe", bool(ffprobe), "ffprobe", ffprobe or "Not found in PATH.",
        fix="Install ffmpeg (which includes ffprobe).",
    ))
    checks.append(_directory_check(
        "staging", "Staging folder", config.staging_dir, required=not runtime_only))
    checks.append(_directory_check(
        "library", "Library folder", config.library_dir, required=not runtime_only))

    if config.staging_dir and config.library_dir:
        staging = Path(config.staging_dir)
        library = Path(config.library_dir)
        separated = not (staging == library or staging in library.parents or library in staging.parents)
        checks.append(DoctorCheck(
            "separate_roots", separated, "Separate staging and library roots",
            "The folders are separate." if separated else
            "Staging and library overlap, which can make incomplete files look like library media.",
            required=not runtime_only,
            fix="Use sibling folders such as ~/Sparrow/Temp and ~/Sparrow/Library.",
        ))

    checks.append(DoctorCheck(
        "tmdb_key", bool(effective_tmdb_key(config)), "TMDB API key",
        "Configured." if effective_tmdb_key(config) else "Not configured.",
        required=not runtime_only,
        fix="Add a TMDB API key in Settings.",
    ))
    checks.append(DoctorCheck(
        "anthropic_key", bool(effective_anthropic_key(config)), "Anthropic API key",
        "Configured." if effective_anthropic_key(config) else "Not configured.",
        required=not runtime_only,
        fix="Add an Anthropic API key in Settings.",
    ))
    checks.append(DoctorCheck(
        "frontend", (ROOT / "frontend" / "dist" / "index.html").exists(),
        "Production frontend", "Built." if (ROOT / "frontend" / "dist" / "index.html").exists() else "Not built.",
        fix="Run ./scripts/install.sh or npm run build in frontend/.",
    ))

    client_ok = config.torrent_client.type != TorrentClientType.NONE
    client_detail = "Not configured."
    if client_ok and check_client:
        info = await TorrentManager(config.torrent_client).get_info()
        client_ok = info.reachable
        client_detail = (
            f"Connected to {info.type.value} at {info.host}:{info.port}." if info.reachable
            else f"Configured {config.torrent_client.type.value}, but it is not reachable."
        )
    elif client_ok:
        client_detail = f"Configured: {config.torrent_client.type.value}."
    checks.append(DoctorCheck(
        "torrent_client", client_ok, "Torrent client", client_detail,
        required=not runtime_only,
        fix="Start/configure Transmission or qBittorrent, then run the doctor again.",
    ))

    required = [check for check in checks if check.required]
    return {
        "healthy": all(check.ok for check in required),
        "checks": [asdict(check) for check in checks],
        "summary": {
            "passed": sum(check.ok for check in required),
            "required": len(required),
            "warnings": sum(not check.ok for check in checks if not check.required),
        },
    }


async def _main(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    storage = Storage(os.getenv("SPARROW_DATA_DIR", str(ROOT / "data")))
    await storage.load_all()
    report = await build_report(
        storage, check_client=not args.skip_client, runtime_only=args.runtime_only)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for check in report["checks"]:
            marker = "PASS" if check["ok"] else ("WARN" if not check["required"] else "FAIL")
            print(f"[{marker}] {check['title']}: {check['detail']}")
            if not check["ok"] and check["fix"]:
                print(f"       Fix: {check['fix']}")
        summary = report["summary"]
        print(f"\n{summary['passed']}/{summary['required']} required checks passed.")
    return 0 if report["healthy"] else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check whether Sparrow is ready to run.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--skip-client", action="store_true", help="do not connect to the torrent client")
    parser.add_argument("--runtime-only", action="store_true",
                        help="require runtimes while allowing onboarding to be incomplete")
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
