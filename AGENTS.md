# Sparrow — Agent Instructions

Sparrow is a local media autopilot: the user says what they want to watch,
and the system finds it, downloads it, organises it into a clean library, and
keeps curating (filling gaps, upgrading quality) forever.

**Read DESIGN.md first.** It is the authority on architecture and philosophy.
The short version: an agent is an LLM in a tool-use loop — nothing else
qualifies. Intelligence lives in the loop; deterministic code lives in the
tool belt and decides nothing.

## Architecture (v3 — agentic)

Everything new lives in `backend/agents/`:

- `models.py` — `Job` (a contract against TMDB: wanted episodes, quality
  window, audio, urgency), `AgentSession` (a persistent tool-loop with full
  message history), `JournalEntry`, `Event`, `Spend`.
- `runtime.py` — the loop: wake on event → reason across tool calls →
  hibernate with a trigger or close. Handles persistence, crash repair,
  history trimming, spend tracking, API retries.
- `tools.py` — the tool belt + guardrails. Filesystem jail (staging +
  library only), library deletion only via verified `upgrade_swap`, indexer
  rate limits, honest error text the agent can reason about (`ToolError`).
- `prompts.py` — the agents' standing orders. Philosophy → operating rules.
- `service.py` — `AgentService`: event routing, the plumbing poller
  (files_landed / download_stalled / client_recovered / timers), job
  lifecycle, boot recovery. Plumbing makes zero decisions.
- `store.py` — sqlite for jobs/journal/sessions; plain markdown memory
  notes in `data/memory/` (global.md + shows/<tmdb_id>.md).
- `resolution.py` — the search box (NOT an agent): TMDB direct, one cheap
  LLM call for fuzzy descriptions, poster cards out.

### The agents

- **Fetch Agent** (smart tier, one session per job): owns a job until the
  library provably matches the spec. Searches, reads results, refines,
  peeks inside packs, weighs downloadability vs quality per urgency, grabs,
  handles stalls, reconciles inventory. Woken by events; hibernates between.
- **Media Agent** (cheap tier, self-escalates, one session per landed
  download): probes every file with ffprobe, matches durations against TMDB
  runtimes, detects samples/fakes, names and places files, updates
  inventory, and **reports back to the Fetch Agent** — that closed loop is
  what makes "done means spec met" real.
- **Librarian** (cheap tier, standing session): watches air dates for owned
  shows and spawns jobs for new episodes, creates upgrade jobs, flags gaps.

## Key API surfaces

- `GET /api/resolve?q=` — search box → poster cards.
- `POST /api/jobs`, `GET /api/jobs[/{id}]`, nudge/pause/resume/cancel.
- `GET /api/journal`, `GET /api/agent-sessions` — the ops console.
- WebSocket `/ws` events: `journal`, `job_added`, `job_update`,
  `session_update` (plus legacy events).

## Rules

- **The journal is the product.** Agents narrate in plain language; the UI
  renders it verbatim. Never let hashes, seeder counts, codecs, or release
  names reach a primary surface.
- **Verify reality; never trust names.** TMDB, torrent file listings, and
  ffprobe are the only ground truth.
- **Guardrails live in the tool layer**, not in agent judgment.
- Model tiering is per agent, not per call: Fetch smart, Media cheap with
  self-escalation, Librarian cheap, resolution one cheap call. Override with
  `SPARROW_SMART_MODEL` / `SPARROW_CHEAP_MODEL`.
- Agent-managed downloads carry `metadata.agent_managed` — the legacy
  enrich/auto-organize path must skip them (the Media Agent owns landing).

## Legacy (demoted, not deleted)

- The deterministic curator loop is off by default; set
  `SPARROW_LEGACY_CURATOR=1` to re-enable. `release_parser.parse_release_name`
  and the ranking helpers survive only as advisory triage tools
  (`triage_parse`) — their output is never final.
- The CWM (`data/cwm/`) is retired; learning lives in agent memory notes.
- v2 goals/requests routes remain for the legacy pages.

## Run

- Dev: `./dev.sh` (backend :8888 + vite :3000)
- Prod: `./start.sh` (builds frontend, serves from backend)
- Env: `ANTHROPIC_API_KEY`, `TMDB_API_KEY` (or set both in Settings).
