"""The public torrent index the Fetch tools search (apibay).

Transport only: rows come back as the index reports them, minus empty
hashes and unseeded entries. Judging and ranking belong to the scout and
the agents.
"""
from __future__ import annotations

import urllib.parse

import httpx

APIBAY = "https://apibay.org"


async def query(q: str, cat: str = "0", *, strict: bool = False) -> list[dict]:
    """Search the index. With strict, an outage or bad response raises instead
    of looking like zero results."""
    url = f"{APIBAY}/q.php?q={urllib.parse.quote(q)}&cat={cat}"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                return [
                    x for x in data
                    if x.get("info_hash") and x["info_hash"] != "0" * 40
                    and int(x.get("seeders", 0)) > 0
                ]
            if strict:
                raise ValueError("The title source returned an invalid response.")
    except Exception:
        if strict:
            raise
    return []
