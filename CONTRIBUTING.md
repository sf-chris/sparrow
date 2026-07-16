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

```bash
./scripts/install.sh
./scripts/check.sh
./dev.sh
```

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
