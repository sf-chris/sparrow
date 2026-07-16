# Sparrow Roadmap

> A local media autopilot: ask for something once, then Sparrow owns the
> outcome until the requested media is verified, ready, and pleasant to use.

This roadmap records the product decisions made so far and puts them in a
dependency order. It is deliberately split into two immediate release gates:

1. Make Sparrow useful for its owner on a private deployment.
2. Turn that working build into a safe, reproducible open-source alpha.

Everything after those gates improves the product without holding up the first
real use.

## Committed delivery order

Milestone numbers remain stable so issues and prior discussion keep their
meaning. The execution order is now:

1. **Milestone 1 — first-class monitoring**
2. **Milestone 2 — Sparrow playback**
3. **Milestone 3 — subtitles that simply work**
4. **Milestone 5 — remote household access and hosted relay**

Milestone 4 (automatic source expansion) and Milestone 6 (music) are explicitly
deferred until that sequence is complete. TPB remains sufficient for the
near-term product work; remote household use is more important than broadening
the source ecosystem first.

## Status legend

- **Now** — required for today's private or open-source alpha gate.
- **Next** — the first product milestone after the alpha gates.
- **Later** — committed direction, ordered after the foundations it needs.
- **Parked** — intentionally deferred; do not accidentally pull it into an
  earlier milestone.

Checkboxes are the source of truth for progress. A milestone is complete only
when its acceptance test passes, not when its code merely exists.

## North star

The complete Sparrow experience is:

> Request -> acquire -> verify -> enrich -> notify -> play

The dinner scenario is the primary product test: someone asks for a film or TV
show from a phone, Sparrow obtains the best practical first item quickly, checks
what actually arrived, prepares correct subtitles, sends a "ready to watch"
notification, and opens directly into Sparrow's player. The job may continue in
the background to complete the season or improve quality.

The equivalent music scenario is: ask for an artist at the gym, receive a
notification when music is ready, and listen in Sparrow without managing files.

The long-term product is therefore a personal media service, not merely an AI
wrapper around an existing automation stack.

## Product principles

1. **The agent owns uncertain decisions.** An agent is a persistent LLM
   tool-use loop. Parsers, rankings, timers, and workflows can provide evidence
   or wake an agent; they do not silently replace its judgment.
2. **Deterministic code owns facts and safety.** TMDB, torrent file listings,
   ffprobe, filesystem jails, rate limits, and verified swaps establish reality
   and enforce guardrails.
3. **Ask once.** A request can become a durable mandate. The user should not
   have to remember release dates, retry failed searches, or request every new
   season.
4. **No idle intelligence bill.** A monitored show with no changed facts should
   consume no model tokens.
5. **Grandma-friendly usage first.** Initial installation may be technical, but
   requesting, monitoring, recovering, and playing media must not be.
6. **The journal is the product.** Primary surfaces explain progress in ordinary
   language. Release names, hashes, codecs, and source trivia stay in advanced
   views.
7. **Verify reality; never trust a filename.** "Done" means the files on disk
   satisfy the request contract.
8. **Own the watching experience.** Jellyfin and Emby are not product
   dependencies. Sparrow will have its own player and watch state.
9. **Measure request-to-first-frame.** Breadth matters less than reliably making
   the requested first item watchable quickly.

## Decisions already made

- TPB is sufficient as the first source. More sources come through a Sparrow
  connector contract, not through a mandatory Prowlarr/Torznab architecture.
- Prowlarr/Torznab may eventually exist as optional compatibility bridges.
- Setup can remain technical for the first private and open-source alphas;
  everyday use cannot.
- A separately hosted relay for remote access and setup is acceptable later,
  but is not part of the first release.
- First-class subtitles are a signature feature, but do not block the first
  usable alpha.
- Existing-library import is useful and explicitly deferred.
- Video comes first. Music and a mobile listening experience are a later
  product expansion.
- Asynchronous provider batch APIs are a cost optimization for non-urgent work,
  not a dependency of the core acquisition loop.

---

## Gate A — Private usable alpha

**Status: Complete (2026-07-16)**
**Target: achieved**

Evidence: the configured macOS host passes all 10 required doctor checks, runs
under a persistent LaunchAgent, and serves the production UI over the trusted
LAN. A real Big Buck Bunny request completed through Fetch, Transmission,
landing, Media verification, organization, inventory, and final journal; it
also recovered across a forced service restart. The single-episode TV contract
is covered by the same ffprobe-backed inventory/completion path in the
confidence suite.

### Outcome

Sparrow runs persistently on the chosen private machine, can be opened from a
phone on the same trusted network, and completes one real request end-to-end.
The initial assumption is the current Mac, its existing torrent client, and
same-Wi-Fi access. A different host changes the service wrapper, not the
acceptance test.

### Scope

- [x] Add a preflight/doctor check for Python, Node, ffprobe, configured paths,
  write permissions, API keys, and torrent-client connectivity.
- [x] Fix every blocker found by the doctor. In the current environment,
  ffprobe is already a known missing runtime dependency.
- [x] Normalize and expand user paths consistently; prove staging and library
  directories are inside the intended filesystem boundaries.
- [x] Validate the configured TMDB and model credentials without displaying
  their secret values.
- [x] Prove title resolution from the phone-facing UI.
- [x] Prove a movie request from resolution through Fetch Agent, client grab,
  landing, Media Agent inspection, organization, inventory, and final report.
- [x] Prove a TV request can make the first requested episode ready without
  waiting for an entire season pack.
- [x] Prove an interrupted/restarted process recovers its jobs and sessions.
- [x] Make the final "ready" state unmistakable in the journal and job UI.
- [x] Run Sparrow as a persistent service that restarts after machine reboot.
- [x] Bind only to the intended private interface, restrict browser origins,
  and confirm the service is not exposed to the public internet.
- [x] Hard-disable the retired CWM code-execution endpoints in this deployment.
- [x] Record the working configuration and a short recovery/backup procedure.

### Acceptance test

From a phone on the private network:

1. Search for a small public-domain/test movie and a TV title.
2. Request the movie and one episode.
3. Watch the journal progress without needing to interpret torrent internals.
4. Confirm the files were inspected and placed in the correct library paths.
5. Restart Sparrow mid-job and confirm it resumes safely.
6. Confirm the UI reaches an honest "ready" state only after verification.

For this gate, playback may use the existing local playback route or external
player. Sparrow's own player, push notifications, subscriptions, and signature
subtitle flow are later milestones.

### Stop rule

Do not proceed to release polish while the acquisition and verification loop is
broken. A reliable ugly alpha is more valuable than a polished repository that
cannot complete a request.

---

## Gate B — Open-source v0.1.0-alpha

**Status: Complete (2026-07-16)**
**Target: achieved (2026-07-16)**

Evidence: a source-only temporary checkout with no `.env`, state, virtualenv,
frontend build, or node modules installed successfully, passed the full suite,
and served the onboarding UI. The release tree scan passes and npm reports zero
known vulnerabilities. The private Git repository is initialized and the
release is tagged `v0.1.0-alpha.1`.

### Outcome

A new contributor can clone Sparrow, understand what it is, install it on a
documented supported platform, complete onboarding, and reproduce the private
alpha flow without inheriting secrets or an unsafe default configuration.

This is an honest alpha, not the finished Grandma installer.

### Security and privacy release blockers

- [x] Remove or compile-gate the retired public CWM execution/read/write routes;
  they must not be reachable in a normal build.
- [x] Default the backend to loopback, disable production auto-reload, and make
  LAN binding an explicit operator choice.
- [x] Replace wildcard CORS with configured trusted origins.
- [x] Make secrets write-only through the API: return `configured: true/false`
  or masked metadata, never stored credential values.
- [x] Confirm all filesystem operations remain jailed to staging and library
  roots and deletion remains limited to verified upgrade swaps.
- [x] Add a clear warning that public-internet exposure is unsupported until
  authentication exists.
- [x] Run a secret scan over the release tree (no local Git history exists yet).
- [x] Ensure sample configuration and fixtures contain no personal paths,
  credentials, library metadata, or download history.

### Reproducible installation

- [x] Document supported versions of Python, Node, ffmpeg/ffprobe, and supported
  torrent clients.
- [x] Make the install/start path fail early with helpful dependency errors.
- [x] Pin or lock dependencies sufficiently for a repeatable alpha build.
- [x] Document one tested macOS path and one tested Linux path, or explicitly
  mark the untested platform.
- [x] Add a one-command container/Compose install if it can be validated today;
  otherwise track it as the first beta packaging task rather than shipping an
  untested container.
- [x] Verify a clean checkout builds the frontend and starts the backend.

### Open-source project surface

- [x] Add a README with the promise, screenshots, architecture summary,
  prerequisites, quick start, limitations, and troubleshooting.
- [x] Choose and add a LICENSE.
- [x] Add SECURITY.md with private vulnerability-reporting instructions.
- [x] Add CONTRIBUTING.md and a small development/test guide.
- [x] Add a code of conduct and issue/feature-request templates.
- [x] Link DESIGN.md prominently and state the agent/tool architecture rules.
- [x] Add CHANGELOG.md and version metadata for `v0.1.0-alpha.1`.
- [x] Create the `v0.1.0-alpha.1` Git tag after the GitHub repository exists.
- [x] Clearly distinguish the v3 agentic product from demoted legacy screens.
- [x] State lawful-use and operator-responsibility expectations plainly.

### Minimum confidence suite

- [x] CI: Python syntax/import check.
- [x] CI: frontend type/build check.
- [x] Tests: job/session/store persistence and restart repair.
- [x] Tests: filesystem jail, upgrade-swap deletion guard, and secret masking.
- [x] Tests: Fetch-to-Media report closes or continues a job correctly.
- [x] Tests: a small mocked acquisition happy path and one stalled-download path.
- [x] Manual: clean-checkout startup plus the configured-host Gate A smoke test.

### Acceptance test

On a clean machine or clean account:

1. Follow only the README.
2. Run the doctor and complete onboarding.
3. Start Sparrow without development reload or wildcard exposure.
4. Complete the mocked smoke test and one permitted real acquisition.
5. Restart and recover cleanly.
6. Verify no secrets or private user data exist in the release tree.

---

## Milestone 1 — Ask once: first-class monitoring

**Status: Next**

### Product behavior

- "Download this when it becomes available."
- "Keep this show current."
- "Get every new season of this show."
- "Prefer 1080p with these audio/subtitle requirements."
- "Stop monitoring this" or "pause until next season."

Monitoring is a first-class domain model and UI surface, not merely a sentence
in the Librarian prompt.

### Architecture

- [ ] Add a durable `Subscription` or `Mandate` contract linked to a TMDB title,
  with scope, quality window, languages, urgency, notification policy, and state.
- [ ] Allow future/unreleased and currently unowned titles, not only shows
  already visible in the library.
- [ ] Add a deterministic catalog watcher that observes TMDB air-date changes
  and inventory deltas and emits facts. It makes no acquisition decision.
- [ ] Let the Librarian turn changed facts into jobs or update an existing job.
- [ ] Merge or extend work for a show instead of rejecting useful new episodes
  because one show-level job already exists.
- [ ] Add upcoming, monitored, paused, attention-needed, and last-checked views.
- [ ] Add notification events for ready, needs-help, paused, and failed states.
- [ ] Add calendar jitter, backoff, provider health, and crash-safe wakeups.

### Compiled agency: intelligence without an endless bill

The agent can make a considered decision once and persist a bounded decision
lease. Plumbing may execute the lease while its assumptions still hold. New or
contradictory facts invalidate it and wake the agent.

Examples:

- A standing mandate says to obtain every aired episode matching the quality
  contract. A watcher can detect a newly aired episode for free, but the Fetch
  Agent still judges candidates and owns the result.
- A decision lease can remember that a healthy source should be retried after a
  stated time. It cannot approve an uninspected file or bypass guardrails.
- An unchanged subscription costs zero tokens. The whole library is not sent to
  a model on every timer tick.

### Cost controls

- [ ] Wake agents from changed facts and events, not constant LLM polling.
- [ ] Persist compact decisions, failures, and evidence so retries do not repeat
  all reasoning from scratch.
- [ ] Use the cheap model for routine Librarian work and let it explicitly
  escalate novel or high-risk cases.
- [ ] Coalesce library deltas into scheduled audits instead of revisiting every
  title independently.
- [ ] Add per-job and household spend visibility, budgets, and graceful
  hibernation when a limit is reached.
- [ ] Keep tool results compact while preserving the evidence the agent needs.

### Optional asynchronous reasoning queue

Keep a provider-neutral `ReasoningQueue` behind the normal agent interface.
Use it only when nobody is waiting: repeated next-episode failures, non-urgent
upgrades, library gap audits, or large metadata/subtitle ambiguity queues.

The right batching shape is one batch job containing many independent small
cases, each with a stable ID—not one giant prompt containing the entire
library. Each case receives only its contract, relevant history/configuration,
candidate evidence, and source health.

Example fallback:

1. A new episode airs and the normal Fetch session cannot choose safely.
2. Sparrow collects the TPB results, inspected evidence, job contract, prior
   failures, and relevant preferences.
3. It queues an asynchronous low-priority reasoning case and hibernates.
4. On completion, the Fetch Agent wakes and revalidates the recommended
   candidate through current source data and tool guardrails before grabbing.
5. A failed or late batch never corrupts the job; Sparrow backs off or returns
   to synchronous reasoning.

OpenAI's Batch API currently documents discounted asynchronous processing with
a completion window of up to 24 hours, which fits this class of work. It does
not fit the dinner scenario, so Sparrow must never require it for urgent jobs:
<https://developers.openai.com/api/docs/guides/batch>

### Acceptance test

Subscribe to an unreleased episode, stop Sparrow for several days, restart it
after the air date, and observe Sparrow create/extend the correct job, obtain and
verify the episode, notify the household, and return to zero-token idle state.

---

## Milestone 2 — Ready for dinner: Sparrow playback

**Status: Queued second, after monitoring is reliable**

- [ ] Build a mobile-responsive Sparrow library and title/episode view.
- [ ] Add direct play for compatible media.
- [ ] Add an ffmpeg-backed HLS/transcode fallback only when the client needs it.
- [ ] Add watch progress, resume, watched state, audio-track selection, and
  subtitle selection.
- [ ] Make "urgent" jobs optimize for the first playable item: an individual
  first episode, a fast practical encode, or prioritized first-file download
  when the client and torrent support it.
- [ ] Mark the first requested item ready while the containing job continues.
- [ ] Add ready notifications that deep-link into Sparrow's player.
- [ ] Add household profiles and sensible shared/private watch state.
- [ ] Package the web app as an installable PWA before committing to native
  mobile clients.

Partial-torrent streaming is a later optimization. The initial player begins
when the first item is fully landed and verified.

### Acceptance test

Request episode one from a phone, receive "ready to watch," tap it, and reach the
first frame inside Sparrow with the correct audio/subtitle controls. Remaining
episodes may continue downloading in the background.

---

## Milestone 3 — Subtitles that simply work

**Status: Queued third; signature feature**

Subtitle requirements become part of the media contract: language, full versus
forced, hearing-impaired preference, and synchronization confidence.

- [ ] Inspect and identify embedded subtitle tracks during Media Agent probing.
- [ ] Fetch external subtitles from pluggable providers when required tracks
  are absent or bad.
- [ ] Detect wrong title/episode/language, constant offset, progressive drift,
  poor coverage, broken encoding, overlaps, and unreasonable reading speed.
- [ ] Repair offset and drift locally where confidence is high.
- [ ] Use speech alignment or transcription only when ordinary subtitle sources
  fail.
- [ ] Support translation as a final fallback, retaining provenance.
- [ ] Store subtitle provenance and confidence next to the media inventory.
- [ ] Let the user report "wrong" or "out of sync" in one tap; reopen the media
  task with that evidence.
- [ ] Integrate the best track automatically into Sparrow's player while keeping
  manual selection available.

Most timing analysis should be local signal processing. The Media Agent reasons
about ambiguity and chooses recovery actions; deterministic checks measure the
result.

### Acceptance test

For a fixture set containing good, offset, drifting, wrong-episode, and missing
subtitles, Sparrow selects or produces the correct-language track, rejects bad
tracks, and starts playback in sync without manual file handling.

---

## Milestone 4 — Sources that feel automatic

**Status: Deferred until Milestones 1, 2, 3, and 5 are complete**

TPB remains the first connector. Sparrow sources expose evidence through a
small contract:

- `health()` — can this source currently be used?
- `search(intent)` — return candidates and source evidence for a job contract.
- `inspect(candidate)` — reveal the file listing and other verifiable facts.
- `acquire(candidate)` — hand the selected candidate to the configured client.

Connectors do not return an authoritative winner. The Fetch Agent compares the
evidence against the job and urgency.

- [ ] Extract TPB behind the connector contract and use it as the reference
  implementation.
- [ ] Add connector conformance tests, capability metadata, rate limits, timeout
  behavior, and honest errors.
- [ ] Default Grandma mode to "Sources: Automatic" with only a plain-language
  health summary.
- [ ] Put source ordering, credentials, diagnostics, and raw evidence in
  Advanced Settings.
- [ ] Allow a conversation such as "add my X source; here are the credentials"
  to invoke a safe credential/configuration tool and then verify health.
- [ ] Let the Fetch Agent request another already-installed source when current
  evidence is insufficient.
- [ ] Add signed, versioned provider packages or isolated sidecars before
  accepting third-party connectors broadly.
- [ ] Consider optional Prowlarr/Torznab bridges for users who already have them;
  do not make them Sparrow's internal abstraction.

### What "agent-created connectors" means

This is a parked experiment, not an MVP feature. Much later, an operator could
ask an agent to draft or update a connector for a site/API, run it through the
same conformance and security tests, inspect/approve it, and then save it as a
normal reusable connector. It does **not** mean an agent writes and executes
fresh scraping code during every search, and generated code is never trusted by
default.

---

## Milestone 5 — Remote household and hosted relay

**Status: Queued fourth, after subtitles and the required authentication work**

- [ ] Add authentication, passkeys, household invitations, sessions, and audit
  history before any supported internet exposure.
- [ ] Design a small independently hosted relay for discovery/bootstrap and
  encrypted peer-to-peer connectivity where direct access is unavailable.
- [ ] Keep media and credentials on the home server; the relay should not become
  a central media host.
- [ ] Add QR-assisted phone setup and connection diagnostics.
- [ ] Add reliable push notifications with player deep links.
- [ ] Threat-model relay compromise, account recovery, malicious connectors,
  and household authorization.

The relay improves Grandma setup later. It is not needed to make Grandma usage
good on a manually configured first deployment.

---

## Milestone 6 — Music at the gym

**Status: Deferred with source expansion until the core video experience is complete**

Music gets its own specialist contract and verification semantics rather than
being squeezed through episode logic.

- [ ] Resolve artists, releases, editions, and tracks through MusicBrainz-class
  catalog identities.
- [ ] Verify audio using tags, duration, track position, and acoustic
  fingerprints where appropriate.
- [ ] Add artist/album/discography mandates, explicit-content policy, preferred
  edition, lossless/lossy quality, artwork, and lyrics.
- [ ] Build gapless playback, queues, playlists, background audio, casting, and
  mobile-friendly controls.
- [ ] Add offline device caching after the web/PWA listening experience is
  dependable.
- [ ] Consider OpenSubsonic compatibility as an optional client bridge, not the
  core product experience.

### Acceptance test

From a phone at the gym, request an artist, receive a ready notification, and
play a verified album continuously in Sparrow without touching files or tags.

---

## Parked migrations and convenience work

- [ ] Validate and publish a one-command Docker/Compose beta deployment.
- [ ] Import and verify an existing media library.
- [ ] Import selected intent/history from existing automation tools.
- [ ] Full one-click installer and automatic home-network discovery.
- [ ] Native iOS/Android apps, if the PWA cannot meet playback requirements.
- [ ] Agent-drafted connector experiment described above.

These are valuable, but none should displace acquisition correctness,
monitoring, playback, subtitles, or source reliability.

## Cross-cutting workstreams

### Reliability and evaluation

- Maintain a fixture library of ambiguous titles, season packs, samples, wrong
  episodes, stalled downloads, corrupt files, and subtitle failures.
- Replay recorded tool evidence against prompt/model changes.
- Track request-to-first-result, request-to-first-frame, verified success rate,
  manual interventions, retries, token cost, and bytes wasted.
- Add migrations and recovery tests for every persistent contract.

### Model and provider independence

- Keep smart/cheap model roles configurable per agent.
- Define model capabilities Sparrow needs instead of scattering provider names
  through product logic.
- Keep synchronous and asynchronous reasoning behind replaceable interfaces.
- Persist enough evidence that a session can resume with a different compatible
  provider.

### Security

- Treat source output, filenames, subtitles, metadata, and connector text as
  untrusted input.
- Never expose credentials to a model unless a narrowly scoped tool requires a
  specific secret operation; prefer tool-side secret use.
- Keep acquisition, filesystem, execution, networking, and deletion guardrails
  in tools.
- Require authentication before supported remote access.

### User experience

- Every failure must produce a plain-language next step or an automatic retry.
- Grandma mode shows outcomes, not infrastructure.
- Advanced mode exposes evidence, source health, sessions, costs, and controls
  without leaking them into the primary journal.

## Priority rule

When roadmap items compete, choose in this order:

1. Make a real request reach a verified ready state.
2. Prevent unsafe behavior, data loss, or secret exposure.
3. Reduce manual intervention and improve recovery.
4. Reduce request-to-first-frame.
5. Reduce recurring model cost without weakening judgment.
6. Expand sources and media types.
7. Improve installation convenience.

## Current foundation

The repository already contains substantial v3 groundwork:

- [x] Persistent jobs, sessions, journal entries, events, and spend records.
- [x] A tool-loop runtime with hibernation, retry, trimming, and crash repair.
- [x] Fetch Agent, Media Agent, and standing Librarian roles.
- [x] Tool-layer filesystem and deletion guardrails.
- [x] TPB-backed search/grab path and torrent-client integration.
- [x] ffprobe-based media evidence tooling.
- [x] Resolution, job, journal, session, and WebSocket API surfaces.
- [x] A frontend for search, jobs, journal/activity, and settings.
- [ ] A proven private end-to-end deployment.
- [ ] A safe and reproducible public alpha.
- [ ] Durable first-class monitoring semantics.
- [ ] Sparrow-native playback, subtitles, remote access, and music.

The immediate job is not to redesign this foundation. It is to prove it, remove
the dangerous release edges, publish an honest alpha, and then work down this
file one acceptance-tested milestone at a time.
