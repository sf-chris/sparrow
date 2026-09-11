# Sparrow Roadmap

> A local media autopilot: ask for something once, then Sparrow owns the
> outcome until the requested media is verified, ready, and pleasant to use.

Updated 2026-09-09 after the owner's acceptance of the product review and
additions covering subtitle synchronisation, agentic discovery/management,
mobile use, accounts and guided self-hosting. TV work was subsequently parked.

The detailed decisions, option comparisons, references and user journeys are in
[the product plan](docs/PRODUCT_PLAN.md). The
[product review](docs/PRODUCT_REVIEW.md) records current defects and missing
capabilities. The [node plan](docs/DISTRIBUTED_NODES_PLAN.md) covers Linux
coordination and Windows/macOS/Linux execution.

Latest owner refinements: ship subtitle processing inside Sparrow and use an
agent for quality review/exceptions; establish admin defaults with personal
inheritance/overrides; park both the setup agent and all TV work, including
Emby integration, compatibility research, casting and native TV applications.

Implementation update, 2026-09-10: the owner authorized stages **1–6** and excluded
**stage 7**. Code, packaging and the shared interface now cover those six stages.
[The implementation record](docs/IMPLEMENTATION.md) separates passing local,
browser and Linux-package checks from pending native Windows, physical-device
and live-provider acceptance. The table below remains the deployment acceptance
contract; code presence alone does not complete its physical Windows gates.

Design integration, 2026-09-11: the approved bright interface and compact Home,
settings, account/security and Logs refinements are now the installed product.
The README and [screenshot gallery](docs/screenshots/README.md) show this direction.
The next phase concentrates on feature reliability and the still-open device,
storage and provider acceptance gates, as recorded in
[the implementation record](docs/IMPLEMENTATION.md#design-integration--11-september-2026).

Agent audit and proposal, 2026-09-11: [the agentic reliability plan](docs/AGENTIC_PLAN.md)
records the actual CWM retirement boundary, current agent capabilities, lessons
from Mogged, proposed intelligence tiers and evaluation-led work packages. It
remains the accepted work plan. [The implementation ledger](docs/agentic-audit/IMPLEMENTATION.md)
records the recovery and cleanup changes now implemented and the remaining gates.
[Issue #3](https://github.com/sf-chris/sparrow/issues/3) consolidates the handoff,
acceptance criteria and required removal of obsolete documents, issues/dependencies
and dead code. Track the next phase there rather than creating competing backlogs.

Gate A and Gate B below retain the repository's historical July completion
record. They do not establish readiness for the new Linux/Windows deployment,
public access or the complete viewing experience. Current review reproductions
identify issues that must be resolved despite those historical checkboxes.

## Current implementation sequence

The stages below are the delivery order. Existing milestone numbers further
down remain backlog references, not a second delivery order; the broad
Milestone 0 spans several stages. This replaces the previous acceptance path.
These are acceptance gates, not calendar estimates. Consult the implementation
record for current evidence and outstanding deployment checks.

| Stage | Deliverable | Done when | Backlog references |
| --- | --- | --- | --- |
| 1. Make the existing product trustworthy | Correct readiness, exact episode scope, ignored settings, pause/cancel, concurrency limits, recovery and missing history. Simplify Home, Library, Activity and title actions around the next useful action. | Reviewed correctness/control defects have regression coverage; missing or unverified files cannot appear ready; cancelled work cannot start stale acquisitions; failed work has a visible recovery action on mobile and desktop. | Milestone 0; product review |
| 2. Establish accounts and preferences | Owner onboarding, local accounts, roles, invitations and personal settings. One effective-settings resolver combines admin defaults, explicit personal overrides and request choices within admin policy. | Two users can inherit, customise and reset preferences; agents and validation receive the same effective contract; users cannot read or change each other's private state; running intent changes only through an explicit revision. | Milestones 0 and 5 |
| 3. Connect Linux to Windows storage | Installable Linux server and Windows node, pairing, durable commands/events, asset identity, safe import/correction and acquisition to the chosen destination. Prove the authenticated video path while building the node. | A clean Windows install pairs without a development environment; a movie and episode survive scans, retries and restarts; interrupted work reconciles; a sleeping node shows unavailable rather than deleted. Real Windows/NTFS checks pass. | Milestone 0; node plan |
| 4. Make the collection watchable | Responsive library and episode views, Play/Resume, audio and existing subtitle-track selection, personal progress, direct play and bounded format fallback. Installable PWA. | The owner imports a Windows-stored movie, plays and seeks from a phone, resumes on desktop, and recovers from a node interruption. A restricted user cannot bypass permissions through media URLs. Existing media plays with the model provider down. | Milestone 2 |
| 5. Deliver subtitles that simply work | Built-in discovery, alignment against actual audio, validation and track preparation using selected bundled/vendored components. Agent review of evidence/samples and exception handling; one-tap repair. | Labelled offset, drift, wrong-cut/episode and already-good fixtures meet measured acceptance thresholds; originals survive; repaired tracks render correctly in supported browsers; mandatory subtitle readiness is reported honestly. | Milestone 3 |
| 6. Complete agentic discovery and collection care | Tool-using Discovery with precise request proposals, stronger Fetch/Media/Librarian flows, visible subscriptions, gap filling, upgrades and recoverable failures. | A description becomes the intended title and exact scope; a followed show gets eligible new episodes without repeated requests; curation respects each user's preferences and authority, preserves good files and spends no tokens on unchanged idle state. | Milestones 0 and 1; curation findings |
| 7. Finish guided self-hosting and sharing | Connections/Sharing flows, concrete DNS and port-forwarding instructions, HTTPS, private-network and supported relay alternatives, recovery, upgrades and backup/restore. | An invited viewer on another network can log in, play, seek and use subtitles at the supplied address; revocation works; blocked inbound access has a tested alternative; restore and upgrade preserve permissions, intent and media mappings. | Milestone 5; node hardening |

**First usable release: stages 1–4.** The owner can install Linux Sparrow,
pair Windows storage, import the existing collection and browse/play/resume on
phone and desktop with accounts and preferences. Existing acquisition agents
remain operational. This is an intermediate release; the complete agreed
authorized implementation also includes stages 5–6. Stage 7 remains deferred.

**Resolve uncertainty early without expanding the first release.** Start a
small labelled subtitle benchmark during the foundation work. After the
preference contract exists, evaluate Discovery and an SDK adapter against real
scoped requests. During the first Windows node slice, prove authenticated
seeking/streaming rather than assuming command connectivity is sufficient.
These evaluations inform stages 3–6; subtitle and SDK experiments do not block
the first import-and-watch journey or require a wholesale agent-runtime rewrite.

**The first implementation slice** fixes readiness and request/control truth:
file evidence and availability, exact episode requests, enforced saved limits,
and pause/cancel revision checks, together with their affected screens and
regression cases from the review. Schema changes include migrations and restart
checks. Finish that reviewable slice before expanding the account/node model.

Mobile responsiveness, accessibility, clear empty/error/offline states, file
preservation and bounded agent costs apply at every stage. Deliver UI and
backend together for each journey. Authentication and permission checks start
in stage 2 and cover each later endpoint; stage 7 completes external access
and diagnostics. Backup/migration safety and installation checks begin when
persistent state and packaging change, rather than waiting until stage 7.

**Parked:** all TV/Emby/casting work and the setup/deployment agent. Retain the
TV option notes in the product plan, but do not investigate or implement them
as dependencies of this sequence. Normal guided setup remains core. Source
expansion, music, native phone apps and an operated relay service remain later
scope. All-platform node support follows the same protocol and platform gates;
Windows is the first required storage-node deployment.

## Status legend

- **Now** — required for the current owner foundation and integration proofs.
- **Next** — part of the complete owner experience, ordered by dependencies.
- **Later** — expansion after the complete owner experience.
- **Parked** — intentionally deferred; do not accidentally pull it into an
  earlier milestone.

Current milestone checkboxes record progress; historical gate checkboxes retain
their original scope. A current milestone is complete only when its acceptance
test passes on the supported deployment, not when its code merely exists.

## North star

The complete Sparrow experience is:

> Request -> acquire -> verify -> enrich -> notify -> play

The dinner scenario is the primary product test: someone asks for a film or TV
show from a phone, Sparrow preserves the exact request, obtains the best
practical first item, checks what arrived, prepares subtitles against its actual
audio, and makes it easy to play in a phone or desktop browser. Resume state follows the
person across devices. Remaining authorised work may continue in the background.
The Linux coordinator and Windows final storage must support this whole journey.

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
5. **Effortless use includes setup.** Guide supported installation, accounts,
   storage and connectivity. Requesting, monitoring, recovering and watching
   must work well from phone and desktop browsers.
6. **Watching is the product; the journal explains the work.** Lead with Play,
   Resume, exact requests and actionable problems. Use ordinary language for
   progress; keep release names, hashes and source trivia in advanced views.
7. **Verify reality; never trust a filename.** "Done" means the files on disk
   satisfy the request contract.
8. **Own the watching experience.** Sparrow has its own web player and watch
   state as target capabilities. TV/Emby integration is parked and does not
   determine the current player or watch-state acceptance gates.
9. **Measure request-to-first-frame.** Breadth matters less than reliably making
   the requested first item watchable quickly.

## Decisions already made

- TPB is sufficient as the first source. More sources come through a Sparrow
  connector contract, not through a mandatory Prowlarr/Torznab architecture.
- Prowlarr/Torznab may eventually exist as optional compatibility bridges.
- Guided setup, mobile responsiveness, accounts/sharing, inherited preferences
  and browser playback are part of the complete current owner experience.
- All TV work is parked, including the installed Emby app, real-server bridges,
  protocol compatibility, casting and a dedicated Sparrow TV app.
- Bundle or vendor selected subtitle-processing components so the owner manages
  one feature; agents review results and handle uncertainty rather than driving
  every predictable processing step.
- The setup agent is parked; normal guided setup and diagnostics remain core.
- Supported direct HTTPS and private-network modes come before operating a
  universal hosted relay service. Relay deployment remains an option for
  otherwise unreachable homes; it must have a tested media path and explicit costs.
- Finding and synchronising subtitles is a signature core feature. A timing
  tool's success or an agent's confidence alone cannot establish a correct track.
- Existing-library import and correction are core collection-management work.
- Discovery becomes a genuine tool-using agent; instant title suggestions remain
  useful. Existing Fetch, Media and Librarian loops already provide agentic work.
- Evaluate a Claude Agent SDK adapter for discovery, preserving durable domain
  state and tool guardrails. SDK migration is conditional on demonstrated parity.
- Video comes first. Music and a mobile listening experience are a later
  product expansion.
- Asynchronous provider batch APIs are a cost optimization for non-urgent work,
  not a dependency of the core acquisition loop.

---

## Gate A — Private usable alpha

**Status: Complete (2026-07-16)**
**Target: achieved**

Historical record for the original deployment; revalidation for the new owner
experience is tracked in Milestone 0 and the node acceptance gates.

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

Historical alpha release record; this does not claim current authentication,
remote sharing, native playback or Windows-node readiness.

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

## Milestone 0 — Reliable, agent-managed owner foundation

**Status: Now; new scope from the September product review**

- [ ] Resolve reviewed readiness, ignored-setting, scope, completed-job,
  abandoned-history, pause/cancel, resource-limit and error-state defects.
- [ ] Introduce accounts, user/role authority and personal preferences with the
  identity work from Milestone 5; protect every API and media path.
- [ ] Add admin-onboarding preference defaults and a brief defaults/customise
  step for invited users; retain discoverable personal settings and reset actions.
- [ ] Resolve server defaults, explicit personal/title overrides and request
  choices in one typed contract constrained by admin policy; retain value sources.
- [ ] Inject relevant effective settings, principal and version into Discovery,
  Fetch, Media, subtitle review and subscription work, using the same contract in
  UI and verification. Keep running job intent stable until explicitly revised.
- [ ] Test inherited-default changes, reset, two-user isolation, hard limits and
  compatible shared media/subtitle needs without destructive preference conflicts.
- [ ] Separate title/episode identity, media edition/file version, location,
  request intent, subscription and user watch state.
- [ ] Prove Linux coordination and Windows node pairing, durable operations,
  capability/evidence reporting and the actual video transport path.
- [ ] Import existing media with preview, uncertain-match correction, distinct
  unmatched identities and rescans that preserve verification evidence.
- [ ] Add a Discovery Agent that searches/refines using tools and returns title
  cards plus exact scope/language/monitoring intent for user-authorised requests.
- [ ] Evaluate the Claude Agent SDK behind an adapter using cancellation,
  restart, cost, restricted-tool and structured-result acceptance checks.
- [ ] Keep the existing management agents operational while proving migration
  parity; add subtitle quality-review sessions over the built-in processing
  pipeline. Retain setup-agent extension points without implementing that agent.
- [ ] Enforce hard reasoning/action budgets and concurrent reservations; persist
  operation identity and revision checks across retries and cancellation.
- [ ] Establish responsive Home, Library, Activity and title flows with explicit
  empty/error/offline states and keyboard-accessible controls.

### Acceptance test

Create the owner, pair a Windows node, import a known movie and preserve it
through two scans and a restart. Submit an ambiguous show description from a
phone, refine the correct edition and exact episode scope, then pause/cancel
with running work and verify no stale action acquires more. Verify permission
boundaries with a second restricted user. Reproduce the Essential correctness
and control defects and demonstrate their corrected behaviour. Missing playback
and subtitle capabilities are completed under Milestones 2 and 3.

---

## Milestone 1 — Ask once: first-class monitoring

**Status: Core; build on the corrected intent model, complete after the first viewing journey**

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

**Status: Next; stage 4 delivers browser playback, stage 5 adds automatic subtitle preparation**

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
- [ ] Verify direct play, remux/transcode fallback, subtitle rendering and resume
  on supported physical phones and desktop browsers using Windows-node media.
- [ ] Keep playback and controls operational when the model provider is down.

Partial-torrent streaming is a later optimization. The initial player begins
when the first item is fully landed and verified.

### Acceptance test

Request episode one from a phone, receive "ready to watch," and play it inside
Sparrow with the correct audio/subtitle controls. Resume as the same user on a
desktop browser, then return to the phone without corrupting progress. Verify
subtitle changes refresh and access revocation holds for media and subtitle
endpoints. Remaining authorised episodes may continue downloading. Interrupt
storage connectivity and verify useful recovery; test with the model service
unavailable. TV compatibility is outside this acceptance gate.

---

## Milestone 3 — Subtitles that simply work

**Status: Next with playback; signature feature, evaluated early**

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
- [ ] Build automatic inspect/search/align/validate/prepare operations inside
  Sparrow using selected pinned/bundled or vendored components; retain upstream
  provenance, required notices, conformance tests and a maintenance/update path.
- [ ] Add a cheap verification agent that reviews evidence and representative
  cue/audio samples, investigates ambiguous outcomes and requests targeted repairs.
  Do not replace media inspection with a model approving tool exit codes.
- [ ] Record measured checks and agent review separately, including sample
  coverage and pending review when the provider is unavailable.
- [ ] Evaluate ffsubsync and alass against the actual audio and labelled fixtures;
  do not equate speech-activity correlation with correct dialogue/episode identity.
- [ ] Associate confidence and timing derivatives with the exact file version,
  edition and audio track; invalidate evidence after replacing the media copy.
- [ ] Preserve originals and good timing; validate proposed changes before
  promotion and distinguish personal offsets from shared-track repairs.
- [ ] Distinguish media playable from mandatory subtitle requirements satisfied;
  retain a deliberate user option to play before subtitle preparation completes.
- [ ] Run alignment near storage where practical, with resource limits; make
  transcription/translation fallbacks explicit and retain provenance.

Routine subtitle processing runs automatically under the resolved policy.
Independent checks and a bounded agent review evaluate the output; ambiguous
matches or failed repairs reopen targeted agent work. Native processing and
playback do not require an agent call for each step. Use audio-capable review
or appropriate sample/transcript tools when actual dialogue must be examined.

### Acceptance test

For labelled good, offset, drifting, edited-cut, wrong-episode, missing, forced,
SDH and multilingual fixtures, select or prepare a suitable track and measure
the result against independently checked cues. Reject low-confidence mismatches,
preserve good originals, and test actual rendering/seeking in supported phone
and desktop browsers. A
one-tap out-of-sync report must reopen the right asset/track task and produce a
recoverable result. See the product plan for tool candidates and limits.

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

## Milestone 5 — Accounts, guided self-hosting and household access

**Status: Core; identity starts with Milestone 0, remote access follows verified permission boundaries**

- [ ] Add owner bootstrap, local accounts, roles/library permissions, personal
  history/preferences, recovery, expiring invitations and revocable device sessions.
- [ ] Enforce user authority on APIs, WebSockets and every media/subtitle/HLS
  endpoint; distinguish user identities and node credentials.
- [ ] Support passkeys or optional OIDC as additional sign-in methods without
  requiring an external identity service for a normal self-hosted installation.
- [ ] Provide a validated Linux installation and Windows pairing flow with
  real access/probe checks, service restart behaviour and preserved media.
- [ ] Build a mobile-usable Connections/Sharing page for local, private-network,
  direct-domain and relay access modes.
- [ ] Detect candidate LAN/public addresses and likely routing obstacles; show
  evidence and uncertainty instead of assuming an egress IP is reachable.
- [ ] Generate exact DNS and port-forwarding instructions for the selected
  hostname/host, including dynamic-address updates and verified IPv6 handling.
- [ ] Manage Caddy HTTPS or validate an existing reverse proxy; verify DNS,
  certificate trust/renewal and reachability independently.
- [ ] Offer a private-network route and a supported relay configuration for
  blocked inbound access; measure media throughput and state any relay costs.
- [ ] Deliver guided setup and deterministic diagnostic/configuration tools
  with useful errors, concrete changes and recovery; leave an extension point
  for the parked setup/support agent.
- [ ] Verify external login, playback, seeking, subtitles and session revocation
  from outside the LAN; preserve a local recovery path.
- [ ] Add in-app and supported push notifications with authenticated player
  deep links; document browser/device limitations.
- [ ] Test backup/restore, upgrades, credential recovery and account isolation.

An operated Sparrow relay service and general autonomous router/cloud
administration are later options. Basic guided self-hosting is core. An
outbound node/control tunnel alone does not establish a video path, and free
web tunnels must not be assumed suitable for sustained media traffic. See the
product plan for verified access options and their limits.

### Acceptance test

An owner completes supported setup, pairs storage, invites a restricted viewer
and supplies a working HTTPS address. The viewer signs in from another network,
watches and seeks with subtitles, and has separate resume state. Revoke that
viewer/device and verify access is removed. Exercise an unreachable home-network
case and demonstrate an honest diagnosis plus a working supported alternative.

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

- [ ] Setup/support agent over the normal diagnostic/configuration tools.
- [ ] All TV playback work: installed Emby/Samsung evaluation, real-server
  integration, protocol compatibility, casting and native TV applications.
  Retained options and future tests are in the product plan; none is an active
  acceptance criterion or prerequisite.

- [ ] Import selected intent/history from existing automation tools.
- [ ] Universal one-click deployment across arbitrary routers/cloud providers.
- [ ] Operate a hosted Sparrow relay service beyond the supported self-hosted
  or private-network configurations.
- [ ] Native iOS/Android apps, if the PWA cannot meet playback requirements.
- [ ] Agent-drafted connector experiment described above.

Validated Linux setup, guided network diagnosis and existing-library import are
now core milestones above. The remaining expansion work should not displace
the complete owner viewing/curation experience.

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

1. Preserve files, permissions, exact user intent and truthful state.
2. Complete the request/import-to-first-frame journey on phone and desktop.
3. Satisfy language/subtitle requirements and make failures recoverable.
4. Make supported setup, household sharing and recurring curation effortless.
5. Bound resource use and reduce recurring model cost without weakening judgment.
6. Expand sources, devices and media types after the core experience is proven.

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
- [ ] A revalidated Linux/Windows owner deployment with actual media playback.
- [ ] Authenticated, reproducible household remote access.
- [ ] Durable first-class monitoring semantics.
- [ ] Sparrow-native playback, subtitles, remote access, and music.

Retain useful v3 groundwork while correcting the reviewed contracts and
completing the viewing experience. Historical completion and source-code
presence are not substitutes for the current acceptance tests.
