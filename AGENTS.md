# Sparrow — Agent Instructions

Sparrow is a local media autopilot: the user says what they want to watch,
and the system finds it, downloads it, organises it into a clean library, and
keeps curating (filling gaps, upgrading quality) forever.

**Read DESIGN.md first.** It is the authority on architecture and philosophy.
For the current owner-approved target and acceptance criteria also read
`docs/PRODUCT_PLAN.md` and `ROADMAP.md`. The stage 1–6 implementation and measured
validation boundaries are in `docs/IMPLEMENTATION.md`; use `docs/PRODUCT_DESIGN.md`
for the shared interface contract. Stage 7 is excluded from the current work.
The short version: an agent is an LLM in a tool-use loop — nothing else
qualifies. Agents own uncertain discovery/acquisition/curation decisions and
quality review. Predictable subtitle processing runs automatically under resolved
policy; tools establish facts and enforce limits. The setup agent and all
TV/Emby/casting work are parked; do not treat retained TV option notes as active
implementation requirements. Follow the delivery sequence at the top of ROADMAP.md.

The next agentic reliability phase is tracked in
[issue #3](https://github.com/sf-chris/sparrow/issues/3), with the source audit and
reproductions in [docs/AGENTIC_PLAN.md](docs/AGENTIC_PLAN.md). Read that handoff
before changing the runtime or retiring legacy paths. It includes model-evaluation
work and the owner's required cleanup of obsolete docs, issues/dependencies and
dead code; its proposed model profiles are not completed evaluations.

## Architecture (v3 — agentic)

Everything new lives in `backend/agents/`:

- `models.py` — `Job` (a contract against TMDB: wanted episodes, quality
  window, audio, urgency), `AgentSession` (a persistent tool-loop with full
  message history), `JournalEntry`, `Event`, `Spend`.
- `openai_loop.py` — runs a session on an OpenAI model through the same loop
  (Responses API translation at the call boundary).
- `runtime.py` — the loop: wake on event → reason across tool calls →
  hibernate with a trigger or close. Handles persistence, crash repair,
  durable event acknowledgements/tool results, spend tracking and API retries.
- `tools.py` — the tool belt + guardrails. Filesystem jail (staging +
  library only), library deletion only via verified `upgrade_swap` (or
  withdrawing an unverified file the same request itself placed, unchanged),
  indexer rate limits, honest error text the agent can reason about (`ToolError`).
- `prompts.py` — the agents' standing orders. Philosophy → operating rules.
- `service.py` — `AgentService`: event routing, the plumbing poller
  (files_landed / download_stalled / client_recovered / timers), job
  lifecycle, boot recovery. Plumbing makes zero decisions.
- `store.py` — sqlite for jobs/journal/sessions and reasoning reservations;
  markdown memory is scoped to the requesting person.
- `evidence.py` — immutable oversized tool observations, atomic with invocation
  receipts; private session retrieval/listing and serialized storage quotas.
- `ledger.py` — the cost ledger: every model and tool call with its trigger,
  cost and facts; reports, timelines, exports, backfill and a model audit.
- `scout.py`, `acquisition_review.py`, `release_match.py` — code search, judging
  and ranking of releases; the smart model's review of the cheap model's pick.
- `discovery.py` — persistent, scoped Discovery tool loop with inspected title
  proposals. Fast TMDB suggestions remain alongside it; `resolution.py` retains
  the legacy description route.
- `curation.py` — personal subscriptions, versioned authority and changed-fact
  checks that wake the Librarian only for eligible work.
- `accounts.py`, `nodes.py`, `node_executor.py`, `catalogue.py`, `playback.py` —
  household preferences/permissions, durable portable storage and media delivery.
- `subtitles.py`, `subtitle_worker.py` — built-in preparation of a playable
  track; `subtitle_evidence.py` (whole-soundtrack speech evidence on the storage
  node), `subtitle_sync.py` (measured timing and correction) and
  `subtitle_review.py` (page-by-page reviewer tools and approval gate).

### The agents

- **Fetch Agent** (cheap tier with smart review, one session per job): owns a
  job until the library provably matches the spec. The scout (`scout.py`)
  runs the usual searches, reads names, peeks inside packs for the wanted
  files and ranks a short list; the cheap model proposes one row and the
  smart model reviews that compact decision (`acquisition_review.py`) before
  anything downloads. A release must open with the show's title followed
  only by numbering/year/season/quality tags; searches queue for the shared
  indexer allowance (no model turns) and repeats come from a cache. A TV
  transfer downloads only the wanted episodes and their subtitle files, by
  name once the torrent's list is known. Hard searches escalate the session
  to the smart model with the raw search tools. Handles stalls (removing a
  transfer deletes its unfinished files) and reconciles inventory. Woken by
  events; hibernates between.
- **Media Agent** (cheap tier, self-escalates, one session per landed
  download): probes every file with ffprobe, matches durations against TMDB
  runtimes, detects samples/fakes, names and places files, updates
  inventory, and **reports back to the Fetch Agent** — that closed loop is
  what makes "done means spec met" real.
- **Librarian** (cheap tier, personal subscription sessions): reasons over eligible
  aired episodes, gaps and explicitly authorized upgrades. Unchanged idle facts
  cause no model calls; unfinished reviews retain their session and retry timer.
  Tools enforce the current subscription revision and shared standing allowance.

## Key API surfaces

- `GET /api/resolve?q=` — search box → poster cards.
- `POST /api/jobs`, `GET /api/jobs[/{id}]`, nudge/pause/resume/cancel.
- `GET /api/journal`, `GET /api/agent-sessions` — the ops console.
- WebSocket `/ws` events: `journal`, `job_added`, `job_update`,
  `session_update` (plus legacy events).

## Rules

- **Watching is the product; the journal explains the work.** Prioritise
  playback, exact user intent and actionable state. Agents narrate progress in
  plain language. Never let hashes, seeder counts, codecs or release names reach
  a primary surface.
- **Verify reality; never trust names.** TMDB, torrent file listings, and
  ffprobe are the only ground truth.
- **Guardrails live in the tool layer**, not in agent judgment.
- **Resolve preferences centrally.** Admin defaults, personal overrides and
  explicit request choices form a versioned effective contract, within admin
  policy. Pass relevant values to agents and use the same contract in UI/tools.
- Current model defaults: Fetch cheap with smart review and escalation
  (`SPARROW_FETCH_SCOUT=off` keeps it smart throughout), Media cheap with self-escalation,
  Librarian and Discovery cheap with bounded turns and spend. The subtitle agent
  (household switch) is managed by GPT-6-Sol when an OpenAI key is set (Settings →
  Connections, or `OPENAI_API_KEY`), else
  Claude Opus 5.5 at medium effort with prompt caching. Cheap page checkers
  (GPT-6-Luna, GPT-6-Sol second opinion when borderline) read every page; the
  manager reads only what they flag plus audit pages. Sources are tried in order:
  the release's text tracks, its picture (Blu-ray/DVD) tracks read by the cheap
  vision model (only with the switch on, within the title's allowance), an
  archive track for the same episode, then writing. A
  human-made track is verified, never edited (only OCR and timing fixes): a
  failing one is set aside for the next source. Sparrow-written tracks are
  edited through tools that re-measure every change. Checker and manager spend
  share one per-title allowance (the household per-case limit unless
  `SPARROW_SUBTITLE_BUDGET` is set); the design target is under $1 per title.
  Configure with `SPARROW_SUBTITLE_MODEL`, `SPARROW_SUBTITLE_EFFORT`,
  `SPARROW_SUBTITLE_CONTRACTOR`, `SPARROW_SUBTITLE_VERIFIER` and
  `SPARROW_SUBTITLE_BUDGET`. Routine subtitle processing,
  including timing measurement and correction for foreign dialogue, is built in
  and makes no model call; no setup-agent implementation is planned now.
  Keep model roles configurable with `SPARROW_SMART_MODEL` / `SPARROW_CHEAP_MODEL`.
- Agent-managed downloads carry `metadata.agent_managed` — the legacy
  enrich/auto-organize path must skip them (the Media Agent owns landing).

## Legacy (demoted, not deleted)

- The deterministic curator loop is off by default; set
  `SPARROW_LEGACY_CURATOR=1` to re-enable. `release_parser.parse_release_name`
  and the ranking helpers survive only as advisory triage tools
  (`triage_parse`) — their output is never final.
- The executable CWM source, service and routes have been deleted. Existing
  CWM log records remain readable by the storage migration layer; installed data
  is preserved. Learning lives in scoped agent memory notes.
- v2 goals/requests routes still have administrator API consumers and await
  retirement. The unmounted legacy frontend pages/client have been deleted.

## Run

- Dev: `./dev.sh` (backend :8888 + vite :3000)
- Prod: `./start.sh` (builds frontend, serves from backend)
- Env: `ANTHROPIC_API_KEY`, `TMDB_API_KEY` (or set both in Settings).

## Installation handoff

When installing Sparrow for its owner, finish by returning the working browser
address, the one-time setup code and a link that fills that code automatically.
The owner creates their own administrator account and password. This is the
first-account setup step; do not describe a separate "claim" or activation process.

After the server is healthy, retrieve the handoff with:

```sh
docker compose exec -T sparrow python -m backend.agents.setup_info --url http://localhost:8888
```

Use the address the owner will actually open. `localhost` is correct for a local
installation or the owner's SSH tunnel; use the server's reachable LAN address
for a LAN installation. Do not present server-local connectivity as proof that
the owner's device can connect. For a source installation, use
`.venv/bin/python -m backend.agents.setup_info --url <browser-address>`.
Startup logs also show the code until setup is complete. The command reads the
existing code and does not create an account or rotate it. Return this handoff
privately to the owner; household invitations are generated separately in People.

## Issue #3 implementation evidence

Every model and tool call is recorded in the cost ledger
([issue #15](https://github.com/sf-chris/sparrow/issues/15)):
`python -m backend.agents.ledger report|timelines|export|audit|backfill --data <state dir>`
gives cost per title by phase, escalations, searches and waste flags. Judge
acquisition and subtitle changes by a before/after ledger report on the same titles.

[The implementation ledger](docs/agentic-audit/IMPLEMENTATION.md) records current
cleanup dispositions, recovery guarantees, evaluations and outstanding work.
The initial 30-case controlled contract suite is reproducible with
`tests/evals/run_contracts.py`; paid model selection and device acceptance remain
separate gates. New tool invocations must be recorded before effects and results
before notifications; never replay uncertain effects without inspecting receipts.
Large results must use the [evidence archive](docs/agentic-audit/EVIDENCE.md), with
explicit partial previews and retrieval references. Do not silently cap observed
inventories or treat a preview as proof that an item is missing.
