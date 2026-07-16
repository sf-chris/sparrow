# Alpha Reliability Patch

Status: implemented (2026-07-17). Deterministic scope guards, staging
isolation, the transfer cap, pause/resume/cancel client control, live
progress persistence, the Library projection, and the three-tab show page
are in place with regression tests (`tests/test_scope_guardrails.py`,
`tests/test_lifecycle_and_projection.py`). Real-Librarian behavioural evals
are opt-in: `SPARROW_RUN_AGENT_EVALS=1 python3 -m pytest tests/evals -q`.

This patch is the immediate priority before continuing the product roadmap. It
addresses the incorrect multi-season download, makes active work visible, and
replaces the title page's wall of operational records with a clear,
grandma-friendly view.

## Problems to solve

### 1. Sparrow exceeded the requested scope

The original request covered season 1 only. The standing Librarian later
interpreted ownership of the show as permission to backfill every missing
episode in seasons 2–4 and created another job.

The current Librarian instructions conflate "this show is in the library" with
"the user wants every available episode." That is the primary cause of the
unexpected downloads.

Related reliability problems make the incident more serious:

- Fetch can enqueue a large number of transfers in one wake without
  backpressure.
- Agent-managed downloads currently share a staging root, so a Media Agent can
  see files belonging to another download.
- Pausing a Sparrow job does not reliably mean pausing its torrent-client
  transfers.
- The meaning and consequences of the current delete action are unclear.

### 2. Pending work is missing from the Library

The Library is currently based on organized inventory. The v3 agent job and
download state is not projected into that view, so an episode can be requested
and downloading without appearing in the Library.

### 3. Activity is difficult to understand

Some journal entries have timestamps, but transfer and execution records do
not provide a consistent sense of when something happened or last changed.
The title page also renders every episode operation at once, producing a long
wall of records instead of explaining the state of the request.

## Implementation order

### 1. Correctness and safety

- Introduce an explicit monitoring mandate for each show. Owning one or more
  episodes must not imply permission to acquire other episodes.
- Enforce request and monitoring scope in the tool layer. The Librarian may
  reason about what should happen, but `spawn_job` must reject work outside the
  user's recorded authority.
- Distinguish useful monitoring modes, including:
  - exact request only;
  - keep current from now on;
  - selected seasons;
  - explicit historical backfill.
- Add a regression fixture reproducing the real failure: season 1 is requested
  and owned, monitoring is off, and the Librarian must not create jobs for
  seasons 2–4.
- Add real-agent behavioural evaluations using fixed library and TMDB states.
  Assert tool calls and outcomes rather than exact journal wording.
- Give each download an isolated staging directory or exact client content
  root. A Media Agent must only be able to inspect and move files belonging to
  its own download.
- Add a deterministic concurrency guardrail so one Fetch Agent wake cannot
  flood the client. The agent still chooses what to acquire next; the tool
  controls resource authority.
- Make pause and resume control both the Sparrow job and its associated client
  transfers.
- Replace the ambiguous delete action with explicit actions and confirmation:
  - **Pause request** — stop agents and transfers without deleting files.
  - **Cancel pending work** — stop unfinished work and remove partial files;
    retain already verified and organized library episodes.
  - Library removal remains a separate operation.

### 2. Accurate visibility

- Persist and broadcast live progress, speed, ETA, transfer status, and the
  time each value was last updated. Do not leave active downloads displayed at
  0% until completion.
- Build the Library from a projection of organized inventory plus active and
  paused v3 jobs/downloads.
- Show an episode moving through plain-language states:

  `Requested -> Queued -> Downloading -> Verifying -> Ready`

- Provide Library filters for **All**, **Ready**, **In progress**, and
  **Needs attention**.
- Use consistent timestamps throughout the journal, execution activity,
  transfers, and request summary. Show friendly relative time by default and
  expose the exact date and time on hover or expansion.
- Mark stale transfer information clearly rather than presenting old data as
  current.

### 3. Title-page cleanup

Split the show page into three focused tabs:

- **Watch** — the default view containing seasons, episodes, readiness, and
  playback.
- **Progress** — the active request, agent journal, grouped transfers, errors,
  and expandable technical evidence.
- **Preferences** — monitoring scope, quality, audio, subtitles, and urgency.

The page must always state the user's contract prominently, for example:

> Requested: Season 1 only  
> Future-season monitoring: Off

The Progress tab should show one compact title-level summary by default:

> Downloading Season 1 · 3 of 8 ready · 42% overall · about 18 minutes
> remaining

Individual episode transfers should be grouped by season and collapsed until
the user expands them. Completed records should collapse automatically, while
failures and decisions requiring attention should be shown first. Raw release
names, hashes, codecs, and similar evidence belong only in an advanced
expansion.

## Regression strategy for the Librarian

The Librarian is an LLM in a persistent tool-use loop, so regression coverage
needs two layers:

1. **Deterministic authority tests.** Tools must make forbidden actions
   impossible regardless of model output. Test no mandate, exact-season,
   keep-current, backfill, and already-active-job cases.
2. **Agent behavioural evaluations.** Run the real Librarian against fixture
   library/TMDB states, preferably multiple times, and verify the operations it
   attempts and the resulting job state. Journal prose is not a stable test
   target.

The real multi-season incident should be retained as a sanitized replay case.
This protects against both a future prompt regression and a model that reaches
an unexpected conclusion.

## Acceptance criteria

This patch is complete when:

- A season-1-only request cannot cause seasons 2–4 to be acquired unless the
  user explicitly enables a monitoring or backfill scope that permits it.
- Concurrent work stays within the configured transfer limit.
- A Media Agent cannot access another download's staging files.
- Pause, resume, and cancel have clear UI copy and matching client behaviour.
- A newly requested episode appears in the Library immediately and advances
  through accurate pending states until it is ready.
- Active progress updates without waiting for a download to reach 100%.
- Every user-visible operation has understandable time context.
- The show page opens on a useful Watch view and no longer displays an
  uncollapsed wall of download records.
- The regression suite contains both hard scope-guard tests and real Librarian
  behavioural fixtures, including the season 1 incident.

## Roadmap impact

Treat this as an alpha reliability gate, not a new long-term milestone. Finish
it before resuming roadmap work: monitoring, playback, subtitles, and remote
household access all depend on trustworthy request scope and legible progress.
