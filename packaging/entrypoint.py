"""Prepare durable folders before starting the single coordinator process."""

import os
from pathlib import Path
from backend.agents.migrations import prepare

root = Path(os.getenv("SPARROW_DATA_DIR", "/data"))
prepare(root)
# One worker owns the in-memory projection and agent scheduling; SQLite stores
# durable truth. More HTTP workers require a separate coordinator, not a flag.
os.execvp(
    "python",
    [
        "python",
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8888",
        "--workers",
        "1",
        "--no-server-header",
    ],
)
