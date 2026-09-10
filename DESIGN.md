# Sparrow — Design Philosophy & System

> This document supersedes all previous design directions, including the Code
> World Model (CWM) and the deterministic curator/planner pipeline. Those
> survive only as *tools* that agents may call — never as decision-makers.

Updated product direction, 2026-09-09: the owner accepted the product review
and added agentic discovery, subtitle/audio synchronisation, mobile use,
accounts and guided self-hosting as core requirements. The owner subsequently
parked all TV/Emby/casting work and the setup agent. Read
[docs/PRODUCT_PLAN.md](docs/PRODUCT_PLAN.md) and [ROADMAP.md](ROADMAP.md) for the
current target and acceptance criteria. The implementation and its measured checks are recorded in
[docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md). Household accounts, browser
playback, personal collection care and built-in subtitle preparation now exist.
Physical Windows and device/provider validation remain explicit release gates;
the external access wizard is deferred with stage 7.

---

## Philosophy

**1. An agent is an LLM in a tool-use loop. Nothing else qualifies.**
An agent, in this codebase, means what Claude Code is: a model with a tool
belt that observes, reasons, acts, checks its own work, and iterates until the
job meets spec — across hours or days, hibernating and waking on events.
Deterministic workers, scoring functions, and "LLM escalation points" are not
agents and must never be presented as such. They are plumbing or tools.

**2. Agents handle judgement; built-in processing handles predictable work.**
Discovery, acquisition and curation retain tool-using agents for uncertain
choices. Subtitle inspection, retrieval, alignment and validation run through
supported automatic operations under the resolved policy. A verification agent
reviews evidence and representative samples, investigates ambiguity and requests
repairs. Reuse selected bundled/vendored components with provenance and tests;
the owner should not have to operate separate subtitle applications. Agents
cannot override measured facts, permissions or resource guardrails.

**3. Verify reality; never trust names.**
Torrent names lie. Filenames lie. The system trusts only: TMDB (what episodes
exist, runtimes, air dates), torrent file listings (what a torrent actually
contains), and ffprobe (what a file actually is — resolution, codec,
duration). Every consequential decision is checked against one of these before
it counts. A 23-minute file cannot be a 58-minute episode. A 40 MB "1080p" is
a fake. `S01.COMPLETE` is season one, and the way you know is by looking
inside it.

**4. The job is a contract against TMDB.**
A user request becomes a spec: exactly these episodes (per TMDB), this quality
window (preferred + minimum), this audio preference, this urgency. A job is
done when the library inventory provably matches the spec — not when a
download finishes, not when a status field flips. Urgency is a first-class
input: "tonight" trades quality for swarm health; "whenever" holds out for the
preferred tier.

**Preference inheritance.** Admin onboarding establishes system defaults;
users see those defaults at first sign-in and may keep or override them in
personal Settings. A shared resolver applies personal/request choices within
admin policy, records the effective values and their sources, and supplies the
same contract to agents, tools and UI. Active work changes only through explicit
intent revisions; current hard limits remain enforceable.

**5. Watching is the product; the journal explains the work.**
Lead with Play, Resume, exact request scope, personal progress and useful
recovery actions. Agents write plain-language progress notes explaining what
they checked, why they are waiting and what happens next. Those notes support
the title and request experience. No hashes, seeders, codecs or release names
on primary surfaces. Existing-media playback and controls must work without a
model call.

**6. Memory is notes, not code.**
Agents read and write plain memory files scoped to the requesting person (per show and across their titles): which query
phrasing worked for this title, which release groups are reliable, which are
fakes, how this tracker behaves. The next job starts with the last job's
lessons in context. This is what the CWM was reaching for — learning as data,
not self-modifying source code.

**7. Persistence with self-awareness, not unbounded thrash.**
Agents are relentless — they do not give up because one search wave missed.
But they can see their own spend (turns, queries, dollars) and are expected to
act like adults: when content genuinely isn't out there, conclude that, write
it down, and hibernate on a trigger (an air date, a timer, new activity) —
never spin forever, never hammer an indexer into rate-limiting the user's IP.

**8. Everyday outcomes first, expert depth behind them.**
Fast search, poster cards and precisely labelled requests with visible profile
defaults. Quality/audio/urgency controls are available when useful. Play,
Resume, subtitle repair and setup must work in phone and desktop browsers.
Agent work is inspectable and normally unattended, with a clear
request for user input when authority or an unresolved choice requires it.

---

## The system

### Discovery Agent and instant title suggestions

The discovery flow is a genuine tool-using agent: interpret a request,
query catalogue and owned media, inspect results, resolve ambiguity and refine
with the user. Return poster cards and structured title/edition, episode scope,
language and monitoring intent. Explicit user action or existing authority
creates a job; search content cannot authorise acquisition. Keep fast direct
title suggestions alongside the richer agent flow.

`discovery.py` implements the new flow on the existing persistent tool runtime;
`resolution.py` retains the legacy direct lookup/description route. The current
runtime enforces cancellation, persistence, cost and tool-access contracts.
The SDK migration decision and remaining live evaluation are recorded in
`docs/IMPLEMENTATION.md`; a package change is conditional on demonstrated parity.

### The Fetch Agent — one session per job

Owns a job from creation until the library matches the spec. Runs on the
smart model tier; wakes on events; hibernates between turns.

Tool belt:

| Tool | Purpose |
|---|---|
| `tmdb.*` | episode lists, runtimes, air dates, alternative/original titles |
| `tpb.search` | raw indexer results — unfiltered |
| `torrent.peek` | fetch a torrent's actual file listing before committing |
| `client.add / status / remove` | the torrent client |
| `inventory.read` | what the library already holds for this show |
| `journal.write` | plain-language reasoning trail, rendered in the UI |
| `memory.read / write` | per-show and global search playbooks |
| `wake.me` | schedule its own next turn; also woken by events |

Behavior — the way a smart human works: search, *read* the results, refine
queries (alt titles, romanizations, tag variants, per-episode probes), peek
inside promising packs to confirm they contain what the spec needs, weigh
downloadability against quality *per the job's urgency*, grab, get woken on
stall → kill and take the runner-up, get woken when files land, reconcile
inventory against spec, and either close the job or journal exactly why it's
waiting and when it will check again ("episodes 9–10 haven't aired — waking
Friday night").

Failure legibility is part of the contract: a dead torrent client is not an
exception to swallow, it's a state to reason about and journal in plain
English ("your download app is off — everything is ready the moment it's
back"), retrying cheaply until the plumbing recovers. Environmental failures
never blacklist candidates.

### The Media Agent — one session per landed download

Woken when files hit staging. Default cheap model tier, escalating itself to
the smart tier when genuinely confused.

Tool belt: `fs.list / probe / move / rename` (jailed — see guardrails),
`tmdb.*` (episode titles, artwork, dates, runtimes), `inventory.write`,
`journal.write`, `memory.read / write`.

Behavior: look at what *actually* arrived. Probe every file; match durations
against TMDB runtimes; detect samples, fakes, and junk; name to library
convention; place; pull episode titles, artwork, dates until the entry is
complete. Then report back to the Fetch Agent: "the pack claimed E01–E10 but
E07 is a corrupt sample — you are not done." That closed loop between the two
agents is what makes "unrelenting until it matches spec" real. On a verified
quality upgrade, it swaps the old file out — the only deletion it is ever
permitted inside the library.

### The Librarian — personal collection-care sessions

Cheap-tier sessions reason over each person's explicitly authorized subscription:
new aired episodes, selected seasons or backfill, with quality upgrades opt-in.
Deterministic checks gather changed metadata and verified collection facts;
unchanged or ineligible work makes no model calls. Tools enforce scope, revisions,
preferences and destination access before creating an acquisition request.
Existing copies are preserved when a better copy is published.

### Plumbing (not agents, not pretending to be)

A torrent-client poller, an event bus that wakes agent sessions
(`download_stalled`, `files_landed`, `client_recovered`, `episode_aired`,
timers), websockets to the UI, storage. Plumbing feeds agents and makes zero
decisions.

### The UI serves the collection and watching experience

- **Home / Discover**: Continue watching and next episodes, plus instant title
  suggestions and agent-assisted discovery with exact request scope.
- **Title**: Play/Resume or the next useful request; actionable episodes;
  audio/subtitle preferences; progress, recovery and journal detail.
- **Activity**: active/blocked requests and their next actions, with sessions,
  usage and execution detail available to inspect.
- **Library**: find and curate owned/followed media, import with correction,
  understand availability and manage copies deliberately.
- **Settings / Sharing**: accounts, preferences, devices, storage and guided
  access; technical diagnostics remain available without dominating normal use.

Phone responsiveness applies throughout. TV/Emby integration, casting and
native TV applications are parked, as is the setup agent. Guided setup and
predictable diagnostics remain core. Browser playback uses shared asset identity,
permissions and personal progress that future clients can reuse. Nodes provide
local processing and evidence.

---

## Guardrails (tool-layer, not agent-judgment)

- **Filesystem jail.** The Media Agent writes only inside staging and its own
  job's destination paths. Library deletions are permitted solely as
  verified upgrade-swaps. An agent's good judgment is not a substitute for a
  seatbelt on `rm`.
- **Visible budgets.** Agents see their own turn/query/dollar spend per job
  and are prompted to hibernate-with-trigger rather than thrash. Indexer
  calls are rate-limited at the tool layer.
- **Peek economics.** Fetching a torrent's file listing requires DHT metadata
  and can be slow on weak swarms. The agent chooses per candidate: healthy
  swarm → peek first; marginal swarm → grab, inspect, abandon if wrong.
- **Model tiering, per agent not per call.** Fetch runs smart (search judgment
  is the hard part). Media runs cheap and self-escalates (median job is
  renaming `S01E05.mkv`). Resolution layer is a single cheap call. Cheap
  where possible, smart where needed.

---

## What this explicitly replaces

- **The CWM** (self-evolving Python strategy file): retired. Learning now
  lives in agent memory notes.
- **The deterministic curator/planner/parser pipeline as decision-maker**:
  demoted. `parse_name`, `rank_candidates`, and query generators remain
  available as triage tools an agent may call on large result sets — their
  output is advisory, never final.
- **Status-machine "done"**: replaced by spec reconciliation. A job is
  complete when inventory matches the TMDB contract, verified, with metadata
  and artwork in place.
