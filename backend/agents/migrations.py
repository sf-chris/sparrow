"""Local, versioned migration snapshots taken before persistent schemas change."""

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

SCHEMA_GENERATION = "agent-evidence-1"


def prepare(data_dir):
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / (".migration-" + SCHEMA_GENERATION)
    if marker.exists():
        return
    files = [
        p
        for p in root.iterdir()
        if p.is_file() and p.suffix in (".db", ".sqlite", ".json")
    ]
    if files:
        target = (
            root
            / "backups"
            / ("before-" + SCHEMA_GENERATION + "-" + str(time.time_ns()))
        )
        target.mkdir(parents=True, mode=0o700)
        for source in files:
            destination = target / source.name
            if source.suffix in (".db", ".sqlite"):
                original = sqlite3.connect("file:" + str(source) + "?mode=ro", uri=True)
                backup = sqlite3.connect(destination)
                try:
                    if original.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError(
                            "The database needs recovery before migration: "
                            + source.name
                        )
                    original.backup(backup)
                finally:
                    backup.close()
                    original.close()
            else:
                shutil.copy2(source, destination)
        (target / "manifest.json").write_text(
            json.dumps(
                {
                    "generation": SCHEMA_GENERATION,
                    "files": [p.name for p in files],
                    "created": time.time(),
                }
            )
        )
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(SCHEMA_GENERATION)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_text(path, text):
    temporary = path.with_suffix(path.suffix + ".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
