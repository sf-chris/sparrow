"""Fetch only pinned, digest-verified release inputs; never execute downloads."""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path


def fetch(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((Path(__file__).parent / "sources.json").read_text())
    for asset in manifest["assets"]:
        path = destination / asset["file"]
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".pending")
            urllib.request.urlretrieve(asset["url"], temporary)
            temporary.replace(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError("Release source checksum mismatch: " + asset["file"])
    return manifest


if __name__ == "__main__":
    fetch(sys.argv[1])
