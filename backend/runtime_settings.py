"""Environment-driven network and production runtime settings."""
from __future__ import annotations

import os
from urllib.parse import urlparse


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def configured_origins() -> list[str]:
    raw = os.getenv("SPARROW_ALLOWED_ORIGINS", "")
    if raw.strip():
        return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    # Vite's development origin is the only cross-origin default. The production
    # frontend is served by FastAPI and therefore uses same-origin requests.
    return ["http://127.0.0.1:3000", "http://localhost:3000"]


def validate_bind(host: str) -> None:
    if host not in LOOPBACK_HOSTS and not env_bool("SPARROW_ALLOW_LAN"):
        raise RuntimeError(
            f"Refusing to bind Sparrow to {host!r}. Set SPARROW_ALLOW_LAN=1 "
            "only for a trusted private network; public internet exposure is unsupported."
        )


def websocket_origin_allowed(origin: str | None, host_header: str) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    if parsed.netloc == host_header:
        return True
    normalized = origin.rstrip("/")
    return normalized in configured_origins()
