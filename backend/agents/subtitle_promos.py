"""Ads and release credits inside subtitle tracks: "Subtitles by …",
"ReEncoded by … www.…", "Support us and become VIP member…".

Code shortlists the captions that could be one (a web address, a handle, a
credit word, or the first and last captions); the household's cheap model
says which of those are not part of the programme; those captions are
deleted whole. Nothing is reworded, and a caption the model was not shown
can never be removed."""

import json
import re

# Anything that might be an ad or a credit. Deliberately loose: the model
# decides, and it only ever sees these.
LOOKS_PROMOTIONAL = re.compile(
    r"https?://|www\.|\b[\w-]+\.(?:com|org|net|io|tv|to|me|info|xyz|cc|co|ru|nl|de|se|ws)\b"
    r"|@\w{3,}|subtit|caption|\bsync|correct(?:ed|ions?)\b|\brip(?:ped|per|s)?\b|encod"
    r"|transcri|translat(?:ed|ion|or)\b|advertis|\bvip\b|support us|donat|opensub|addic7ed"
    r"|podnapisi|subscene|yify|\byts\b|rarbg|\bpsa\b|eztv|torrent|telegram|patreon|discord",
    re.IGNORECASE,
)
EDGE = 2  # first and last captions are where credits usually sit
MOST = 60

SYSTEM = """You clean subtitle tracks. You are shown some captions from one \
film or episode, each with its number and time. Say which are promotional or \
release credits rather than part of the programme: website addresses, ads, \
"subtitles/sync/corrections/translation by", "ripped/encoded by", group or \
site names, requests to donate, subscribe or become a member.

Keep anything that belongs to the programme: dialogue, narration, song \
lyrics, on-screen text and signs, chapter or title cards, quotations, and the \
programme's own credits. When unsure, keep it. Answer with the numbers to \
remove, which may be none."""

SCHEMA = {
    "type": "object",
    "properties": {"remove": {"type": "array", "items": {"type": "integer"}}},
    "required": ["remove"],
    "additionalProperties": False,
}


def shortlist(cues):
    """Indexes of captions worth showing the model, in order."""
    edges = set(range(min(EDGE, len(cues)))) | set(range(max(0, len(cues) - EDGE), len(cues)))
    found = [i for i, cue in enumerate(cues) if i in edges or LOOKS_PROMOTIONAL.search(cue["text"])]
    if not any(LOOKS_PROMOTIONAL.search(cues[i]["text"]) for i in found):
        return []  # nothing even looks like an ad: no model call
    return found[:MOST]


def stamp(seconds):
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


async def find(caller, cues):
    """Indexes of captions to delete, and the call's spend."""
    from .subtitle_contract import cost

    shown = shortlist(cues)
    if not shown:
        return [], None
    prompt = "\n".join(
        f'{i + 1} [{stamp(cues[i]["start"])}] {json.dumps(cues[i]["text"], ensure_ascii=False)}'
        for i in shown
    )
    answer, usage = await caller(SYSTEM, f"{len(cues)} captions in the track. Shown:\n{prompt}", SCHEMA)
    allowed = set(shown)
    remove = sorted({n - 1 for n in answer.get("remove", []) if isinstance(n, int)} & allowed)
    spend = {
        "model": caller.model,
        "calls": 1,
        "dollars": round(cost(caller.model, usage), 6),
        "phase": "subtitle_promotions",
    }
    return remove, spend
