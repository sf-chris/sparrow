# Agentic reliability and intelligence plan

Status: **implementation proposal and handoff**, 11 September 2026. This is a
code, documentation and issue audit followed by an implementation proposal.
The owner requested consolidation into a GitHub issue and explicitly added
removal of obsolete design documents, obsolete issues/dependencies and dead
code. Model profiles and the framework choice remain evaluation candidates.
This document does not record a runtime migration or a completed model evaluation.

Sparrow should use agents to own uncertain outcomes, with ordinary code doing
measurement, processing, scheduling and enforcement. Its normal application
already follows that architecture. The next step is to make those agents more
capable, durable and economical, then prove the resulting viewing experience.

## What is actually running

Audit baseline: Sparrow `bec7f14f079bd8e909e1659267df73b25b357d85` on `main`.
The installed container has neither `SPARROW_ENABLE_LEGACY_CWM` nor
`SPARROW_LEGACY_CURATOR` set. Startup enables the agent service and leaves the
old curator loop off. CWM routes return 404 when their legacy switch is off.
**The normal application is no longer using the self-modifying CWM.**

Retirement is not complete deletion: CWM modules, historical data readers,
legacy APIs, the old curator, one-shot resolution and other legacy model calls
remain in the repository. Authenticated administrators can still reach some
legacy APIs. Their removal needs a consumer inventory; a blanket deletion of
old state or routes could remove useful history or compatibility.

Sources: [startup and legacy routes](../backend/main.py),
[account boundary](../backend/agents/account_api.py),
[design](../DESIGN.md), [agent instructions](../AGENTS.md).

| Area | Implemented behavior | Gap to address |
| --- | --- | --- |
| Agent runtime | Persistent Anthropic model/tool loops; authority and revision checks; per-wake step limits and durable cost reservations | Explicit model/effort profiles, durable event delivery, complete evidence and stronger recovery |
| Discovery | Cheap model, up to eight calls; search/details/library tools; inspected title proposals; no acquisition authority | Difficult descriptions and multi-constraint requests need evaluated escalation; interrupted searches currently need user refinement |
| Fetch | Smart model, up to 40 calls per wake within policy; source search/inspection and recovery; owns the requested result | Better structured candidate evidence, bounded investigation and recovery; evaluate cheaper ordinary cases before lowering its default |
| Media | Cheap model; node tools inspect, publish and record files; can escalate to the smart model for the remaining session | Structured results for rejected/missing files and difficult identity/edition decisions; assisted import needs a deliberate extension |
| Librarian | Cheap, six-call review scoped to a person and subscription; eligibility calculated from authority and aired facts; unchanged fingerprints avoid model calls | Each changed review currently creates a fresh session; incomplete work must survive restarts and recover without losing the idle-cost benefit |
| Subtitle review | Cheap, three-call evidence/verdict loop after deterministic preparation; failed measurements cannot be approved | Cannot currently request additional samples, investigate a different candidate or escalate; rejection generally needs attention |
| Import | Filename suggestions, owner-selected identity and deterministic inspection/publication | Optional Media assistance for ambiguous matching, preserving explicit import authority and stable file identity |
| Playback, accounts and nodes | Deterministic playback, authorisation, conversion, measurements and durable node operations | Complete physical-device/provider acceptance; these operations do not need a reasoning call |

Current code defaults are `claude-sonnet-5` for smart and `claude-haiku-4-5`
for cheap; configuration and environment can override them. Only Media exposes
`escalate_model`. The runtime does not explicitly configure effort or thinking.
Step limits and maximum output tokens are separate controls
from reasoning effort.

Sources: [service](../backend/agents/service.py),
[runtime](../backend/agents/runtime.py), [Discovery](../backend/agents/discovery.py),
[node tools](../backend/agents/node_tools.py), [Media escalation](../backend/agents/tools.py),
[curation](../backend/agents/curation.py), [subtitles](../backend/agents/subtitles.py),
[catalogue import](../backend/agents/catalogue.py).

## Documentation and GitHub findings

Before creating the consolidated handoff issue, Sparrow had **zero open or
closed GitHub issues** at the audit date. Its backlog was in
[ROADMAP.md](../ROADMAP.md), [PRODUCT_PLAN.md](PRODUCT_PLAN.md) and the
review documents. The two merged PRs are the
[scope/reliability patch](https://github.com/sf-chris/sparrow/pull/1) and
[design integration](https://github.com/sf-chris/sparrow/pull/2). Neither has
review comments adding a separate agent backlog.

Read the documents in this order: current [design](../DESIGN.md) and
[product plan](PRODUCT_PLAN.md), then [implementation evidence](IMPLEMENTATION.md),
then historical reviews and milestone checklists. Several older descriptions
now disagree with code: the node plan still says implementation has not begun,
older roadmap boxes describe Discovery as missing, DESIGN's budget paragraph
still calls resolution a single cheap call, and some agent comments describe
a whole-library Librarian. These need reconciliation without rewriting the
historical evidence as if new acceptance had occurred.

The most consequential historical incident remains the Librarian treating
ownership of season one as authority to acquire later seasons. Preserve its
[sanitised regression case](ALPHA_RELIABILITY_PATCH.md) in every model evaluation.

The current opt-in [live Librarian evaluations](../tests/evals/test_librarian_behaviour.py)
also need repair before they can select models: they create an unscoped legacy
session and inspect `spawn_job`, whereas the registered implementation expects
a personal subscription and uses `acquire`. Controlled
[curation](../tests/test_curation.py), [Discovery](../tests/test_discovery.py)
and [subtitle](../tests/test_subtitles.py) tests are useful contract evidence,
but they do not establish real-model judgment quality.

The [audit reproduction](agentic-audit/reproduce.py), using temporary curation
fixtures and a controlled provider failure, confirms two findings at this
baseline: the unchanged follow-up check makes no retry call and retains
`wake_at=0`; the legacy evaluation session's evidence tool returns
`Subscription not found.` [Recorded output](agentic-audit/results.json).
Run `.venv/bin/python docs/agentic-audit/reproduce.py` from the repository root;
it makes no provider calls and does not touch installed application state.

## What to reuse from Mogged

Reviewed Mogged's authenticated GitHub issues and local source at
`a267710659647abec572a7161e85d32161f42eb6` (`release/platform-mvp`), and fetched
`main` at `fcaac58bdad3a3da9c9ec1f8ddd2ff38be1bf788`. The research runner,
worker, report validator, evidence store and single-agent acceptance documents
examined here have no differences between those revisions.

| Pattern | Evidence in Mogged | Application to Sparrow |
| --- | --- | --- |
| One accountable conversation per outcome | [Runner](https://github.com/levy-street/mogged-seo/blob/a267710659647abec572a7161e85d32161f42eb6/api/app/agent/runner.py) resumes the saved session; research and correction stay together | Fetch continues to own a request through verification. Media/subtitle work returns durable specialist results to that owner |
| Complete request and versioned instructions | [Worker](https://github.com/levy-street/mogged-seo/blob/a267710659647abec572a7161e85d32161f42eb6/api/app/worker.py) saves request revisions and prompt versions, checking current ownership/revision before publication | Persist the exact intent, effective preferences, prompt/tool versions and model profile used for each decision |
| Full evidence stored outside the conversation | [Evidence store](https://github.com/levy-street/mogged-seo/blob/a267710659647abec572a7161e85d32161f42eb6/mcp/src/evidence.ts) saves complete atomic artifacts with stable IDs | Store full candidate lists, file inventories and measured observations; return concise, explicitly partial previews plus retrieval tools |
| Completion is a checked submission | [Report validator](https://github.com/levy-street/mogged-seo/blob/a267710659647abec572a7161e85d32161f42eb6/api/app/agent/report.py) checks evidence and artifact hashes against the current request | Extend existing publication receipts and completion checks into a single inspectable request result |
| Bounded tools and recoverable ownership | Runner process cleanup; worker leases and stale-publication rejection | Extend Sparrow's durable node protocol to agent events and tool invocations, with tool-specific deadlines and uncertain-result reconciliation |
| Actual runtime parity and independent review | [Acceptance record](https://github.com/levy-street/mogged-seo/blob/a267710659647abec572a7161e85d32161f42eb6/docs/SINGLE_AGENT_ACCEPTANCE.md) distinguishes delivered output from accepted quality | Record actual model, effort, runtime and prompt versions; inspect real outcomes in the packaged application |

Mogged's initial production runs delivered artifacts but failed quality review.
Its record identifies a local/production mismatch in CLI version, model and
reasoning effort. The correction pinned Codex 0.153.4, `gpt-6-astra`, medium
effort and updated instructions; three fresh runs then passed independent
acceptance. This is evidence for explicit configuration and outcome review,
not a controlled comparison proving which model caused each improvement.

Mogged has 26 open issues. The relevant future platform work is
[#39](https://github.com/levy-street/mogged-seo/issues/39) (agent/run visibility),
[#40](https://github.com/levy-street/mogged-seo/issues/40) (real worker and durable transport),
[#41](https://github.com/levy-street/mogged-seo/issues/41) (scoped access),
[#43](https://github.com/levy-street/mogged-seo/issues/43) (tasks, follow-ups and approvals),
[#49](https://github.com/levy-street/mogged-seo/issues/49) (research-tool reuse),
[#52](https://github.com/levy-street/mogged-seo/issues/52) (event-driven mailbox work),
and [#53](https://github.com/levy-street/mogged-seo/issues/53) (real pilot acceptance).
The [release tracker](https://github.com/levy-street/mogged-seo/issues/56) and
runbook distinguish the working research agent from the planned Mac operational
agent. An open issue is a useful design contract, not proof its capability shipped.

Adopt these patterns through Sparrow's existing domain boundaries. Its media
agents do not need unrestricted shell access, arbitrary generated Python or
one universal household conversation. Keep long-running downloads asleep
between events, retain spending limits, and keep private history scoped to its
owner. Translate architectural ideas; do not copy private business artifacts
or treat another application's deployment settings as Sparrow defaults.

## Intelligence allocation

These are **starting hypotheses for evaluation**, not measured winners.
Choose the cheapest profile that meets each role's quality and latency target.

| Work | Initial profile | Escalation or exit |
| --- | --- | --- |
| Exact-title autocomplete, airing checks, polling, file probing, conversion, subtitle alignment, permissions and progress | No LLM | Failed operations return typed facts; uncertain interpretation wakes the responsible agent |
| Routine Discovery, clear Media identification, ordinary authorised collection care, clear subtitle samples | Haiku 4.5, bounded tool loop, thinking disabled | Escalate when evidence conflicts or the task needs a more capable investigation |
| Fetch ownership; difficult Discovery; ambiguous episode numbering, editions or subtitle repair | Sonnet 5, explicit medium effort as the evaluation candidate | Compare against Sonnet high; retain the stronger configuration where medium loses outcome quality |
| Unresolved cases with sufficient evidence and meaningful consequences | Sonnet high, then a bounded Opus 5 high profile if it improves results | Stop with a precise blocker if evidence or authority is missing; never buy more reasoning to invent it |

Haiku 4.5 does not support the effort parameter. Sonnet 5 and Opus 5 do; their
documented default is high. Effort is not a hard spending limit. The adapter
must validate model capabilities and configure thinking, effort and output
allowance together. [Model capabilities](https://platform.claude.com/docs/en/models/overview),
[effort documentation](https://platform.claude.com/docs/en/build-with-claude/effort).

Mogged's `gpt-6-astra` at medium is a useful alternative candidate for the hard
case set if an OpenAI adapter is evaluated. Its successful research use does
not establish a need for it on every Sparrow task. OpenAI documents text/image
input and configurable reasoning effort for this model; direct audio input is
not supported. [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra).

For scale only, standard uncached input/output prices per million tokens at the
audit date are Haiku 4.5 **$1/$5**, Sonnet 5 **$2/$10**, Opus 5 **$5/$25**, and
GPT-6 Astra **$10/$50**. A hypothetical 10,000-input/1,000-output-token call costs
about **$0.015 / $0.030 / $0.075 / $0.150**, respectively. This excludes additional
thinking/output, caching, tool charges and repeated calls. It is not a measured
Sparrow request cost. [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing),
[Astra pricing](https://developers.openai.com/api/docs/models/gpt-6-astra).

Routing should use known task type and saved evidence without adding a separate
classifier model call. A difficult user request can start at the balanced tier.
Cheap agents can request escalation with an evidence-backed reason; code checks
the allowed profile and remaining request/household allowance. Keep the same
case identity and retained evidence. A bounded specialist case can use a
stronger model without permanently raising a standing subscription's cost.

Measure total cost per verified outcome, including failed attempts and retries.
A cheap model that needs many attempts can cost more than one successful
balanced-model attempt. Do not lower Fetch's current tier until comparisons
show that both outcome quality and total cost improve.

## Runtime and evidence changes

Preserve existing file-version receipts, node idempotency, authority checks,
completion validation and budget reservations. The additions are:

1. **Durable case state.** Persist event IDs, delivery acknowledgements and each
   tool invocation before execution, then its result before another decision.
   Recover interrupted writes by inspecting existing operation receipts. Model
   history currently saves after a whole tool batch, and pending wake events
   live in memory; these are gaps around otherwise durable node operations.
2. **Explicit outcomes.** Use completed, waiting-for-event, needs-input,
   budget-limited, failed and cancelled states with a concrete reason/trigger.
   A text-only model ending must not silently strand an unfinished case.
   Provider recovery is a changed dependency that can wake pending work.
3. **Recoverable subscription review.** Separate observed evidence from
   successfully handled evidence. Curation currently saves a fingerprint before
   the review succeeds, and resets its unfinished session's retry timer. A
   provider failure can therefore leave unchanged work requiring manual retry.
   Resume that case on recovery; completed unchanged work still costs zero tokens.
4. **Evidence by reference.** Store complete observations before producing a
   preview. Current generic results truncate at 40,000 characters; source and
   file-list tools also cap returned rows. Include total count, returned range,
   completeness, observation time and stable retrieval references. Keep storage
   quotas and private access controls; operational Logs are not the evidence store.
5. **Versioned context and memory.** Persist prompt/profile/tool versions and a
   structured checkpoint containing the request revision, completed/remaining
   scope, evidence IDs, failed attempts and next trigger. Replace blind history
   trimming with recoverable compaction. Learned notes stay scoped data with
   evidence and invalidation conditions; they cannot grant authority or execute code.
6. **Structured handoffs.** Media returns verified assets, rejected files,
   missing items and receipt IDs. Subtitle review returns measured checks,
   sampled-content assessment and unresolved issues. Fetch rechecks the current
   contract before closing. Existing tools already enforce key parts of this;
   the work makes the complete result durable and inspectable.
7. **Observable profiles and bounded tools.** Record requested/actual model where
   available, effort, token usage, estimates, escalation reason and runtime
   version. Add deadlines/cancellation to the tool contract, while continuing
   long media work through durable node operations. Retain uncertain billing
   reservations until reconciled; profile changes cannot reset the budget.

Subtitle investigation needs actual additional evidence: another candidate,
representative transcripts, timing observations or supported multimodal samples.
A text reviewer must not claim to have listened to a film. Translation quality
and whole-film synchronisation remain separate evaluation problems from a few
successful English speech samples.

## Framework decision

Keep the present runtime as the baseline while evaluating a thin adapter on
read-only Discovery. Compare it with the Claude Agent SDK first, as the product
plan already proposes. The SDK supplies a loop, context management and sessions;
Sparrow still owns permissions, durable jobs, budgets and verified completion.
[Official SDK overview](https://code.claude.com/docs/en/agent-sdk/overview).

Codex is another possible adapter: its CLI supports resuming a specific session,
which Mogged uses. Test the same cancellation, interruption, evidence and budget
contracts before selecting it. [Official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

Separate the framework comparison from model selection. Compare the same model
where feasible; label tests that change both runtime and model as combined
system comparisons. Migrate one role only if the new adapter improves measured
quality or meaningfully reduces maintenance while preserving every contract.
An SDK migration is not a prerequisite for the reliability work below.

## Required cleanup and consolidation

The owner explicitly requested these three cleanup workstreams as part of this
implementation. They are deliverables with recorded outcomes, not suggestions
to leave old material indefinitely. Perform them alongside the relevant feature
changes and finish the audit before closing the parent issue.

### Remove obsolete design documentation

Keep a small authoritative reading path: `AGENTS.md`, `DESIGN.md`, the current
product plan and interface contract, `ROADMAP.md`, and a concise implementation
record containing measured evidence and unresolved gates. Fold still-valid
requirements into those documents before deleting their superseded source.

Review `docs/DESIGN_FOLLOW_UP.md`, `docs/REIMAGINED_ASSETS.md`,
`docs/PRODUCT_REVIEW.md`, `docs/ALPHA_RELIABILITY_PATCH.md` and
`docs/DISTRIBUTED_NODES_PLAN.md` individually. These are cleanup candidates,
not a declaration that all their contents are obsolete. Retain the real
scope-violation regression, device/provider acceptance gaps and useful node
contracts. Remove outdated design alternatives, duplicated implementation
instructions and resolved defect descriptions from the active reading path.

Review old product/follow-up/reimagined validation galleries and screenshot
references as well. Preserve the current approved bright interface, public
`docs/screenshots/` gallery, necessary fixture attribution and meaningful
acceptance evidence. Git history and pinned commit links can preserve historical
design context without keeping contradictory documents in the current tree.

Record each candidate as retained/current, consolidated-and-deleted, or retained
solely as dated evidence with a stated reason. Update all links from the README,
agent instructions, contributing guide, roadmap, issues and scripts. Remove
references to deleted preview routes and assets after checking actual consumers.

### Close obsolete issues and repair dependencies

Re-enumerate all Sparrow issues and open PRs at implementation time. At this
handoff there were no pre-existing issues to close; the new parent issue must
remain open until its actual acceptance gates pass. Mogged issue numbers are
external design references, not Sparrow dependencies to edit or close.

For each later child or related Sparrow issue, record one disposition: active,
completed with evidence, duplicate/superseded with a surviving issue link, or
obsolete/out of scope with a reason. Carry forward unresolved acceptance work
before closing a superseded issue. Close completed issues as completed and
superseded/obsolete issues with the appropriate not-planned reason; preserve
their history instead of deleting issue records.

Update native parent/sub-issue and blocking relationships where used, task
lists, roadmap links and PR references together. Remove obsolete blockers;
connect surviving prerequisites to the surviving issue. Do not close a valid
dependency merely because one of its dependants was cancelled or superseded.
Use closing PR keywords only when that PR actually completes the referenced
issue; a documentation-only handoff must not auto-close this implementation.

### Delete dead code and orphaned software dependencies

Inventory imports, mounted routes, frontend callers, background tasks, CLI and
packaging entry points, feature flags, fixtures and persisted-state consumers.
Trace actual use before deleting a path; an unused UI link alone is insufficient.

Initial candidates are `cwm/sparrow_world_model.py`,
`backend/services/cwm_service.py`, the legacy CWM routes/client helpers,
`SPARROW_ENABLE_LEGACY_CWM`, `SPARROW_LEGACY_CURATOR`, the old curator and
goal/request orchestration, unused legacy resolution/model-call paths,
unmounted frontend code, obsolete tests and retired preview assets. Review
`coverage_planner.py`, `release_decision_engine.py`, `request_service.py` and
`file_organizer.py` by consumer; preserve or extract any still-used functionality.

Delete obsolete branches and fallback logic after their remaining consumers
are migrated or intentionally retired. Preserve active deterministic tools,
advisory parsing, storage/media operations, file-version receipts, scope checks,
upgrade preservation and historical-state compatibility that is still needed.
Repository cleanup must not delete installed user media, credentials or state.

Remove Python/JavaScript/container/system packages used only by deleted paths,
regenerate the appropriate lockfiles and update packaging, environment examples,
CI, diagnostics and installation instructions. Do not combine this with unrelated
dependency upgrades. Record retained legacy adapters with their live consumer,
reason and a bounded retirement condition instead of silently keeping them.

Cleanup acceptance: every candidate has a disposition and evidence; no active
consumer or link points to a deleted item; package/build and relevant lifecycle,
guardrail, migration and browser checks pass; no abandoned issue dependency or
unowned model call remains. Publish the removal inventory and closure links in
the parent issue's final completion record.

## Proposed implementation packages

These are issue-ready work packages, not newly created GitHub issues. Start with
P1 and P2; implement P3/P4 before changing production intelligence defaults.
P5–P7 then improve the user-facing outcomes. P8 is a bounded framework decision;
P9 is the release acceptance gate.

| ID | Deliverable and principal files | Acceptance |
| --- | --- | --- |
| P1 | Consolidate/delete obsolete docs; triage and close superseded issues and repair dependencies; inventory/remove dead code and orphaned packages | Required cleanup section is satisfied; every active reasoning call has an owner/profile/budget; normal product cannot reach CWM; retained history is readable; delete obsolete consumers and compatibility paths with evidence |
| P2 | Repair live evaluation fixtures and establish a baseline; `tests/evals/`, role tests | Tests exercise the current personal subscription/tool contracts and each real agent; record tool attempts and outcomes, not journal wording |
| P3 | Durable agent events, invocation/result records, recovery states and subscription retry; `runtime.py`, `store.py`, `models.py`, `service.py`, `curation.py` | Restart before/after a tool effect and acknowledgement; no duplicate acquisition/publication, no lost request changes, unfinished work resumes after recovery, completed idle work makes zero calls |
| P4 | Versioned model profiles, bounded escalation, complete evidence retrieval and compaction; runtime, tools, account policy and usage views | Supported parameters actually reach the provider; budget holds across retries/escalation/concurrency; oversized evidence remains retrievable; context reduction preserves unresolved scope |
| P5 | Improve Discovery and Fetch; structured intent/candidates, targeted recovery, Media result contract | Correct remake/edition and exact scope; no acquisition from search; partial season pack triggers targeted repair; outage is distinct from no results; returned files satisfy the request |
| P6 | Extend Media assistance and subtitle investigation using existing roles/tools; catalogue, node tools, subtitles | Ambiguous imports preserve identity and await needed input; originals survive; wrong cut/language/drift cases get a grounded result and useful repair path; required subtitles gate readiness |
| P7 | Complete collection-care memory and cost control; curation and scoped memory | New eligible episode creates one authorised job; offline storage never implies missing content; explicit upgrade policy preserves good copies; recovery reuses evidence; fresh sessions cannot evade standing budgets |
| P8 | Evaluate Discovery adapters and record a framework decision | Same evidence/authority/budget/cancellation tests pass; compare outcome quality, latency, complete cost, packaging and maintenance; retain the baseline unless benefits are demonstrated |
| P9 | Packaged application and real-use acceptance; final cleanup/issue-closure audit | Tested runtime/profile matches the deployed one; real request reaches verified playback with requested subtitles; restart, pause/cancel and unavailable-provider cases behave honestly; device/provider limitations and cleanup dispositions are recorded; completed child issues and stale dependencies are closed/resolved |

P5 and P6 can be delivered as separate small slices after the shared contracts.
Avoid coupling a runtime rewrite to another visual redesign. Keep technical
evidence in request details and administrator diagnostics; ordinary users need
to know what is ready, what is waiting, why, and the next useful action.

## Evaluation and release gates

Build a fixed, versioned set of at least 30 cases across the five roles, covering
ordinary tasks and the known failure modes. Run each candidate at least three
times; keep an unseen holdout set and record sample size. Replays use disposable
state and controlled sources/files. Paid calls and real-provider acceptance are
separate recorded runs; none were performed for this planning audit.

Measure correct completion, wrong-identity/scope attempts, accepted false
readiness, unnecessary downloads, useful clarification, recovery success,
latency, model calls, escalations and total estimated spend. Review actual media
and subtitle results where fixtures alone cannot prove viewer quality.

Release gates are zero accepted out-of-scope writes, false-ready completions or
lost originals in the defined suite; zero model calls for unchanged completed
subscriptions; and no unexplained quality regression when selecting a cheaper
profile. Treat forbidden attempted actions as model-quality failures even when
tools successfully block them. Passing a finite suite is evidence, not a claim
of universal correctness. Establish latency/cost targets from the baseline and
the household's chosen budget before making them release thresholds.

The final pilot must include a straightforward movie, a difficult title match,
an incomplete season pack, one newly aired followed episode, a subtitle repair,
and an interrupted/resumed request. Capture deployed revision, runtime/profile
versions, evidence receipts, actual playback and estimated spend. Existing
physical Windows, mobile Safari and live-provider gaps remain visible until
those checks are actually completed.

External sharing/DNS/HTTPS stage 7, TV/Emby/casting, music, arbitrary connector
generation and the setup agent remain outside this plan. The recommended first
implementation is **P1 + P2, followed by P3**, so stronger agents are selected and
introduced against meaningful evidence and reliable recovery.
