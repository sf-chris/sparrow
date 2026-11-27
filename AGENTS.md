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
policy; tools establish facts and enforce limits. Casting from the phone and
opt-in Sparrow Connect are current work
([issue #38](https://github.com/sf-chris/sparrow/issues/38)); the setup agent,
Emby and native TV apps stay parked, so don't treat retained TV option notes as
active implementation requirements. Follow the delivery sequence at the top of ROADMAP.md.

The agentic reliability phase, [issue #3](https://github.com/sf-chris/sparrow/issues/3),
closed on 26 November 2026. Its audit is [docs/AGENTIC_PLAN.md](docs/AGENTIC_PLAN.md);
what it delivered and left out is in
[the implementation ledger](docs/agentic-audit/IMPLEMENTATION.md).

## Architecture (v3 — agentic)

Everything new lives in `backend/agents/`:

- `models.py` — `Job` (a contract against TMDB: wanted episodes, quality
  window, audio, urgency), `AgentSession` (a persistent tool-loop with full
  message history), `JournalEntry`, `Event`, `Spend`.
- `glm_loop.py` — runs a session on Z.ai's GLM models (Chat Completions
  translation at the call boundary, preserved reasoning, JSON answers checked
  against their schema).
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
  cost, facts and profile, plus every guardrail that acted; reports, timelines,
  exports, backfill and a model audit.
- `profiles.py` — one versioned profile per agent role: model, the effort
  actually sent, call/output bounds and prompt/tool versions.
  `python -m backend.agents.profiles` lists them.
- `escalation.py` — the escalated Fetch search's per-wave allowance.
- `scout.py`, `acquisition_review.py`, `release_match.py` — code search, judging
  and ranking of releases; the smart model's review of the cheap model's pick.
- `discovery.py` — persistent, scoped Discovery tool loop with inspected title
  proposals. Fast direct TMDB suggestions remain alongside it; descriptions go
  to Discovery, never to a one-shot model call.
- `curation.py` — personal subscriptions, versioned authority and changed-fact
  checks that wake the Librarian only for eligible work.
- `accounts.py`, `nodes.py`, `node_executor.py`, `catalogue.py`, `playback.py` —
  household preferences/permissions, durable portable storage and media delivery.
  Each playback session also has a signed cast link (`/api/v1/cast/…`) that a
  TV can fetch without a sign-in; it ends with the session, the sign-in that
  started it or the viewer's access. Its converted stream carries the chosen
  subtitle so AirPlay shows it.
- `connect.py`, `acme.py` — Sparrow Connect on this server (Settings →
  Connections), off until an administrator turns it on. It registers with the
  shared service, gets its own certificate for `<id>.h.streamsparrow.com` by a
  DNS proof that Connect publishes (the key never leaves this server), serves
  HTTPS on `SPARROW_CONNECT_PORT`, and checks in so Connect calls back and
  verifies it before pointing the name here. Off forgets it everywhere. The
  shared service itself is `connect/` (FastAPI + Caddy), deployed separately;
  no video passes through it. Homes that can't open a port: issue #37.
- `computers.py` — each computer's jobs (Storage, Downloads, Subtitles; Playback
  follows Storage), remembered per computer and sent to it on every check-in,
  and their health in plain words with a fix. Placement and the Computers page
  read the same answer.
- `placement.py`, `moves.py` — where a request is kept (Automatic, pools, a
  person's storage or one disk) and, separately, which computer downloads it,
  pinned when it starts with a plain reason; verified copy-then-remove moves of
  a title between disks and computers, including moving a finished request from
  the computer that downloaded it to where it's kept.
- `node_update.py`, `release.py` — Sparrow Node updating itself, like spawnd: each
  check-in carries the current version and whether to take it (Update by itself,
  on by default, or Update); the node fetches `/api/v1/node/package.tar.gz`,
  proves it starts, swaps `app/` keeping `app.previous/`, restarts in place and
  rolls back when the new one can't start or reach the server within five
  minutes (`update.json` records where it is). `release.py` labels a version with
  its commit and date (`SPARROW_COMMIT`, `SPARROW_COMMIT_DATE` at image build).
- `folders.py`, `node_package.py`, `node_install/` — browsing and making folders
  inside a computer's own places (server or paired computer); the one-line
  setup command that installs, pairs and starts Sparrow Node, whose package is
  this server's code plus httpx. Its folders are then chosen from Sparrow.
- `subtitles.py`, `subtitle_worker.py` — built-in preparation of a playable
  track; `subtitle_evidence.py` (whole-soundtrack speech evidence on the storage
  node), `subtitle_sync.py` (measured timing and correction) and
  `subtitle_review.py` (page-by-page reviewer tools and approval gate).
- `speech_setup.py`, `speech_device.py` — the Subtitles job's speech engine. Speech
  work runs on the best computer with Subtitles on and ready (a working NVIDIA
  card first, then the one holding the file, then this server); another computer
  gets the soundtrack decoded where the file is (`subtitle_audio`) over the
  transfer path. A paired computer sets the engine up itself when the job is on
  (uv environment pinned from `speech_requirements.txt`, CUDA libraries with a
  card, the model copied from this server via `/api/v1/node/models`). CUDA is
  used when it works and set aside for a week when it fails; the Windows
  installer stays on its bundled processor engine.

### The agents

- **Fetch Agent** (cheap tier with smart review, one session per job): owns a
  job until the library provably matches the spec. The scout (`scout.py`)
  runs the usual searches, reads names, peeks inside packs for the wanted
  files and ranks a short list; the cheap model proposes one row and the
  smart model reviews that compact decision (`acquisition_review.py`) before
  anything downloads, unless code finds nothing in doubt (exact match, wanted
  files in the real listing, attributes known; skips are ledger rows). Episode
  numbering disputes are settled from TMDB episode titles in pack listings, and
  a veto only over unverified quality/audio downloads the copy once for the
  Media Agent to check. A release must open with the show's title followed
  only by numbering/year/season/quality tags; searches queue for the shared
  indexer allowance (no model turns) and repeats come from a cache. A TV
  transfer downloads only the wanted episodes and their subtitle files, by
  name once the torrent's list is known. Hard searches escalate the session
  to the smart model with the raw search tools, in waves with a small allowance
  each: a new wave needs a genuinely new candidate or a recorded identity
  conflict, and a request's repeated searches come from the cache. Handles
  stalls (removing a transfer deletes its unfinished files) and reconciles
  inventory. A copy left by an ended request is taken over by a new request
  for it (resuming what was fetched) and checked by a Media session of its own;
  a live request's copy is never shared.
  An ended request's leftover transfers are cleared a day after it ends, once
  not seeding (`transfer_control.release_finished`); library files are never touched. Woken by events; hibernates between.
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

- `GET /api/resolve?q=` — direct TMDB lookup → annotated poster cards (no
  model call; the product's search box uses `/api/v1/suggest` and Discovery).
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
  Librarian and Discovery cheap with bounded turns and spend. Cheap is
  `glm-5.3-flash`, smart is `glm-5.3` (Z.ai key in Settings → Connections or
  `ZAI_API_KEY`). The subtitle agent (household switch) is managed by `glm-5.3`
  at high effort. Cheap page checkers (`glm-5.3-flash`, `glm-5.3` second opinion
  when borderline) read every page; the manager reads only what they flag plus
  audit pages. Sources are tried in order:
  the release's text tracks, its picture (Blu-ray/DVD) tracks read by the cheap
  vision model (only with the switch on, within the title's allowance), an
  archive track for the same episode, then writing. A
  human-made track is verified, never edited (only OCR and timing fixes): a
  failing one is set aside for the next source. Ads and release credits
  ("www.…", "Subtitles by …") are the one exception: code shortlists them,
  the cheap model confirms, and those whole captions are deleted
  (`subtitle_promos.py`). Sparrow-written tracks are
  edited through tools that re-measure every change. Checker and manager spend
  share one per-title allowance (the household per-case limit unless
  `SPARROW_SUBTITLE_BUDGET` is set); the design target is under $1 per title.
  Configure with `SPARROW_SUBTITLE_MODEL`, `SPARROW_SUBTITLE_EFFORT`,
  `SPARROW_SUBTITLE_CONTRACTOR`, `SPARROW_SUBTITLE_VERIFIER` and
  `SPARROW_SUBTITLE_BUDGET`. Routine subtitle processing,
  including timing measurement and correction for foreign dialogue, is built in
  and makes no model call; no setup-agent implementation is planned now.
  Keep model roles configurable with `SPARROW_SMART_MODEL` / `SPARROW_CHEAP_MODEL`.
  GLM's own default effort is max, so every call sends one (low, high or max).
  The benchmark against the previous setup is
  `docs/agentic-audit/GLM_BENCHMARK_2026-11-25.md`.
  These defaults are the role profiles in `profiles.py`; sessions and ledger
  rows record the profile id and version they ran under. `SPARROW_PROFILES` (JSON per
  role) overrides fields for evaluations. Escalated Fetch searching gets $0.02
  of smart-model calls per wave, up to three waves; two note-only turns make
  later timer wakes back off. Spend counts per request or subscription across
  every session and profile, so neither a new profile nor a new session resets
  an allowance.
- Agent-managed downloads carry `metadata.agent_managed` — the legacy download
  poller and its metadata enrichment must skip them (the Media Agent owns landing).

## Legacy (retired; history still loads)

- The deterministic curator, coverage planner, v2 request planner, release
  decision engine, old search/suggestion routes, auto-organiser, filename
  rescan and every one-shot model call they made have been deleted, as have the
  executable CWM and the old frontend. `SPARROW_LEGACY_CURATOR` no longer
  exists. Saved CWM logs, requests/goals and activity still migrate and load;
  installed data is preserved. Learning lives in scoped agent memory notes.
- `release_parser.parse_release_name` survives only as a regex first guess for
  the scout, the import preview and the advisory `triage_parse` tool — its
  output is never final. `services/apibay.py` is the index query the Fetch
  tools use.
- Remaining non-`/api/v1` administrator routes in `backend/main.py` (config,
  health, downloads, library, metadata, logs, activity and the v3 jobs/journal
  console) have no product caller; the legacy download poller still tracks
  manually added, non-agent transfers. Retire them with their owning migration;
  `docs/agentic-audit/IMPLEMENTATION.md` records each disposition.

## Run

- Dev: `./dev.sh` (backend :8888 + vite :3000)
- Prod: `./start.sh` (builds frontend, serves from backend)
- Env: `ZAI_API_KEY`, `TMDB_API_KEY` (or set both in Settings).

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

## Implementation evidence

Every model and tool call is recorded in the cost ledger
([issue #15](https://github.com/sf-chris/sparrow/issues/15)):
`python -m backend.agents.ledger report|timelines|export|audit|backfill|tag --data <state dir> [--batch NAME]`
gives cost per title by phase, escalations, searches, each pick's review and what
came of it, and waste flags. Administrators read the same at `GET /api/ledger` (and
`/api/ledger/export`); Settings › Logs lists any title or recent run that is
over its target (search median ≤ 2¢, p90 ≤ 10¢, escalation ≤ 15%; subtitles < $1).
Judge acquisition and subtitle changes by a before/after ledger report on the same
titles: `tests/evals/search_bench.py` replays fixed title sets offline and runs the
live search-only benchmark (see `tests/evals/README.md`).

[The implementation ledger](docs/agentic-audit/IMPLEMENTATION.md) records current
cleanup dispositions, recovery guarantees, evaluations and outstanding work.
The initial 30-case controlled contract suite is reproducible with
`tests/evals/run_contracts.py`; paid model selection and device acceptance remain
separate gates. New tool invocations must be recorded before effects and results
before notifications; never replay uncertain effects without inspecting receipts.
Large results must use the [evidence archive](docs/agentic-audit/EVIDENCE.md), with
explicit partial previews and retrieval references. Do not silently cap observed
inventories or treat a preview as proof that an item is missing.
