"""
The resolution layer (NOT an agent).

The search box hits TMDB directly. Fuzzy human descriptions — "the show
where the teacher cooks meth" — go through a single cheap LLM call with a
TMDB search tool and come back as poster cards. Disambiguation (Office US
vs UK) happens visually, by showing the options. Picking a card and
confirming creates a job; nothing here decides anything beyond which cards
to show.
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Optional

from ..services.metadata_service import tmdb_quick_suggest

logger = logging.getLogger("sparrow.resolve")

DEFAULT_CHEAP_MODEL = "claude-haiku-4-5"


def _looks_descriptive(query: str, direct_hits: list[dict]) -> bool:
    """A title search that TMDB nails needs no LLM. A miss, or a wordy
    natural-language description, gets one cheap call."""
    if not direct_hits:
        return True
    words = query.strip().split()
    return len(words) >= 4 and not any(
        query.lower().strip() in (h["title"] or "").lower() for h in direct_hits)


async def resolve(query: str, tmdb_key: str, anthropic_key: str,
                  cheap_model: str = DEFAULT_CHEAP_MODEL) -> list[dict]:
    direct = await tmdb_quick_suggest(query, tmdb_key)
    if not anthropic_key or not _looks_descriptive(query, direct):
        return direct

    llm_titles = await _describe_to_titles(query, anthropic_key, cheap_model)
    if not llm_titles:
        return direct

    seen = {(c["media_type"], c["tmdb_id"]) for c in direct}
    cards = []
    for title in llm_titles[:5]:
        for c in await tmdb_quick_suggest(title, tmdb_key):
            key = (c["media_type"], c["tmdb_id"])
            if key not in seen:
                seen.add(key)
                cards.append(c)
            break  # top hit per suggested title
    # LLM-resolved cards first (they answer the description), then direct.
    return (cards + direct)[:10]


async def _describe_to_titles(query: str, api_key: str, model: str) -> list[str]:
    """One cheap call: description in, canonical titles out."""
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await asyncio.wait_for(client.messages.create(
            model=(os.getenv("SPARROW_CHEAP_MODEL") or model or DEFAULT_CHEAP_MODEL),
            max_tokens=600,
            tools=[{
                "name": "identify_titles",
                "description": "Return the canonical movie/TV titles the user is describing",
                "input_schema": {
                    "type": "object",
                    "properties": {"titles": {
                        "type": "array", "items": {"type": "string"},
                        "description": "1-4 canonical titles, most likely first"}},
                    "required": ["titles"],
                },
            }],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content":
                f'Someone described what they want to watch as: "{query}"\n\n'
                'Which movie or TV show are they most likely describing? Give the '
                'canonical title(s), most likely first. Example: "the show where the '
                'teacher cooks meth" → ["Breaking Bad"].'}],
        ), timeout=10.0)
        for block in msg.content:
            if block.type == "tool_use" and block.name == "identify_titles":
                return [t for t in block.input.get("titles", []) if isinstance(t, str)]
    except Exception:
        logger.exception("describe_to_titles failed")
    return []
