"""What a computer downloads to become Sparrow storage: this server's own code,
the few pure-Python libraries Sparrow Node needs, and the setup scripts that
install, pair and start it from one pasted command."""

from __future__ import annotations

import functools
import io
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
import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [here, os.path.join(here, "vendor")]
runpy.run_module("backend.agents.node_client", run_name="__main__", alter_sys=True)
'''
CODE = re.compile(r"[A-Za-z0-9_-]{20,100}")
EXPIRED = {
    "sh": (
        "#!/bin/sh\n"
        "printf '%s\\n' \"This pairing command expired or was already used. "
        "In Sparrow, open Settings > Storage > Pair a computer for a fresh one.\" >&2\n"
        "exit 1\n"
    ),
    "ps1": (
        "Write-Host 'This pairing command expired or was already used. "
        "In Sparrow, open Settings > Storage > Pair a computer for a fresh one.' -ForegroundColor Red\n"
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


@functools.lru_cache(maxsize=4)
def package(kind: str = "tar.gz") -> bytes:
    stamp = time.time()
    entries = [("sparrow-node.py", LAUNCHER.encode())]
    entries += [(name, path.read_bytes()) for name, path in files()]
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
