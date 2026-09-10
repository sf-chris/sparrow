# Contributing to Sparrow

Thank you for helping build Sparrow. This is an agentic system with strict
architecture boundaries; please read [DESIGN.md](DESIGN.md) first.

## Ground rules

- Uncertain acquisition and organisation decisions belong to an LLM tool loop.
- Deterministic code may observe facts, provide tools, route events, and enforce
  guardrails. It must not become a hidden decision-maker.
- TMDB, torrent file listings, and ffprobe are the relevant ground truth.
- Primary journal/UI copy must be understandable without torrent jargon.
- Never weaken filesystem, deletion, networking, secret, or rate-limit
  guardrails to make a prompt easier.
- Never commit `.env`, `data/`, credentials, library names, or download history.

## Development setup

Install Python 3.11, Node.js 22.12 or newer, FFmpeg (including ffprobe), and
ripgrep (`rg`). Clone the repository, then run from its root:

```bash
./scripts/install.sh
./scripts/check.sh
./dev.sh
```

The UI runs on `:3000` and the backend on `:8888`. The installer uses the
hash-locked Python dependencies and `npm ci`. `scripts/check.sh` runs Python
compilation, dependency consistency, backend tests, the production frontend
build, npm audit and release-tree checks. Media tools must be on PATH, or set
`SPARROW_FFMPEG` and `SPARROW_FFPROBE` to their executable paths. Live paid-model
evaluations are opt-in and skipped by default.

### Browser journeys and screenshots

Install Google Chrome, or point `SPARROW_CHROME` at a compatible Chromium
executable with H.264/AAC support. Build the frontend before running:

```sh
tests/browser/ci.sh
```

The runner starts the real backend and built UI with isolated state and
fictional media on `127.0.0.1:8891`. It checks account entry, actual playback,
audio/caption selection, exact requests, household permissions, settings,
Logs, responsive layouts and automated accessibility. It neither starts
acquisition nor makes paid model calls. The port must be free; use
`SPARROW_BROWSER_PORT=8893` alongside a running design preview.

Results, screenshots and the server log are written to the ignored
`tests/browser/artifacts/` directory and uploaded by CI. To refresh the public
gallery as well, run:

```sh
SPARROW_SCREENSHOT_OUT=docs/screenshots tests/browser/ci.sh
```

Review the resulting desktop and phone images before committing. The capture
script checks for the fictional fixture catalogue; never substitute a personal
library. See [the screenshot gallery](docs/screenshots/README.md) for provenance.

Add tests for persistence, recovery, tool failures, and guardrails whenever the
affected code can change an external outcome. Use small generated/mocked
fixtures; do not commit copyrighted media.

## Changes

Keep changes focused and explain:

1. Which user outcome changes.
2. Which agent owns the decision.
3. Which tools provide facts and guardrails.
4. How failure and restart behavior were tested.
5. Whether model/token cost changes.

The project uses the checklist and acceptance tests in [ROADMAP.md](ROADMAP.md)
to mark milestones complete.

The default branch is `main`. Open a focused pull request against it, include
the relevant checks, and attach updated screenshots when a visible flow changes.
Use [the shared design contract](docs/PRODUCT_DESIGN.md) for visual changes.
