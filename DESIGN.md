# Sparrow — Design Philosophy & System

> This document supersedes all previous design directions, including the Code
> World Model (CWM) and the deterministic curator/planner pipeline. Those
> survive only as *tools* that agents may call — never as decision-makers.

---

## Philosophy

**1. An agent is an LLM in a tool-use loop. Nothing else qualifies.**
An agent, in this codebase, means what Claude Code is: a model with a tool
belt that observes, reasons, acts, checks its own work, and iterates until the
job meets spec — across hours or days, hibernating and waking on events.
Deterministic workers, scoring functions, and "LLM escalation points" are not
agents and must never be presented as such. They are plumbing or tools.

**2. Intelligence lives in the loop. Code lives in the tool belt.**
Regex parsers, ranking heuristics, and query generators are cheap triage tools
an agent may choose to use on 200 raw results. They never decide what to
download or where a file belongs. When heuristics and agent judgment disagree,
the agent wins.

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

**5. The journal is the product.**
Every agent writes its reasoning in plain language as it works — what it
searched, what it rejected and why, what it's waiting on, when it will wake.
The UI does not summarize logs; it renders the journal. A non-technical user
reading the Show page should feel like they're reading a competent human
assistant's notes. No hashes, seeders, codecs, or release names on primary
surfaces.

**6. Memory is notes, not code.**
Agents read and write plain memory files (per show, and global): which query
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

**8. Grandma-first surfaces, expert depth behind them.**
One search box. Poster cards. One-click get with profile defaults; quality/
audio/urgency knobs exist but are never required. Everything the agents do is
inspectable, but nothing they do requires supervision.

---

## The system

### Resolution layer (not an agent)

The search box hits TMDB directly. Fuzzy human descriptions — "the show where
the teacher cooks meth" — go through a single cheap LLM call with a TMDB
search tool and come back as poster cards (→ Breaking Bad). Disambiguation
(Office US vs UK, Shōgun 2024 vs 1980) happens here, visually, by showing the
options. The user picks a card, optionally adjusts the contract knobs, and
confirms. That confirmation creates a **job**.

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

### The Librarian — a standing session over the whole library

Recurring pass, cheap tier: watch TMDB air dates for owned shows and spawn
jobs when new episodes air (own season 4 → S04E11 airs → it appears without
being asked); turn below-preference episodes into upgrade jobs; chase metadata
and artwork gaps; flag duplicates and stragglers. Subscriptions are what make
Sparrow a library, not a download tool.

### Plumbing (not agents, not pretending to be)

A torrent-client poller, an event bus that wakes agent sessions
(`download_stalled`, `files_landed`, `client_recovered`, `episode_aired`,
timers), websockets to the UI, storage. Plumbing feeds agents and makes zero
decisions.

### The UI is a window into the agents

- **Discover**: search box → poster cards → one-click get. Cards for shows
  with active jobs say so.
- **Show page**: the job's workspace. "Sparrow's agent is working on this" —
  live journal, current transfers in plain words, per-season/episode coverage,
  next-wake countdown, and a "check now" nudge.
- **Activity**: the operations console across all sessions — every job, every
  journal, live.
- **Library**: compact grid with per-season/episode completeness; drill-down;
  a single "scan folder" to reconcile disk reality.
- **Settings → Advanced**: raw surfaces for experts. Never required.

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
