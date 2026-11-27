"""Which Sparrow this is: a version that identifies the code, and the commit and
date people recognise.

The version is the fingerprint of Sparrow Node's package (see node_package), so
two computers with the same version run the same code. The commit and date are
labels: from the image build (SPARROW_COMMIT, SPARROW_COMMIT_DATE), else from
git for a source install, else the newest file's date.
"""

from __future__ import annotations

import datetime
import functools
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _git(*args) -> str:
    if not (ROOT / ".git").exists():
        return ""
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _newest_file_date() -> str:
    newest = 0.0
    for path in (ROOT / "backend").rglob("*.py"):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    if not newest:
        return ""
    return datetime.datetime.fromtimestamp(newest, datetime.timezone.utc).isoformat(timespec="seconds")


@functools.lru_cache(maxsize=1)
def current() -> dict:
    """This server's release: {"version", "commit", "date"} (commit may be "")."""
    from .node_package import version

    commit = os.getenv("SPARROW_COMMIT", "").strip()[:12]
    date = os.getenv("SPARROW_COMMIT_DATE", "").strip()
    if not commit:
        commit = _git("log", "-1", "--format=%h")[:12]
        date = date or _git("log", "-1", "--format=%cI")
    return {"version": version(), "commit": commit, "date": date or _newest_file_date()}
