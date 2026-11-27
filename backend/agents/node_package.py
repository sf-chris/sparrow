"""What a computer downloads to become Sparrow storage: this server's own code,
the few pure-Python libraries Sparrow Node needs, the media tools and download
app built for its kind of computer, and the setup scripts that install, pair
and start it from one pasted command."""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import re
import tarfile
import time
import zipfile
from importlib import metadata
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent / "node_install"
# Sparrow Node's only third-party needs: httpx and what it imports. All pure Python.
LIBRARIES = (
    "httpx", "httpcore", "h11", "anyio", "idna", "certifi", "sniffio", "typing_extensions",
)
LAUNCHER = '''"""Start Sparrow Node from this folder, with its own copy of its libraries."""
import json
import os
import runpy
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
home = os.path.dirname(here)
arguments = sys.argv[1:]
sys.path[:0] = [here, os.path.join(here, "vendor")]
# The media tools and download app installed beside this folder come first.
tools = os.path.join(home, "tools")
if os.path.isdir(tools):
    os.environ.setdefault("SPARROW_TOOLS_DIR", tools)
for stamp, key in (("VERSION", "SPARROW_NODE_VERSION"), ("RELEASE", "SPARROW_NODE_RELEASE")):
    try:
        with open(os.path.join(here, stamp)) as handle:
            os.environ[key] = handle.read().strip()  # This folder's own, never inherited.
    except OSError:
        pass
try:
    runpy.run_module("backend.agents.node_client", run_name="__main__", alter_sys=True)
except (ImportError, SyntaxError) as error:
    # A new version that can't even start goes back to the one before it.
    state_path = os.path.join(home, "update.json")
    previous = os.path.join(home, "app.previous")
    try:
        with open(state_path) as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    if os.path.basename(here) != "app" or state.get("state") not in ("restarting", "probation") or not os.path.isdir(previous):
        raise
    failed = os.path.join(home, "app.failed")
    if os.path.isdir(failed):
        import shutil
        shutil.rmtree(failed, ignore_errors=True)
    os.replace(here, failed)
    os.replace(previous, here)
    with open(state_path, "w") as handle:
        json.dump({"state": "failed", "target": state.get("to", ""), "at": time.time(),
                   "error": "The new version didn't start, so the one before is back."}, handle)
    for key in ("SPARROW_NODE_VERSION", "SPARROW_NODE_RELEASE"):
        os.environ.pop(key, None)  # The version before stamps its own.
    os.execv(sys.executable, [sys.executable, os.path.join(here, "sparrow-node.py"), *arguments])
'''
# Media tools and the download app, one self-contained archive per platform,
# made when the server image is built (packaging/node_tools).
TOOLS = Path(os.getenv("SPARROW_NODE_TOOLS", "/opt/sparrow/node-tools"))
PLATFORM = re.compile(r"linux-(x86_64|aarch64)")
CODE = re.compile(r"[A-Za-z0-9_-]{20,100}")
EXPIRED = {
    "sh": (
        "#!/bin/sh\n"
        "printf '%s\\n' \"This pairing command expired or was already used. "
        "In Sparrow, open Settings > Computers > Add a computer for a fresh one.\" >&2\n"
        "exit 1\n"
    ),
    "ps1": (
        "Write-Host 'This pairing command expired or was already used. "
        "In Sparrow, open Settings > Computers > Add a computer for a fresh one.' -ForegroundColor Red\n"
    ),
}


def files():
    """(name in the package, path on this server) for everything a node runs."""
    for path in sorted(BACKEND.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            yield "backend/" + path.relative_to(BACKEND).as_posix(), path
    for name in LIBRARIES:
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # typing_extensions is part of newer Pythons.
        for entry in distribution.files or ():
            if "__pycache__" in entry.parts or entry.suffix == ".pyc" or ".." in entry.parts:
                continue
            path = Path(distribution.locate_file(entry))
            if path.is_file():
                yield "vendor/" + entry.as_posix(), path


@functools.lru_cache(maxsize=1)
def contents():
    entries = [("sparrow-node.py", LAUNCHER.encode())]
    entries += [(name, path.read_bytes()) for name, path in files()]
    return entries


@functools.lru_cache(maxsize=1)
def version() -> str:
    """This server's Sparrow Node, as a short fingerprint of everything in it.

    A computer reports the version it runs; a different one means it's out of
    date and running its setup command again updates it.
    """
    digest = hashlib.sha256()
    for name, data in contents():
        digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()[:16]


def tools(platform: str) -> Path | None:
    """The media tools archive for a kind of computer, if this server has one."""
    if not PLATFORM.fullmatch(platform or ""):
        return None
    path = TOOLS / f"tools-{platform}.tar.gz"
    return path if path.is_file() else None


@functools.lru_cache(maxsize=4)
def tools_manifest(platform: str) -> dict | None:
    """What a platform's tools archive holds: its version and tool versions."""
    path = tools(platform)
    if not path:
        return None
    with tarfile.open(path) as archive:
        for member in archive:
            if member.name.lstrip("./") == "manifest.json":
                return json.loads(archive.extractfile(member).read())
    return None


@functools.lru_cache(maxsize=4)
def package(kind: str = "tar.gz") -> bytes:
    stamp = time.time()
    from .release import current

    release = current()
    label = {"version": release["version"], "commit": release["commit"], "date": release["date"]}
    entries = [*contents(), ("VERSION", version().encode()), ("RELEASE", json.dumps(label).encode())]
    buffer = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, data in entries:
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = len(data), stamp, 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def script(kind: str, server: str, code: str, valid: bool) -> str:
    """The setup script for one pairing, or a plain message when it can't pair."""
    if not valid:
        return EXPIRED[kind]
    text = (SCRIPTS / f"install.{kind}").read_text(encoding="utf-8")
    return text.replace("__SPARROW_SERVER__", server).replace("__SPARROW_CODE__", code)


def command(kind: str, server: str, code: str) -> str:
    """The one line a person pastes on the computer they're pairing."""
    url = f"{server}/api/v1/node/setup/{code}.{kind}"
    if kind == "ps1":
        return f"irm {url} | iex"
    return f"curl -fsSL {url} | sh"
