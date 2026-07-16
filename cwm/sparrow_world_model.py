"""
Sparrow Code World Model (CWM)
==============================
Starts minimal. Grows from real usage.

When the search engine fails to find results, Claude adds a new strategy_ method.
When the organiser can't parse a filename, Claude adds a new pattern_ method.
Over time this file becomes a complete model of how the pipeline works —
built from actual failures, not assumptions.

HOW TO ADD A STRATEGY
─────────────────────
def strategy_<name>(self, query: str, context: dict) -> list[str]:
    "When this applies and why it was added."
    return ["list of queries to try"]

HOW TO ADD A PATTERN
────────────────────
def pattern_<name>(self, filename: str) -> dict | None:
    "What naming convention this handles and why it was added."
    # return {"season": N, "episode": N} or None

Both are auto-discovered by prefix scan. Never remove — they are the history
of what the system learned.
"""
from __future__ import annotations

import inspect
import json
import re
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, Any


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH STRATEGIES
# ═══════════════════════════════════════════════════════════════════════════════

class SearchStrategies:
    PRECEDENCE: dict[str, int] = {
        "bare": 10,
    }

    def strategy_bare(self, query: str, context: dict) -> list[str]:
        "Try the query exactly as given, and with preferred quality appended. When prefer_smaller is set, also tries HEVC/x265 variants which are typically 30-60% smaller."
        quality = context.get("quality", "1080p")
        prefer_smaller = context.get("prefer_smaller", False)
        queries = [f"{query} {quality}", query]
        if prefer_smaller:
            # HEVC/x265 encodes are significantly smaller; try these first
            queries = [
                f"{query} {quality} x265",
                f"{query} {quality} HEVC",
                f"{query} {quality} x265 HEVC",
            ] + queries
        return queries

    # — Claude adds new strategies below this line —

    def get_all(self) -> list[tuple[int, str, Any]]:
        out = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if name.startswith("strategy_"):
                key = name[len("strategy_"):]
                out.append((self.PRECEDENCE.get(key, 999), key, method))
        return sorted(out)

    def run_all(self, query: str, context: dict) -> list[tuple[str, list[str]]]:
        seen: set[str] = set()
        result = []
        for _, name, method in self.get_all():
            queries = [q for q in method(query, context) if q.lower() not in seen]
            for q in queries:
                seen.add(q.lower())
            if queries:
                result.append((name, queries))
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# ORGANISER PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

class OrganizerPatterns:
    PRECEDENCE: dict[str, int] = {
        "sxxexx": 10,
    }

    def pattern_sxxexx(self, filename: str) -> Optional[dict]:
        "Standard SxxExx — the most common TV episode format."
        m = re.search(r'[Ss](\d{1,2})[Ee](\d{1,4})', filename)
        if m:
            return {"season": int(m.group(1)), "episode": int(m.group(2))}
        return None

    # — Claude adds new patterns below this line —

    def get_all(self) -> list[tuple[int, str, Any]]:
        out = []
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if name.startswith("pattern_"):
                key = name[len("pattern_"):]
                out.append((self.PRECEDENCE.get(key, 999), key, method))
        return sorted(out)

    def parse(self, filename: str) -> Optional[dict]:
        for _, name, method in self.get_all():
            result = method(filename)
            if result is not None:
                return {**result, "_pattern": name}
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE STATE
# ═══════════════════════════════════════════════════════════════════════════════

class SparrowWorldModel:
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.search_strategies = SearchStrategies()
        self.organizer_patterns = OrganizerPatterns()

    def _load(self, filename: str) -> Any:
        p = self.data_dir / filename
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    @property
    def config(self) -> dict:
        return self._load("config.json") or {}

    @property
    def downloads(self) -> list[dict]:
        return self._load("downloads.json") or []

    @property
    def library(self) -> list[dict]:
        return self._load("library.json") or []

    def get_search_plan(self, query: str, media_type: str = "unknown", quality: str = "1080p") -> list[tuple[str, list[str]]]:
        return self.search_strategies.run_all(query, {"media_type": media_type, "quality": quality})

    def get_episode_queries(self, title: str, season: int, episode: int, quality: str = "1080p") -> list[str]:
        """Generate targeted search queries for a specific TV episode.
        Used by the season-download flow when we know exact title/season/episode."""
        se = f"S{season:02d}E{episode:02d}"
        queries = [
            f"{title} {se} {quality}",
            f"{title} {se}",
        ]
        # Try without leading "The" — indexers are inconsistent about articles
        if title.lower().startswith("the "):
            t = title[4:]
            queries += [f"{t} {se} {quality}", f"{t} {se}"]
        return queries

    def parse_filename(self, filename: str) -> Optional[dict]:
        return self.organizer_patterns.parse(filename)

    def check_torrent_client(self) -> dict:
        cfg = self.config
        tc = cfg.get("torrent_client", {})
        t, h, p = tc.get("type", "none"), tc.get("host", "localhost"), tc.get("port", 8080)
        if t == "none":
            return {"reachable": False, "error": "No torrent client configured"}
        try:
            socket.create_connection((h, p), timeout=2).close()
        except Exception:
            proc = subprocess.run(["pgrep", "-i", t], capture_output=True, text=True)
            running = bool(proc.stdout.strip())
            msg = f"{t} not accepting connections on {h}:{p}."
            if running and t == "transmission":
                msg += " Transmission is running but RPC is disabled — Preferences → Remote → Allow remote access."
            elif running:
                msg += f" Process is running but web API on port {p} is not responding."
            else:
                msg += f" Is {t} running?"
            return {"reachable": False, "error": msg}
        return {"reachable": True, "type": t}

    def analyze_pipeline_health(self) -> dict:
        client = self.check_torrent_client()
        cfg = self.config
        issues, warnings = [], []

        if not client.get("reachable"):
            issues.append(client.get("error"))
        if not cfg.get("staging_dir"):
            issues.append("Staging directory not configured")
        if not cfg.get("library_dir"):
            issues.append("Library directory not configured")
        if not cfg.get("tmdb_api_key"):
            warnings.append("No TMDB key — metadata/art won't be fetched")
        if not cfg.get("anthropic_api_key"):
            warnings.append("No Anthropic key — CWM can't evolve")

        errors = [d for d in self.downloads if d.get("status") == "error"]
        for d in errors:
            issues.append(f"Download error: {d.get('name','?')[:40]} — {d.get('error_message','')[:80]}")

        return {
            "healthy": not issues,
            "issues": issues,
            "warnings": warnings,
            "summary": {
                "torrent_client": client.get("type", "none"),
                "reachable": client.get("reachable", False),
                "downloads": len(self.downloads),
                "library": len(self.library),
                "search_strategies": len(self.search_strategies.get_all()),
                "organizer_patterns": len(self.organizer_patterns.get_all()),
            }
        }

    def get_status_report(self) -> str:
        h = self.analyze_pipeline_health()
        s = h["summary"]
        lines = [
            "=== SPARROW ===",
            f"Client:     {s['torrent_client']} ({'OK' if s['reachable'] else 'NOT REACHABLE'})",
            f"Downloads:  {s['downloads']}",
            f"Library:    {s['library']} items",
            f"Strategies: {s['search_strategies']} search, {s['organizer_patterns']} organiser",
        ]
        if h["issues"]:
            lines += ["\nISSUES:"] + [f"  [!] {i}" for i in h["issues"]]
        if h["warnings"]:
            lines += ["\nWARNINGS:"] + [f"  [~] {w}" for w in h["warnings"]]
        return "\n".join(lines)


if __name__ == "__main__":
    import sys
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "./data"
    model = SparrowWorldModel(data_dir=data_dir)
    print(model.get_status_report())
