"""Real app/UI/media with isolated catalogue fixtures and no model or acquisition traffic."""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
STATE = Path(os.environ.get("SPARROW_BROWSER_STATE", "/tmp/sparrow-browser-check"))
os.environ["SPARROW_DATA_DIR"] = str(STATE)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("TMDB_API_KEY", None)
from backend import main as m
from backend.agents.service import AgentService
from backend.agents.models import Job, JobStatus, AgentSession, AgentKind, SessionStatus
from backend.models import SparrowConfig, LibraryItem, MediaType
from backend.agents.node_executor import probe_file
from tests.test_playback import make_video

TITLES = {
    101: ("Harbour Lights", "tv"),
    102: ("The Quiet Planet", "movie"),
    103: ("A Long Way Home", "movie"),
    104: ("Northern Signal", "tv"),
    105: ("Tomorrow Bay", "movie"),
    106: ("The Last Evening", "movie"),
}
PALETTES = [("#243f56", "#afd6cf", "#f1e7c9"), ("#675c94", "#d6cce9", "#f8e5a7"), ("#e6b5a5", "#7e443f", "#fff2d8"), ("#283c46", "#c7dfaa", "#729d9a"), ("#aec5d1", "#375e7e", "#f5d8b5"), ("#8a4163", "#ecc7db", "#dab291")]


def fixture_art(identity, title, wide=False):
    # Original geometric covers for fictional browser fixtures, not catalogue data.
    bg, ink, accent = PALETTES[(identity - 101) % len(PALETTES)]
    width, height = (1000, 560) if wide else (400, 600)
    midpoint = (len(title.split()) + 1) // 2
    lines = [" ".join(title.split()[:midpoint]), " ".join(title.split()[midpoint:])]
    motifs = [
        '<path d="M30 310c90-100 160 100 290-20M0 350c140-100 180 100 370-20M-30 390c160-100 240 100 430-20" fill="none" stroke="{ink}" stroke-width="8"/><path d="m185 60 70 190H115Z" fill="{accent}"/><path d="M190 58v196" stroke="{bg}" stroke-width="5"/>',
        '<circle cx="207" cy="228" r="107" fill="{ink}"/><ellipse cx="207" cy="228" rx="177" ry="34" transform="rotate(-32 207 228)" fill="none" stroke="{accent}" stroke-width="9"/><circle cx="75" cy="86" r="6" fill="{accent}"/><circle cx="338" cy="322" r="9" fill="{accent}"/>',
        '<path d="M0 370 170 45 395 370Z" fill="{ink}"/><path d="M160 360c230-10-140-124 90-208" fill="none" stroke="{accent}" stroke-width="23"/>',
        '<g fill="none" stroke="{ink}" stroke-width="9"><circle cx="200" cy="230" r="52"/><circle cx="200" cy="230" r="87"/><circle cx="200" cy="230" r="122"/><path d="M200 65v330M35 230h330" stroke-width="3"/></g><circle cx="200" cy="230" r="16" fill="{accent}"/>',
        '<path d="M50 335V175a150 150 0 0 1 300 0v160Z" fill="{ink}"/><path d="M96 335V185a104 104 0 0 1 208 0v150Z" fill="{accent}"/><path d="M140 335V194a60 60 0 0 1 120 0v141Z" fill="{bg}"/>',
        '<path d="M300 60c-135 10-196 104-191 189 6 114 146 145 209 37-101 20-191-113-18-226Z" fill="{ink}"/><path d="m318 142 8 25 26 7-26 8-8 25-8-25-25-8 25-7 8-25Z" fill="{accent}"/>'
    ]
    motif = motifs[(identity - 101) % len(motifs)].format(bg=bg, ink=ink, accent=accent)
    art = f'<g transform="translate({width * .3 if wide else 0},0)">{motif}</g>'
    lettering = "" if wide else "".join(f'<text x="30" y="{465 + i * 43}" font-size="37" font-weight="700" letter-spacing="-1" fill="{ink}" font-family="Arial,sans-serif">{line.upper()}</text>' for i, line in enumerate(lines))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="{bg}"/>{art}{lettering}<text x="30" y="{height - 28}" fill="{ink}" font-size="9" letter-spacing="2" font-family="Arial,sans-serif">SPARROW · FICTIONAL TEST COLLECTION</text></svg>'


def details(identity):
    name, kind = TITLES.get(int(identity), ("Catalogue Fixture", "movie"))
    return {
        "id": int(identity),
        "title": name,
        "name": name,
        "overview": "A close-knit coastal community follows a strange signal beyond the familiar shore. An isolated test collection for checking Sparrow’s complete viewing experience.",
        "release_date": "2024-01-01",
        "first_air_date": "2024-01-01",
        "runtime": 1,
        "original_language": "en",
        "poster_path": f"/art/{identity}.svg",
        "backdrop_path": f"/art/{identity}-backdrop.svg",
        "seasons": (
            [
                {"season_number": 1, "episode_count": 3, "name": "Season 1"},
                {"season_number": 2, "episode_count": 3, "name": "Season 2"},
            ]
            if kind == "tv"
            else []
        ),
    }


async def tmdb(path, **kwargs):
    parts = path.strip("/").split("/")
    if parts[0] == "search":
        return {
            "results": [
                details(i)
                for i, (name, kind) in TITLES.items()
                if kwargs.get("query", "").lower() in name.lower()
            ]
        }
    if len(parts) > 2 and parts[2] == "season":
        return {
            "episodes": [
                {
                    "episode_number": e,
                    "name": ["The Signal", "A Small Discovery", "Back to the Shore"][
                        e - 1
                    ],
                    "air_date": "2024-01-01",
                    "runtime": 1,
                }
                for e in range(1, 4)
            ]
        }
    return details(parts[1])


async def suggest(query, *args, **kwargs):
    return [
        {
            "tmdb_id": i,
            "media_type": kind,
            "title": name,
            "year": "2024",
            "poster_url": f"/art/{i}.svg",
        }
        for i, (name, kind) in TITLES.items()
        if query.lower() in name.lower()
    ]


m.meta_svc.tmdb_quick_suggest = suggest


@asynccontextmanager
async def lifespan(app):
    await m.storage.load_all()
    library = STATE / "library"
    library.mkdir(parents=True, exist_ok=True)
    incoming = STATE / "incoming"
    incoming.mkdir(exist_ok=True)
    await m.storage.save_config(
        SparrowConfig(
            library_dir=str(library),
            staging_dir=str(incoming),
            tmdb_api_key="fixture-only",
        )
    )
    m.agent_service = AgentService(m.storage, str(STATE), m.broadcast)
    m.agent_service.toolbox.tmdb_get = tmdb
    m.agent_service.emit = AsyncMock()
    m.subtitles.attach(m.agent_service)
    m.discovery.register()
    # A generated video with two actual audio tracks and three embedded captions.
    source = library / "fixture.mp4"
    if not source.exists():
        await make_video(library)
    facts = await probe_file(source)
    for identity, (title, kind) in TITLES.items():
        m.catalogue.cache_title(kind, identity, details(identity))
        item = LibraryItem(
            id=str(identity),
            title=title,
            tmdb_id=identity,
            media_type=MediaType(kind),
            path=str(source),
            year=2024,
            overview=details(identity)["overview"],
            poster_path=f"/art/{identity}.svg",
            backdrop_path=details(identity)["backdrop_path"],
        )
        await m.storage.add_library_item(item)
        copy = library / f"{identity}.mp4"
        if not copy.exists():
            os.link(source, copy)
        m.catalogue.save_asset(
            item.id,
            "local",
            "library",
            copy.name,
            facts,
            season=1 if kind == "tv" else None,
            episode=1 if kind == "tv" else None,
        )
        art_dir = STATE / "art"
        art_dir.mkdir(exist_ok=True)
        (art_dir / f"{identity}.svg").write_text(fixture_art(identity, title))
        (art_dir / f"{identity}-backdrop.svg").write_text(fixture_art(identity, title, wide=True))
    yield
    if m.nodes.local()._hls_cache:
        for identity in list(m.nodes.local()._hls_cache.jobs):
            await m.nodes.local()._hls_cache.stop(identity)


m.app.router.lifespan_context = lifespan
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(m.app, host="127.0.0.1", port=int(os.environ.get("SPARROW_BROWSER_PORT", "8891")))
