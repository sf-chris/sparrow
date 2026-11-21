# Issue #3 implementation ledger

Started 11 September 2026 on `issue-3/durable-outcomes`, from handoff
`6857432` and refreshed `main` at `bec7f14`. This is implementation evidence;
the parent issue remains the acceptance tracker. Unchecked work is not complete.

## Sequence and starting evidence

1. Refreshed GitHub and reproduced both audit findings using disposable state.
   Failed review: one call before and after unchanged recheck, no retry timer.
   Legacy live evaluation: `evidence` returns `Subscription not found.`
2. Inventory consumers before retirement; repair the current evaluation fixture.
3. Implement durable recovery against regression tests, preserving authority,
   node operation receipts, originals and conservative cost reservations.
4. Profiles/evidence, role quality, standing care and framework comparison follow
   the P2/P3 contracts. Live model quality and actual device playback need their
   own evidence; controlled responses cannot satisfy those gates.

## Initial consumer inventory (P1 prerequisite)

| Candidate | Observed consumers | Disposition / retirement condition |
| --- | --- | --- |
| CWM source, service and flag | Former admin routes and diagnostics | **Deleted:** executable source/service/routes, environment flag guidance, diagnostic and unused log writers. Legacy `Storage.load_all` readers remain for migration; original state and source files are preserved without execution |
| Legacy curator and flag | `SPARROW_LEGACY_CURATOR` read only in `main.lifespan`; `curator.poke()` from legacy download/organise/scan paths; goal/show/movie routes; `.env.example`. No frontend `api()` call (the product calls only `/api/v1`), test, script, browser or package consumer | **Deleted** (`baccbe8`): `curator.py`, flag, startup task, pokes, `/api/goals*`, `/api/shows/{id}`, `/api/movies/{id}[/status]` (served the deleted Show page; `/api/v1/titles/…` replaces them). Saved goals are `MediaRequest` rows and still load |
| `coverage_planner.py` | Imported only by `curator.py` | **Deleted** with the curator (`baccbe8`) |
| `release_decision_engine.py` | `request_service.py` and `search_engine.choose_season_strategy`, whose only caller was `request_service.py` | **Deleted** (`faafec3`). Nothing extracted: `triage_parse` uses only `parse_release_name`; Fetch ranking lives in `scout.py`/`release_match.py` |
| `request_service.py` | `/api/requests`, `/selected`, `/preview`, `/{id}`, `/{id}/retry`, `DELETE` in `backend/main.py` and `update_requests_for_download`; no product, test, script or browser caller (frontend helpers were already deleted) | **Deleted** (`faafec3`) with those routes, the progress updater, `MediaRequest` curator helpers and the unused storage writers. **Retained readers:** `_load_requests[_sqlite]`, the `_save_requests_sqlite` JSON→SQLite migration and `get_requests` (health summary count); `test_retired_request_history_still_migrates_and_loads` covers them |
| `file_organizer.py` | `auto_organize_download` (legacy poller), `/api/downloads/{id}/organize`, `/api/library/scan`; `test_filename_scan_is_never_verification`; dated `product-review/backend_reproductions.py` | **Deleted** (`69898d3`) with those paths and the test, which only exercised the retired filename scan. Existing folders enter through `catalogue.scan`/`confirm_import`, which probes every file (`test_nodes`). The `auto_organize` setting stays as an inert stored field because persisted configs carry it and `configuration.py` lists it; remove it when that allow-list and `PATCH /api/config` retire. The reproduction script is dated evidence pinned to `b84450c` |
| Old search/suggestion routes and `search_engine.py` | `/api/search`, `/stream`, `/deeper/stream`, `/season/stream` (queued downloads around the agents' tool guardrails), `/log`, `/suggest`, `/suggest/expand`, `/suggest/tv/{id}`; Small calls in `_small_title_expand` and `_ai_alt_queries_stream`. Only live import: `tools.py` `_query` | **Deleted** (`7ffefe7`). **Extracted:** the index query to `services/apibay.py`; `tools.apibay_query` keeps its name, so guardrail tests patch it unchanged. Orphaned `SearchResult` and `tmdb_tv_seasons` deleted |
| Release parser Small tier and parse cache | `parse_releases`/`parse_one`/`_ai_parse_batch` and `Storage.get/put_parsed_release`, called only by the curator and organiser | **Deleted** (`94fb707`); new installs no longer create `parsed_releases`, existing tables are untouched. **Retained:** `parse_release_name`/`ParsedRelease` for `scout.py`, `triage_parse`, `catalogue.scan` and `main._classify_media_type`; output is identical on sample names |
| Remaining non-`/api/v1` routes and loops in `backend/main.py` | Config, onboarding status, doctor, torrent clients, health/actions, activity, resolve, jobs/journal/sessions/usage/prompts/mandates, downloads, library, metadata, logs; the legacy download poller, artwork enrichment and seeding enforcer. No product caller; `identity_boundary` limits them to administrators. The poller tracks manual `POST /api/downloads` and saved non-agent transfers (`test_legacy_boundaries`); artwork/seeding loops cover every item and transfer | **Retained** as administrator diagnostics/plumbing outside this pass. Retire each when its `/api/v1` equivalent (admin config, imports, logs, jobs) is the accepted administrator API and the owner agrees to drop direct access; delete the poller together with `POST /api/downloads` |
| Legacy Librarian registration and prompt | Registration was overwritten by `Curation.register`; diagnostic previews advertised a different tool set | **Deleted:** overwritten registration. Previews now use registered tool sets and the actual personal Librarian prompt. Old tool/prompt helpers still serve scope-guardrail regressions; migrate all retained cases before deleting them |
| `resolution.py` | Only `/api/resolve`, which made a one-shot description call outside any agent session | **Deleted** (`af2e7b7`). The route calls `tmdb_quick_suggest` directly with the same key resolution, so direct lookup is unchanged; descriptions belong to Discovery |
| Legacy frontend pages, layout, client | Only `App`/`main`/`product` are reachable; old pages imported only their old client/types/components | **Deleted:** seven pages, layout/UI helpers, client and types. Shared `useWebSocket` remains because current product screens use it. Removed unused cinema artwork/Instrument Serif font and its notice |
| Python/JS/system packages | Import and dependency graph audit plus clean package validation | **Removed:** `clsx`; `aiofiles`, `aiosqlite`, `beautifulsoup4`, `lxml`, transitive `soupsieve`. Regenerated both locks; zero retained version changes. No system dependency removal was justified. The later route/model-call retirement orphaned no package (`provider` serves the runtime and subtitle contract, `httpx` the index and catalogue), so no lock changed. **Open:** `ffsubsync` has no code import since its alignment path was retired, but `subtitles.py`, `subtitle_provider.py` and `subtitle_worker.py` import `pysubs2`/`charset_normalizer`, which the lock provides only through it; removal must promote those to direct requirements, regenerate the hashed lock and update `packaging/windows/node.spec` and `THIRD_PARTY.md` |

## Documentation inventory

| Material | Disposition / reason |
| --- | --- |
| `AGENTS.md`, `DESIGN.md`, `PRODUCT_PLAN.md`, `PRODUCT_DESIGN.md`, `ROADMAP.md` | Current authority; reconcile stale architecture/status paragraphs with each implementation |
| `IMPLEMENTATION.md`, `INSTALLATION.md`, `NEARBY_DISCOVERY.md` | Current measured limits, installation and scoped prototype contract |
| `AGENTIC_PLAN.md`, `agentic-audit/results.json`, `reproduce.py` | Dated baseline and accepted work packages; retain original observations |
| `DESIGN_FOLLOW_UP.md` | **Consolidated and deleted:** current interface/implementation contain implemented visual/logging requirements; nearby scope/limits remain in their dedicated record |
| `REIMAGINED_ASSETS.md` | **Consolidated and deleted:** `PRODUCT_DESIGN.md#artwork-and-font-provenance` preserves current asset/font provenance; README/gallery links updated |
| `PRODUCT_REVIEW.md`, `product-review/` | **Retained as dated evidence:** links to retired frontend and backend source (`file_organizer.py`, `resolution.py`) pinned to reviewed `b84450c`; the reproduction script is documented as running from that commit. Implementation record identifies later fixes. Reproductions and scope incidents remain useful regression provenance |
| `ALPHA_RELIABILITY_PATCH.md` | Retain sanitized season-one scope incident until current evaluation covers attempted and accepted violations |
| `DISTRIBUTED_NODES_PLAN.md` | **Retained/current contract:** corrected obsolete status; old starting architecture is explicitly dated. Physical Windows/NTFS/service gates remain open |
| `product-validation/`, `reimagined-validation/`, `follow-up-validation/` | **Retained:** `tests/browser` scripts still write each directory by default (`entry`/`check`/`design`/`accessibility`/`household` → product, `reimagined` → reimagined, `follow-up` → follow-up), `docs/IMPLEMENTATION.md` and their READMEs link them, and `.dockerignore` excludes them from images. Per-artifact audit: every file is written by a current script or linked; `lan-entry-results.json`, `security-wide.png` and `playback-help-1440.png` backed README claims without a link, so they are now linked rather than deleted |
| `screenshots/`, subtitle fixture attribution | Current approved bright interface and required provenance: retain |

## GitHub disposition and dependency map

Enumeration on 11 September 2026 found one issue: #3, active. PRs #1 and #2 are
merged historical implementations. [PR #5](https://github.com/sf-chris/sparrow/pull/5)
merged at `a7f99c6` with owner authorization and successful Product checks. It
included the #4 documentation commits; GitHub also marked #4 merged. Neither
completes #3. No older issues, native child dependencies or obsolete blockers
were found to close. External Mogged issues remain references. No issue closure
is justified by this inventory.

Before and after this initial inventory: P1 inventory → P2 baseline → P3 recovery
→ P4 profiles/evidence → P5/P6/P7; P8 records a framework decision after the shared
contracts; P9 requires completed implementation, cleanup and real acceptance.

## Implemented behavior and measured evidence

- Durable event outbox and per-session delivery IDs. Acknowledgements commit with
  the wake context. Unrouted facts retry on timer scans and startup. Landed-file
  facts are recorded before their polling suppression marker.
- Assistant tool batches persist before effects; each invocation/result and
  control outcome persists separately. Observers run after result persistence.
  Interrupted batches recover known results and mark unknown effects uncertain;
  they do not blindly rerun mutations. Node receipts remain authoritative.
- A runtime-to-node replay creates one download. Crash tests cover queued events,
  failed context saves, partial batches, effects without results, completion
  without acknowledgement, duplicate IDs with changed arguments and cancellation
  both during a tool and during its completion notification.
- Personal curation separates observations from successfully handled evidence,
  resumes the same unfinished session and keeps acquired-request receipts after
  a later model failure. Retry delays survive startup and normal timer scans.
  Older fingerprints without completion evidence cannot suppress recovery.
- Replacement sessions share the subscription's existing maximum reasoning
  allowance, including historical sessions and uncertain reservations. This is a
  conservative **lifetime allowance**, using the configured request dollar limit;
  it does not introduce a daily/monthly renewal policy. The
  [budget setting explains this behavior](budget-setting-desktop.png). This capture
  uses the same isolated browser fixture and production UI; it contains no
  personal media or provider data. Changing the allowance
  and explicitly retrying are administrator/user actions. P7's richer standing
  policy remains open.
- Sessions expose active/completed/waiting/needs-input/budget-limited/failed/
  cancelled outcomes alongside compatible lifecycle status. Plain text alone
  does not mean verified completion. Cancelled work cannot be resurrected by a
  late result; abandoned jobs are failed outcomes, not completed viewing requests.
- Current personal Librarian live fixtures inspect `evidence`, record `acquire`
  attempts and actual job scope, and require completion. A blocked forbidden
  attempt is still a quality failure. Fresh fixtures isolate each repetition. Attempts are read from the complete
  model transcript, including actions blocked before dispatch or after finish.

[Updated audit replay](recovery-results.json) shows the failure retains a positive
retry timer and has no successful fingerprint; recovery closes the same session,
and the completed unchanged recheck makes no extra call. The old unscoped session
still correctly fails authority checks; the repaired evaluation no longer uses it.
The original [audit results](results.json) remain unchanged as historical evidence.

[The versioned contract manifest](../../tests/evals/contracts-v1.json) contains
30 ordinary/adversarial cases across Discovery, Fetch, Media, Librarian and
subtitle work. [Recorded controlled results](contracts-v1-results.json) contain
three repetitions, latency, source/manifest identifiers and outcomes. These are
90 controlled contract runs, **not 90 live model evaluations**. A fresh real-model
holdout and the remaining live role fixtures are still required before profile
selection. Provider cost for these controlled runs is zero.

[Verification summary](verification.json): **140 tests, 138 passed and two
opt-in live-model tests skipped**. `scripts/check.sh` with the available FFmpeg/ffprobe
binaries; compilation, dependency consistency, backend suite, frontend production
build, npm audit and release-tree checks. The independent browser runner used a
free port (8893), temporary state and fictional media; all viewing, account,
request, household, subtitle, accessibility and layout journeys passed, including
55 redesign layouts/11 behaviors and 40 follow-up layouts. Approved public
screenshots were preserved. The isolated Linux package on port 8894 passed import,
media ranges, converted segments, persistent login/resume across restart, PWA
assets and loading the bundled speech model. No installed owner state was used. Packaged backend source hashes match the
checked working tree. The removed CWM endpoints remain unavailable even with
the old enable flag set (GET 404; removed POST handlers 405).

## Remaining issue #3 work (do not close the parent)

The next P4 slice adds [retrievable oversized observations](EVIDENCE.md), atomic
with invocation receipts, private to a session and subject to explicit storage
limits. Fetch search/file listings, Discovery collection matches and Librarian
candidates retain all locally available rows. The linked record states the
retrieval contract, measured regression cases and remaining evidence boundaries.

| Package | Current boundary and next acceptance |
| --- | --- |
| P1 | Code cleanup (C) delivered for the listed legacy paths: curator, requests/goals, planners, old search and organiser routes and the one-shot Small/description calls they made are gone; galleries audited and retained. Remaining: the administrator-only routes/poller and the inert `auto_organize` field (retirement conditions above), the orphaned `ffsubsync` dependency, scope-guardrail cases still using old Librarian helpers, and documentation/issue cleanup A/B |
| P2 | Current Librarian live fixture and 30-case controlled baseline delivered. Add full live fixtures for the other roles, separate unseen holdout, real-model/provider repetitions and full measured outcome costs |
| P3 | Core event/result persistence and recovery delivered with controlled crash tests. Extend combined restart/revision/provider chaos scenarios in the final acceptance pilot; conservative uncertain effects still require receipt investigation |
| P4 | Oversized tool observations now have complete saved artifacts, explicit previews, bounded retrieval/listing and serialized storage quotas. Versioned provider profiles/effort, bounded escalation, structured checkpoints/recoverable compaction, cross-session handoffs, retention policy and fuller source pagination remain |
| P5 | Rich Discovery intent/edition constraints, complete acquisition candidate evidence, targeted missing-episode investigation and structured Fetch/Media results remain |
| P6 | Structured rejected/missing-file results, optional import investigation and richer subtitle samples/candidate/repair tools remain |
| P7 | Durable review and shared allowance foundation delivered; scoped evidence-backed lessons, useful policy windows/renewal and complete standing-care quality gates remain |
| P8 | Existing runtime retained during recovery implementation. This is not the requested same-model SDK comparison or a final framework decision |
| P9 | Isolated Linux/browser checks are available. Installed-runtime rollout, live six-scenario pilot, native Windows, physical mobile/Safari, provider acceptance and final cleanup/issue closure remain |

The implementation adds no framework/provider dependency and changes no production
model default. Recovery may retry unfinished work within the existing conserved
allowance; completed unchanged subscriptions continue making zero model calls.
No installed deployment, paid/provider acquisition or physical-device acceptance
was performed. All retained issue scope remains with #3.

## P6 audio-first experiment — 29 September 2026

The [subtitle trial record](SUBTITLE_TRIAL.md) documents an opt-in local
transcription/semantic-comparison harness and its actual English speech run.
The initial 18 subtitle/provider/trial tests passed. The subsequent
[Four Lions experiment](FOUR_LIONS_TRIAL.md) acquired the actual film using
Sparrow's search/node downloader tools, sampled foreign dialogue with three local
model configurations, reused the existing Assistant CLI sign-in for live reviews,
and checked native browser captions in isolated excerpts. It exposed missing
translations, recognition failures and semantic-review problems. The opt-in CLI
adapter adds no production provider or default change. No subtitle quality pass,
autonomous installed acquisition or installed-player acceptance is claimed.
The expanded subtitle/provider/trial suite passes all 21 tests, including CLI
isolation, invalid-action rejection and cancellation receipts. Compilation and
dependency consistency pass; production subtitle readiness remains unchanged.

The 30 September continuation adds `subtitle_investigation.py` and an isolated
speech worker for an actual inspect → recognise → edit draft → reassess loop.
Source hashes, immutable observations/revisions, writer locking, stale-revision
checks and patch-intent recovery protect the experiment. A complete soundtrack
pass and seven scene reviews produced real caption corrections but also exposed
incorrect approvals and unresolved speech. Full-movie fixture playback through
Sparrow passed 24 positions and found/fixed the paused-seek buffering overlay.
The suite now passes **32 subtitle/provider/trial tests** and the frontend build
passes. The [updated trial record](FOUR_LIONS_TRIAL.md) distinguishes these checks
from the stricter bilingual quality gate used at that time. No installed account/library,
production provider, default policy or verified flag was changed.

The owner's subsequent clarification replaces translation perfection with
watchability: a working track for the actual movie, broadly aligned to the voice.
Reassessing the same recorded evidence gives **Four Lions a watchability pass**;
the short uncle wording uncertainty is nonblocking. The trial record preserves
the earlier strict result. Reviewer prompts now focus on material identity,
coverage and timing failures and preserve usable captions; measured tool gates
remain unchanged. General unattended/multi-title acceptance is still separate.

## Anime subtitle controls and review — 30 September 2026

The owner authorized the anime batch and the discussed subtitle controls. Default
inclusion and opt-in sync checking now share household/personal/request resolution;
imports and landed copies retain that contract. Basic preparation makes no ASR or
model call and preserves timings. Title, episode and player actions distinguish
available captions from sync-checked captions. Older in-flight required-subtitle
contracts keep their previous verification requirement.

Live trials exposed signs-only default tracks, misleading cross-language lexical
scores and incorrect model-counted Japanese indices. Actual cue inspection and
quoted source-phrase measurement address those failures. Translated rejections
receive a smart-tier second review; tools still refuse approval without passing
measurements. The eight-call review also has one bounded extra-sampling operation.

The [trial record](ANIME_SUBTITLE_TRIAL.md) reports three sampled watchability passes,
24 real-player positions, negative offset/wrong-content controls, 77 passing tests,
the frontend build, costs and iteration failures. All originals remain unchanged.
The additional-sampling branch has controlled coverage; the recorded live passes
used their original sample sets. Installed rollout, live provider acceptance,
physical mobile/Safari and an independent holdout remain separate gates.

## Subtitle evidence, timing correction and Large review — 30 September 2026

The owner narrowed the subtitle target to foreign-language audio with English
captions, reviewed by Large 5.5 over audio-timed evidence it can extend, with
caption sync measured to about ±50 ms and corrected automatically. The storage
node now builds whole-soundtrack, caption-independent speech evidence
(`subtitle_evidence.py`); `subtitle_sync.py` measures offset, drift and
consistency and proposes the least invasive correction, applied without a model
call; `subtitle_review.py` gives the reviewer paged speech (captions revealed only
after its own gloss), re-listening and a tool-enforced approval gate. Rejected or
inconsistent tracks are set aside for the next candidate. The five-sample checker,
FFsubsync path and old benchmark were retired. The
[evidence record](SUBTITLE_EVIDENCE.md) reports the 91 ms detector onset lag,
three professional-track baselines (one consistently 220 ms late), model
comparison, heuristics, tests and the end-to-end Haibane run.
