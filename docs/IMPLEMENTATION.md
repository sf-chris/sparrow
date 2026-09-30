# Implementation record

Owner instruction: implement roadmap stages 1–6, work systematically, test the
results and finish with a cohesive product design. Stage 7 (guided external
DNS/HTTPS/sharing), TV/Emby/casting and the setup agent are excluded.

## Implemented product foundation

| Stage | Implementation | Validation boundary |
| --- | --- | --- |
| 1. Trustworthy requests | Verified file availability, exact episode scope, revision fences, real transfer controls, global limits, visible recovery/history, strict provider failures | Regression tests exercise cancellations, stale responses, retry/restart, settings and file preservation |
| 2. Household accounts | Owner onboarding, invitations, roles/storage scope, central inherited preferences, explicit request overrides, session revocation and password changes | HTTP tests and actual Chrome household flows; no externally hosted deployment claim |
| 3. Portable storage | Outbound pairing and credential rotation, durable commands/results, safe publication, import/correction, Linux container, Windows setup/service/installer workflow | Linux container and portable protocol/media tests pass; Windows FFmpeg binaries compile; native installer/service execution and physical Windows remain pending |
| 4. Watching | Responsive collection/player, authenticated ranges, audio/captions, conversion, personal progress, PWA assets/install guidance | Real Chrome and generated-media HTTP checks, Linux container restart/resume; physical phone/Safari/iOS validation remains pending |
| 5. Subtitle care | Included/local/provider candidates, FFsubsync timing, independent local speech samples, durable preparation and agent review, repair/upload, personal delay, mandatory readiness | Five labelled English speech cases reproduce successfully; review loop tested with controlled model responses; no live provider or translated-caption quality claim |
| 6. Discovery and care | Tool-using conversational Discovery, personal subscriptions, aired scope, gap filling, explicit upgrades, private memory and bounded reasoning | Tool-loop evidence, authority, revision, upgrade and zero-idle-call tests; no paid live-model quality evaluation |

The shared visual contract is [PRODUCT_DESIGN.md](PRODUCT_DESIGN.md). Installation
and release commands are in [INSTALLATION.md](INSTALLATION.md). This is an alpha
implementation, not a claim that every roadmap deployment acceptance gate passed.
The foundation and bright redesign were merged into `main` on 10 September 2026.
The later design follow-up and integration checks are recorded below. No Windows
installer was published; real acquisition and paid model calls were not started
by these validation runs.

## Measured checks

- Original baseline: 42 tests, 40 passed and two opt-in live-model checks skipped.
- Foundation expansion: 54 tests, 52 passed and two skipped.
- Latest repository checks: **118 tests, 116 passed and two opt-in live-model
  checks skipped**. Python compilation, dependency consistency, frontend production
  build and the required release-tree checks pass. Ruff's undefined-name and
  syntax checks pass across backend, tests and packaging.
- Actual FFmpeg fixtures test direct and suffix/ranged media reads, selected audio,
  embedded captions, browser conversion/seek, per-person progress and access
  revocation. Node tests cover durable replay, missed acknowledgements, interrupted
  transfers, publication provenance and refusal to overwrite existing media.
- `tests/package_check.py` passed against the actual Linux image: owner bootstrap,
  import, authenticated range reads, a converted segment, persistent login and
  resume after container restart, PWA assets, and loading the bundled local model.
- `tests/browser/check.cjs` completed the real Chrome viewing flow and exact
  episode/pause behavior with no page errors or failed API responses.
- `tests/browser/accessibility.cjs` found no WCAG A/AA violations across ten
  screens and the request dialog, with no overflow at 360, 390, 768 and 1440 px.
- `tests/browser/household.cjs` passed invitation/join, inherited personal settings,
  password change, permission revocation, subscription editing, subtitle repair
  failure and honest mandatory-caption readiness. Five additional mobile states
  had zero automated accessibility violations.
- `tests/subtitle_benchmark.py` reproduces the checked-in LibriSpeech fixtures:
  already-correct captions stay unchanged; a seven-second offset and 4.2% drift
  are corrected; a changed cut and unrelated caption text are rejected. Metrics,
  independent speech-match evidence and labelled timing residuals are in
  `product-validation/subtitle-benchmark.json`. This is English speech with known
  labels, not a general film/multilingual accuracy claim. Fixture attribution and
  the CC BY 4.0 licence are in `tests/fixtures/subtitles/ATTRIBUTION.md`.
- The Windows FFmpeg/ffprobe build produced x86-64 PE executables from pinned
  sources. That proves compilation, not execution on NTFS or Windows services.
- The node setup's scrollable form and fixed action area were checked at 620×740
  and 500×640 using Linux Tk under Xvfb. The last field remained reachable and
  the primary action remained visible. Native Windows DPI/font checks remain open.
- Frontend audit has reported zero advisories. Production Python dependencies
  are resolved for supported platform markers and hash-locked in requirements.lock.
- Controlled provider HTTP checks verify title/episode/language/style matching,
  three-candidate limits, account quota errors, download-host credential isolation,
  redirect refusal and the two-megabyte download limit. These complement the
  local speech benchmark; no live OpenSubtitles account was used.
- Final browser refinements cover keyboard containment after a dialog changes
  to its success state, accessible phone dialog actions, subscription loading
  states, and readable player language names. Successful household logins no
  longer consume the shared IP's failed-login allowance; regression checks retain
  protection against actual failed attempts.

Evidence screenshots and JSON results are under `docs/product-validation`.
Automated accessibility checks complement visual inspection; they do not replace
manual screen-reader or physical-device acceptance.

## Administrator setup refinement

First-run copy now says **Set up your Sparrow server** and **Create administrator
account**. The setup field explains that its code comes from the installation
agent or startup logs. Startup logs print the existing code while no administrator
exists; `python -m backend.agents.setup_info --url <browser-address>` returns the
code and a link that fills it automatically. The command reads existing state
without creating an account or regenerating credentials, and suppresses the code
after setup. Installation handoff instructions are explicit in `AGENTS.md`.

The browser reads the code from the URL fragment and removes it from the address
bar. Opening the link leaves account creation to the owner. Checks cover fresh
navigation, an already-open setup tab, the normal first-account flow, private
local retrieval and suppression after an administrator exists. The running Linux
instance was rebuilt and updated with its existing data volume preserved.

## Picture house redesign — 10 September 2026

The owner's visual references prompted a new identity across the active product:
midnight blue, ivory and citron; DM Sans and Instrument Serif; an original
folded-bird mark and projection-frame artwork. Entry forms use a split desktop
layout and compact phone header. A persistent desktop rail, collection feature,
poster grid, discovery prompts, titles, player, settings, dialogs and empty/error
states share the new tokens. Fonts and art ship inside the application.

Passwords accept 8–1,024 characters without character-class requirements. A shared
show/hide control supports password-manager autocomplete. Account API coverage
checks eight-character owner/join/change flows, seven-character rejection,
existing authentication and revocation of other sessions after a change.

The browser checks cover setup-link prefill, password visibility and length,
editable discovery prompts and reload, loading-error recovery and empty states.
The visual review corrected a cramped phone filter and stopped an initial load
failure from masquerading as an empty filtered collection. Progress bars expose
proper accessible values. `tests/browser/design.cjs` records four setup layouts
and ten further entry/discovery/empty/error layouts. The existing viewing,
household and accessibility journeys also exercise the new UI. Evidence files
are linked from `product-validation/README.md`; physical-device and live-provider
limits below remain unchanged.

The running Linux instance was rebuilt and redeployed with its existing data
volume preserved. Its sign-in screen and bundled artwork/fonts pass live Chrome
checks at 390px and 1440px with no overflow or automated accessibility violations.
The container is healthy and the running backend accepts eight-character
passwords. No owner account was created by the validation check.

## Engineering decisions and limits

The existing persistent tool runtime remains the agent boundary. Discovery and
collection care now perform genuine model/tool loops; subtitle processing does
not depend on a model, while its quality-review role does. An SDK migration is
not required to obtain tool use and would still need Sparrow's durable session,
permission, job-revision and node contracts. Provider adaptation remains small;
this turn validates the existing runtime with controlled responses, not a live
head-to-head SDK/model evaluation. No reasoning key is configured here.

Reasoning uses step limits and persistent conservative cost reservations before
calls, including possible retries. Uncertain/interrupted calls retain an
allowance. Dollar totals depend on the model-rate table and are estimates rather
than the provider's final invoice. Media conversion is bounded to two producers
per node with free-space checks and cache retention; subtitle work is serialized
per node with an isolated, timed worker.

The existing scheduled Sonnet 5 price increase was removed after checking
[Anthropic's current price table](https://platform.claude.com/docs/en/about-claude/pricing)
on 2026-09-10. Historical usage entries retain the rates originally applied.

Local migration snapshots precede schema generation changes. SQLite is the
established source of truth, JSON mirrors are atomic, malformed legacy state
stops migration, and rescans/corrections retain file identity and personal history.
Windows setup runs the service as LocalService with access granted to its unique
service SID for the approved folders. The workflow still needs to be run on a
Windows host; no remote Windows address or connection has been supplied.

Subtitle preparation now defaults on, with optional paid sync checking off.
Household/personal/request preferences use the shared resolver; imports and landed
downloads retain that effective policy. Available and sync-checked tracks have
distinct labels, and title/episode/player actions can get, check or fix subtitles.
Basic preparation preserves source timings without ASR or a model call.

Translated text tracks reach a bounded semantic review loop. The reviewer quotes
matching source-language phrases; tools locate them in independently timestamped
speech and measure onset/end differences across distributed samples. Cross-language
lexical equality is not scored as a failed translation. At least three distributed
matches and a passing measured timing gate are required for approval. Image
subtitles still need a text alternative. OpenSubtitles authentication, quotas and
its real response contract still require a configured-account check before deployment.

The remaining release gates are native Windows installer/service/NTFS execution,
the owner's Windows-to-phone journey, physical mobile/Safari checks, and live
subtitle-provider/model-quality evaluation. The code and CI workflows make those
checks concrete and reproducible; they are not marked passed without evidence.


## Bright visual reimagining — 10 September 2026

The owner rejected the initial dark cinematic proposal and requested a brighter,
more whimsical interface with a public landing page and smaller typography and
controls. The same `design/sparrow-reimagined` branch now uses light paper, plum,
lilac, mint and pink; bundled DM Sans; and original SVG bird/television and doodle
illustrations. The rejected WebP artwork and Archivo asset were removed.

The public root introduces Sparrow; sign-in is a deliberate action at `/login`.
Unconfigured servers link to `/setup`, and setup-code/invitation links still go
directly to their form. Account entry is compact, and sign-out returns to the
landing page. Home, library, discovery, title, activity, player settings,
preferences, defaults, server settings, people, storage/import, and all dialogs
and states use the new shared system. Video retains a dark viewing surface.

The functional improvements from the previous iteration remain: persistent
library filters/sorting, contextual title return links, keyboard discovery tabs,
assisted drafts, and season filtering. Scoped local backdrop permissions and
the Vite proxy's preserved Host header remain necessary integration fixes.

Validation evidence for the current interface is recorded in
[reimagined-validation](reimagined-validation/README.md). The browser tests now
exercise the public landing before account entry and its back navigation,
alongside the existing playback, request, household, storage and accessibility
journeys. The fixture server uses original geometric covers for fictional test
titles; production continues to use real catalogue images.

Existing local work is preserved. The same isolated backend on 8891 and Vite
preview on 3000 serve this branch; the existing installation on 8888 was not
redeployed. No new runtime dependency or paid acquisition/model call was added.
Physical-device, Windows and live-provider release gates remain unchanged.


## Personal cinema and operational history — 10 September 2026

The owner’s follow-ups are implemented under the current
[interface contract](PRODUCT_DESIGN.md). The bright palette, small controls and public landing page
remain. Home uses a short greeting, compact horizontal Continue watching cards,
then library posters. The owner review removed the oversized playable feature,
decorative page-header lines and excess space below navigation. Activity leads
with title/request progress instead of metric cards.

Settings no longer repeats administration shortcuts or browser-session rows.
Account & security contains a compact password row opening a focused dialog,
current-browser identification and session revocation. Sign out sits below the settings navigation on desktop and
above the settings content on phones; it immediately returns to the public root.
Discover is the single search entry in navigation; Ctrl/Cmd+K still opens it.
Playback help explains the optional browser-format conversion and that the
original file remains unchanged.

A dedicated Logs API/page records request state, download start/stall/completion
and recovery, import/matching, measured storage availability, and playback and
subtitle preparation/review failures and recovery. Fixed summaries and scoped
context avoid copying raw exceptions, paths, credentials or release names into
the feed. Current account/library permissions are applied before queries/counts;
administrators additionally see system events and expandable identifiers.
Successful sign-ins and routine polling do not create rows. Recording a transfer
recovery does not cause an extra reasoning wake.

The SQLite history uses the `operational-history-2` migration generation, with a
pre-change snapshot through the existing migration mechanism. It keeps at most
50,000 events / 90 days and begins collecting new changes; there is no invented
historical backfill. Repeats group in five-minute windows. Time, category,
severity and title/request filters persist in the URL, while a fixed event-id
snapshot keeps new arrivals from shifting pagination. Refresh opens the latest
first page; retention can expire old records during very long browsing sessions.

Repository checks pass with **118 tests: 116 passed, two opt-in live-model tests
skipped**, using the available FFmpeg/ffprobe binaries. Compilation, dependency
consistency, the production frontend build, npm audit and release-tree checks
pass. Targeted coverage checks scope/revocation, repeat grouping, filtered
pagination through concurrent arrivals and restart, state transitions and
playback/subtitle recovery. The browser evidence and screenshots are in
[follow-up-validation](follow-up-validation/README.md).

[NEARBY_DISCOVERY.md](NEARBY_DISCOVERY.md) documents a separate, executable mDNS
prototype in the server/node layer. Controlled tests and an actual loopback-only
zeroconf exchange pass. The prototype is opt-in, limits service types/interface/
duration, returns untrusted candidates, and cannot pair devices or mount storage.
It adds no normal-runtime dependency or LAN scan. An authenticated chooser and
physical LAN/NAS/Windows validation remain release work. This work did not
redeploy the existing installation or resume stage 7 or TV/client work.

## Design integration — 11 September 2026

The approved bright design and personal-cinema follow-ups are integrated into
the normal application routes. The Linux installation was rebuilt from this
source and updated with the existing named volume. A private stopped-state
backup and the previous image were retained before replacement. Before/after
database fingerprints confirmed unchanged accounts, browser sessions, settings,
requests, collection mappings and watch state. SQLite integrity checks pass;
the operational-history migration also created its normal local snapshot.

The README now introduces the product with current desktop/phone captures,
complete clone/install instructions, an explicit alpha status and links to
contribution, licensing and the remaining feature work. The
[public gallery](screenshots/README.md) contains 12 captures from the real
production frontend with fictional fixture media. The existing AGPL-3.0-or-later
license is retained; three accidental shell-banner lines were removed from its
file. No release tag or Windows installer was published.

Checks performed for this integration:

- `scripts/check.sh`: **118 tests, 116 passed, two opt-in live-model checks
  skipped**; compilation, Python dependency consistency, frontend build, npm
  audit (zero advisories) and release-tree checks passed.
- Isolated Chrome journeys passed owner entry, playback/audio/captions/seeking,
  exact episode requests and pause, household access and recovery, public entry,
  settings/security and Logs. The redesign suite passed **55 layouts and 11
  behaviors**; the follow-up suite passed **40 layouts**, with zero reported
  automated accessibility violations, overflow or page errors.
- The Linux image passed `tests/package_check.py`: import, authenticated media
  ranges, converted segments, login/resume across restart, PWA assets and loading
  its bundled speech model.
- The updated installation passed live Chrome welcome/sign-in checks at **390
  and 1440px**, with no page errors, overflow or automated WCAG A/AA violations.
  This was checked from the server, not from a physical phone on another network.

Browser runs now create isolated state, reject an occupied fixture port and
write results/server logs into ignored `tests/browser/artifacts/`, which CI
uploads. They support a separate port for an existing preview. Documentation
capture is reproducible and opt-in for checked-in files. The playback test
accepts both native HLS and MediaSource delivery instead of assuming a `blob:`
URL. Release checks fail if ripgrep is unavailable, and CI installs it explicitly.

The next phase is feature-quality work: native Windows storage and recovery,
physical mobile/Safari playback, and live subtitle/provider/model evaluations.
Nearby discovery remains an opt-in prototype. Stage 7, TV/casting, music and
the setup agent remain deferred. The product's accepted visual direction is
established; open platform/provider gates above remain open.

## Agent recovery and retirement — 11 September 2026

Issue #3 implementation began with the two reproduced failures and a consumer
inventory. Failed collection reviews now retain their session and retry timer,
separately record observed/successfully reviewed evidence, and share a standing
subscription allowance across replacement sessions. Missing-provider evidence
from earlier versions is not mistaken for successful review.

Agent events/deliveries and tool invocations/results are persisted. Delivery is
acknowledged with its saved context, tool results precede observer notifications,
and restart repair uses recorded results while describing uncertain effects
honestly. Discovery/subtitle failure timers survive; completed unchanged work
still costs zero model calls. Node acquisition/publication receipts remain the
authority for external effects. Cancellation/revision fences apply to late results.

The executable CWM, unreachable legacy frontend graph and its unused assets
were removed. `clsx`, `aiofiles`, `aiosqlite`, `beautifulsoup4`, `lxml` and the
Beautiful Soup dependency `soupsieve` were removed from the relevant manifests
and lockfiles. Retained versions were unchanged.
Current artwork, fixture attribution, installed data and historical storage
readers are preserved. Two superseded design documents were consolidated into
the interface contract and removed; historical source links are pinned.

Detailed evidence and outstanding P1–P9 scope are in
[the implementation ledger](agentic-audit/IMPLEMENTATION.md). This is not parent
issue completion or a live model/provider/device acceptance claim.

## Retrievable agent evidence — 11 September 2026

Large tool observations now persist with their invocation receipts and expose
bounded, session-private retrieval. Agents can recover full observations after a
restart and rediscover oversized evidence references after context loss. Four
local row caps in Fetch search/file listings, Discovery collection matching and
Librarian candidates were removed. The archive enforces explicit payload quotas
and reports unavailable evidence without assuming an external effect failed.

[The evidence contract](agentic-audit/EVIDENCE.md) describes storage, privacy,
migration, crash checks and retained limitations. This is the next P4 slice after
merged PR #5; provider profiles, conversation compaction, model-quality evaluation
and the rest of issue #3 remain open.

## Guided administrator setup — 25 September 2026

Administrator onboarding now continues from household defaults into a persistent
server setup flow: choose automatic downloads or existing-library use, connect
providers, select local/paired storage, configure downloads and review the result.
Previously account creation stopped at preferences and left these steps in Settings.
Existing administrators with unfinished setup enter the same flow; progress is
saved server-side, and Finish later retains an unfinished state and return link.
Household viewers still receive only their personal-preferences welcome.

Completion checks the selected mode against observed storage. Automatic downloads
need provider keys and one available destination with media tools, writable
library/incoming folders and download capability. Existing-library mode needs no
provider or download app. Saved credentials/settings are labelled as configured;
setup makes no paid calls or acquisitions and does not claim live provider access.
The owner can revisit `/setup` or Settings → Server setup without creating a new
account. External access, TV and the setup agent remain out of scope.

Validation: 161 backend tests, 159 passed and two opt-in live-model tests skipped;
production frontend build, dependency/audit and release checks pass. Five new API
cases cover restart persistence, deferral versus completion, credential privacy,
admin authority and unavailable/read-only/split storage. The isolated onboarding
browser journey covers 24 layouts at 360, 390, 768 and 1440 pixels, missing settings,
folder errors, refresh/sign-out/resume and provider-free import. The Linux package
checks saved setup progress across container restart alongside import, ranged and
converted playback, persistent login/progress and bundled speech-model loading.
Browser artifacts use fictional media and disposable state. No live provider or
physical-device acceptance is implied.

[Recorded checks](product-validation/onboarding-checks.json),
[phone setup](product-validation/onboarding-choice-mobile.png) and
[desktop summary](product-validation/onboarding-review-desktop.png) preserve the
isolated evidence. Existing viewing, household, accessibility and design journeys
also pass, including 55 redesign layouts and 40 follow-up layouts.

## TV guide redesign — 28 September 2026

Every screen was redesigned from the ground up under a new
[interface contract](PRODUCT_DESIGN.md): the household's own weekly TV guide,
in newsprint, print black, channel teal and biro blue, set in one bundled
variable family (Archivo). The owner chose the direction from an impeccable
direction round and approved structural UX changes. After a first look the
owner found the bright yellow fields hard on the eyes; the band became a
mid-dark channel teal and yellow shrank to a marigold accent for small marks.

- **Guide replaces Home and Library.** One page holds Tonight (resumes with a
  direct play button), Coming up (open requests) and the whole collection as an
  A–Z listing or covers. `/library` redirects to it with its filters. Search,
  type, sort, availability and view persist in the URL.
- **Find has one box.** Title matches appear as you type and are marked when
  already in the guide; Ask Sparrow is the only way to start paid discovery.
  The separate title/assisted tabs are gone; drafts and sessions persist.
- **Couch and TV use.** Arrow keys move between listed titles, rows and covers
  across the app; `/` focuses the page's search; the type scale grows with very
  wide screens. Focus rings are drawn for distance.
- **Fewer words and labels.** Only exceptions carry flags; preferences show a
  source only when it is personal or limited by the server; Following is one
  line on a title page and names the title on Requests even when the saved
  subscription has none.
- **Removed:** Tailwind, DM Sans and the pastel illustrations. No backend,
  API or runtime dependency changed.

The browser suite was updated for the new copy and structure and passes in full
(`tests/browser/ci.sh`, including axe checks at 360, 390, 768 and 1440px). Two
harness races were fixed: audits now wait for a frame after an emulated resize,
and the session check counts End session buttons directly. Repository checks
pass with 161 tests (two opt-in live-model tests skipped); the production build,
npm audit and release-tree checks pass. A new capture script,
`tests/browser/gallery.cjs`, photographs every screen from a fresh fixture; the
[before and after gallery](redesign/README.md) compares all 25 screens. The
earlier validation galleries remain as dated evidence of the previous design.
Physical-device, Windows and live-provider release gates are unchanged.
