# Sparrow product review

Reviewed 9 September 2026, against `b84450c` on `main`.

Follow-up: the owner accepted these findings and added subtitle alignment,
agentic discovery/management, mobile use, self-hosted accounts/sharing and TV
playback. The resulting target is in [PRODUCT_PLAN.md](PRODUCT_PLAN.md) and
the updated [roadmap](../ROADMAP.md). This review retains its original
baseline observations; subsequent plan changes are not implemented fixes.

**Sparrow has useful foundations for finding and acquiring media. Its biggest product opportunity is completing the experience of owning and watching a collection.** Today, the strongest workflows concern requests and agent activity. The user still has to bridge the gap between an entry being labelled ready and actually watching the right episode, in the right language, on the device they are using.

There is also a more immediate issue: several reassuring labels and controls are inconsistent with the underlying behaviour. “Ready,” “Get,” “Saved,” “Paused,” and “Monitoring” need precise meanings that hold across the application. Visual polish will have much greater value once those promises are dependable.

This review is about the product: the experience Sparrow should provide, the current obstacles, and the changes that would matter most. It does not prescribe packaging, a deployment architecture, or an implementation schedule.

**What was reviewed.** All seven page components and their principal routes; eleven representative screen/state combinations at desktop and phone widths, producing 22 baseline captures; additional keyboard, form, navigation, request, and failure scenarios; and the code behind configuration, resolution, requests, agent lifecycle, monitoring, inventory, scanning, and live updates. The existing repository check had passed: 42 tests discovered, 40 passing and two opt-in live-model evaluations skipped, alongside the build/release checks.

The browser ran the actual built frontend and application routes against isolated SQLite fixtures. External catalogue and downloader responses were substituted; agent wakes and background acquisition were disabled. Additional reproductions exercised real backend tools and the agent runtime with temporary files and fake external actions. The description-search failure was reproduced using the installed SDK, before any provider request. No real media was acquired, no paid model workflow was exercised, and playback quality, actual download reliability, Windows operation, and behaviour at a large library size remain unvalidated. Placeholder artwork and fictional titles are test data, not visual-design findings.

The [screenshot index and methodology](product-review/README.md) and [machine-readable observations](product-review/evidence.json) accompany this review. Findings below distinguish observed behaviour, code-level evidence, and proposed product capabilities.

**The product promise to design around.** “Tell Sparrow what you want to watch. It respects your choices, keeps the collection usable, and makes the next thing easy to play.” For your Linux server and eventual Windows storage machine, that promise includes explaining when something is stored elsewhere and temporarily unavailable. Users should be able to understand that without learning the agent architecture.

| User goal | Current position | What a good experience requires |
|---|---|---|
| Find a title | Direct-title search and poster cards exist; the advertised description fallback fails locally | Reliable exact and descriptive search, useful disambiguation, clear outage handling |
| Request what I mean | Movies and season requests exist; search-card Get defaults to the entire TV show | Visible episode/season scope, understandable defaults, separate future monitoring |
| Trust it to arrive | Jobs, transfers, verification tools and journals exist; important controls and completion checks have gaps | Enforced intent, honest readiness, recoverable requests, bounded resource use |
| Watch something | Coverage is displayed; playback/handoff and viewing progress are absent | Play or explicit player handoff, resume, next episode, audio/subtitle choices |
| Keep a show current | Stored permissions and a Librarian exist; unowned subscriptions fall outside its normal overview | Visible subscriptions before ownership, upcoming episodes, clear check/recovery state |
| Bring my existing collection | A folder scan exists; layout, identity and verification issues undermine it | Preview, matching, preserved file evidence, duplicate/unmatched resolution |
| Manage my collection over time | Basic shelves, filters and removal exist | Findability at scale, watched state, space management, locations and reversible actions |

**Keep the useful foundations.** The warm dark palette and poster-led browsing give Sparrow a recognisable visual identity. The separation of Watch, Progress and Preferences is understandable. Plain-language journal entries can explain delays well. Explicit monitoring permissions, masked configuration secrets, and the removal copy explaining that files are retained are valuable foundations. Preserve those ideas while improving their implementation.

At the reviewed baseline, `DESIGN.md` declared “The journal is the product.” The review recommended “The viewing experience is the product; the journal explains the work.” A journal is especially useful when a request is delayed or uncertain. A returning viewer usually needs the next episode and a working action first. The subsequent owner-approved plan now incorporates that principle in the architecture guidance; the application itself remains unchanged.

**Priority definitions.** “Essential” means a core user promise is missing or unreliable. “Daily use” means a substantial improvement to repeated use once those promises hold. “Expansion” means useful later scope. These are product priorities, not security-severity ratings or delivery phases.

**1. Complete the journey from ready to watching. — Essential**

The movie Watch tab says “In your library — ready to watch” but offers no Play action or player handoff. The TV view expands into numbered episode pills; these are non-interactive spans. There is no viewing history, resume position, next-episode experience, or user-facing audio/subtitle selection. This is a missing capability relative to the one-stop-shop ambition, rather than a regression in an existing player.

Make the title page answer three questions immediately: what can I watch, what should I watch next, and how do I start it? A ready movie should offer Play; an interrupted movie should offer Resume with Start over nearby. A show should offer the next unwatched available episode. If playback is delegated to another application, provide a clear, functioning “Open in …” action and define how progress returns to Sparrow. Copying a filesystem path is useful for troubleshooting but leaves the normal journey unfinished.

Replace the episode-number cloud with compact rows containing episode number, title, runtime, availability, watched state and the appropriate action. Keep upcoming episodes visible with their air dates. Audio and subtitles are part of whether a copy is suitable, so expose their availability before playback and allow defaults with per-item overrides.

**Success:** a returning user can open Sparrow and resume an available item in one deliberate action. Starting an episode does not require inspecting a file path or the agent journal.

Evidence: [ready movie on mobile](product-review/movie-ready-mobile.png), [episode view](product-review/show-episodes-mobile.png); `WatchTab` and `EpisodePill` in [Show.tsx](../frontend/src/pages/Show.tsx).

**2. Give “ready” one dependable meaning. — Essential**

The Library projection marks inventory entries ready even when their verification flag is false. In the fixture, an unverified movie appeared ready in Library while its movie-detail API said it was not in the library. A verified inventory entry whose file was absent appeared ready as well. Separately, the completion tool accepted a missing movie file whose recorded audio was French despite an English job preference. The scanner marked a 24-byte text file named like an episode as verified.

These are different defects sharing one product consequence: the user cannot rely on the status at the moment it matters. Distinguish requested, acquiring, checking, available, unavailable and needing attention. Track the facts behind those labels: identity, acceptable quality/language, verification evidence, and current storage availability. Validate availability when playback begins; remote availability can change after the last background check.

Show partial success explicitly: “Episode 1 ready; episodes 2–3 downloading.” A requested season being complete is separate from owning every season, and both are separate from whether the storage machine is online. Keep those distinctions consistent across search cards, Library, title pages and notifications.

**Success:** the same item has compatible states everywhere; an offline copy remains visible with an availability explanation; imported filename guesses never become verified media evidence.

Evidence: `library_states`, `unverified_movie_detail`, `missing_movie_detail`, `complete_missing_file_wrong_audio`, and `scan_text_file` in [evidence.json](product-review/evidence.json). Code: [library_view.py](../backend/services/library_view.py), [tools.py](../backend/agents/tools.py), [file_organizer.py](../backend/services/file_organizer.py).

**3. Make the acquisition scope obvious before accepting it. — Essential**

Clicking Get on a TV search result sent only the title ID and media type. The server expanded the fixture into all nine episodes across three seasons. The card's Options expose quality, minimum quality, audio and urgency, but no season or episode scope. The backend default includes every numbered season with an episode count, without restricting that initial expansion to already aired episodes.

The product should offer a compact request sheet: try the first episode, choose seasons/episodes, or get all available episodes. Future monitoring should be an explicit, separate choice. Once a user's default is established, a specific label such as “Get season 1” can preserve fast requests. Whole-show acquisition should say how much content is being requested; show a size estimate or explain when it is not yet known.

Preserve scope expressed in search. “Get season 2 of …” must not become a title-only card followed by a whole-show request. Also replace ambiguous defaults: “Best available” currently becomes the configured quality preference, and “Anything watchable” falls back to the server's minimum. Label these as the actual inherited settings, or make their behaviour match their wording.

The advertised description search also needs a functional repair: its model call omits the required `max_tokens` argument. The installed SDK raises before contacting the provider, and the resolver catches that failure and returns no inferred titles. Keep direct-title search useful when the interpretation service fails, and tell the user when description interpretation is unavailable.

Use an intent summary such as “Season 2 · 8 episodes · 1080p preferred · English audio · future episodes off.” Even the current mandate summary loses precision: a request for episode 2 alone is described as “Requested: Season 1.”

**Success:** before any acquisition begins, the user can correctly predict its scope, preferences and future authority.

Evidence: [search before Get](product-review/search-scope-before-get.png), `search_get` and `single_episode_summary`; [Home.tsx](../frontend/src/pages/Home.tsx), `create_job` in [service.py](../backend/agents/service.py), and `Mandate.describe` in [models.py](../backend/agents/models.py).

**4. Make requests continue naturally as the collection grows. — Essential**

A completed season-one job is still selected by the title page. Its presence hides Get season for missing season two, while the header says “Sparrow is on it.” A genuinely active job also prevents creating another request for that title. There is no obvious way to extend the current scope. Conversely, an abandoned request with no inventory disappears from the Library projection and the title's selected job, making its previous failure difficult to understand.

Treat the title as the durable home for ownership, preferences and request history. A completed request should expose the next useful action. An active request should accept additional explicit scope, or explain a queued follow-on request. An unsuccessful request should remain visible with its reason and a recovery action. Search cards should show the real existing request state; they currently ignore the API's active-job annotation and offer Get again after navigation/reload.

**Success:** request season one, finish it, request season two, pause it, return later and recover a failure without losing context or creating accidental duplicate work.

Evidence: [completed-season trap](product-review/show-completed-season-desktop.png), [abandoned title](product-review/show-abandoned-mobile.png), [already-requested search result](product-review/search-active-mobile.png). Code: `loadJob`, `WatchTab` and the header in [Show.tsx](../frontend/src/pages/Show.tsx); [main.py](../backend/main.py) job creation; [library_view.py](../backend/services/library_view.py).

**5. Make controls authoritative and their effects visible. — Essential**

Changing Concurrent downloads from three to one displayed “Saved,” then returned to three. The request schema silently drops this field. Independently, two concurrent calls to the acquisition tool both passed its limit check when the configured limit was one. A replay using an already-running session context also accepted an acquisition after pause and after cancellation. These were isolated tool-level reproductions with fake downloader actions, not observed live downloads.

Pause and cancel need defined outcomes across work already underway. Show a transitional acknowledgement when necessary, then report the achieved state. Prevent new consequential actions after the relevant request authority changes. Explain whether existing files, partial transfers and future monitoring are retained. A download cap must govern concurrent decisions as well as sequential ones.

Several preference contracts also need correction. “Organize silently” is consulted by the legacy path, while the current Media Agent is instructed to organise automatically. “0 means no ratio limit” conflicts with a startup sweep that treats zero as disabled seeding and removes completed torrents from the downloader while retaining files. These are source-backed inconsistencies; the seeding sweep was not exercised against a real client.

**Success:** Saved means the value persisted; Paused means the agreed work has stopped; Cancelled work cannot restart through an in-flight action; every exposed setting has one documented effect in the current workflow.

Evidence: [ignored saved setting](product-review/settings-ignored-save.png), `config_patch`, `settings_save`, `concurrent_limit`, `add_after_pause`, `add_after_cancel`; [main.py](../backend/main.py), [service.py](../backend/agents/service.py), [runtime.py](../backend/agents/runtime.py), [tools.py](../backend/agents/tools.py).

**6. Make failures legible and recoverable. — Essential**

An injected search-service failure became “Nothing found” with advice to check spelling. A failed setup-status request redirected an established user into onboarding. A failed settings fetch left an indefinite loading screen. Invalid overlapping folders produced a server validation error but no visible settings error. WebSocket navigation also left three live connections after starting with one, because closing an unmounted page's connection schedules reconnection; reconnecting does not explicitly request a fresh snapshot.

Design distinct empty, loading, unavailable and failed states. Retain cached content during temporary outages, label its freshness and provide Retry. Do not make existing ownership depend on a live metadata service: title-detail routes currently fetch external metadata even for owned titles. Keep a persistent place for blocked requests with the cause, what Sparrow will do next, and what the user can do now.

Replace vague recovery with concrete actions: reconnect the downloader, change a destination, retry a search, relax a preference, or report a wrong file. Separate a stale progress report from an actually stalled transfer; the Library currently labels stale statistics “Stalled.”

**Success:** a temporary outage does not masquerade as an empty collection, a spelling error or a new installation. Returning after a disconnect produces a consistent, current view.

Evidence: [outage presented as no results](product-review/search-outage-as-no-results.png), [invisible validation failure](product-review/settings-invalid-no-feedback.png), `bootstrap_outage`, `settings_outage_text`, `initial_sockets`, `settings_sockets`; [App.tsx](../frontend/src/App.tsx), [Settings.tsx](../frontend/src/pages/Settings.tsx), [useWebSocket.ts](../frontend/src/hooks/useWebSocket.ts).

**7. Make setup prove the first useful outcome. — Daily use**

Onboarding's six checks are primarily checks that fields are non-empty. Fake API keys, nonexistent folders and an untested downloader configuration produced “Ready” and allowed completion. The same screen still showed health issues from the earlier configuration. The initial mobile setup capture was about 3,200 pixels tall, with promotional explanation, duplicated readiness information and technical settings competing for attention. Its closing copy mentions “Compare first” and an older request flow that the current primary interface does not provide.

Organise setup around connecting a usable library, deciding whether to import existing media or acquire something, and proving the chosen path. Distinguish “details entered” from “connection verified.” Test credentials and folder access against the actual process or selected machine that will use them. Explain which machine a path belongs to. Allow a clearly labelled incomplete setup to be saved, without reporting the system ready for acquisition.

A new user should reach one recognisable success: an existing movie imported and available, or a precisely scoped first request accepted. Keep advanced quality and model configuration available later. An existing-library path should not force the user to finish every acquisition-service setup step before getting value from ownership.

**Success:** readiness corresponds to a working capability, and setup ends with a usable item or an understandable request rather than another configuration screen.

Evidence: [initial mobile setup](product-review/onboarding-mobile.png), [false-ready setup](product-review/onboarding-false-ready-mobile.png); readiness and finish handlers in [Onboarding.tsx](../frontend/src/pages/Onboarding.tsx).

**8. Treat importing an existing library as a major product journey. — Daily use**

The current Scan operation assumes `Movies/<folder>` and `TV Shows/<series>`. The Media Agent's TV naming instruction uses `<library>/<show>/Season …`, so the scanner missed a fixture following that convention. Scanning an existing show replaces its episode map with filename-derived records, discarding richer verification/provenance fields. Two distinct unmatched movies without a catalogue ID collapsed into one Library entry in a separate reproduction.

The UI exposes a single Scan button and a count. It offers no preview of likely matches, unmatched items or duplicate candidates. For a person bringing an established collection, this makes the first impression fragile and correction expensive.

Provide an import preview with matched, uncertain, duplicate and unsupported groups; let users correct title/season matches before accepting uncertain changes. Preserve known evidence when rescanning unchanged files. Report progress and useful results, including refreshed entries and unresolved issues. Support the layouts Sparrow itself creates and make supported import layouts explicit. Unmatched media must retain its own identity and remain browsable.

**Success:** scanning twice preserves a correct catalogue, never merges unrelated unknown titles, and gives the user a practical way to resolve uncertainty without renaming an entire collection manually.

Evidence: `scan_text_file`, `scan_agent_layout_found`, `unmatched_library`; [file_organizer.py](../backend/services/file_organizer.py), scan route in [main.py](../backend/main.py), naming instructions in [prompts.py](../backend/agents/prompts.py), [library_view.py](../backend/services/library_view.py).

**9. Make monitoring visible even before a show is owned. — Daily use**

Monitoring permissions are already modelled explicitly, which is useful. However, the normal Librarian overview enumerates owned TV entries. A stored keep-current mandate for an unowned show was absent from that overview and from Library. Saving monitoring changes does not itself wake the Librarian. There is no central list of followed shows or upcoming episodes.

Treat following a title as a durable user choice independent of ownership and the current request. A followed show should appear immediately, including when nothing has aired or downloaded yet. Show the monitoring policy, next relevant air date, last check and any blocking issue. Distinguish Save for later, get these existing episodes, and acquire new episodes as they air. Explicitly explain whether turning monitoring off affects an already active request.

Avoid quietly expanding acquisition authority when the user removes an item, cancels a request or changes a preference. Those actions need compatible, understandable meanings rather than relying on the user to infer the relationship between inventory, jobs and mandates.

**Success:** follow an unowned show today, find it again tomorrow, understand what will happen when an episode airs, and stop future acquisition predictably.

Evidence: `mandate_unowned`, `unowned_monitor_visible_to_librarian`; `set_monitoring` in [service.py](../backend/agents/service.py), `library_overview` in [tools.py](../backend/agents/tools.py), Preferences in [Show.tsx](../frontend/src/pages/Show.tsx).

**10. Give Home, Library and Activity distinct everyday jobs. — Daily use**

Discover currently serves as both search and a small collection landing page. Library repeats titles across recently added and type-based shelves, which makes a small collection feel longer without making it easier to find something. Activity puts agent sessions and model/token/cost detail ahead of the progress updates many users would be looking for. The title's technical drawer partly duplicates the main title page.

Use Home for Continue watching, Next episode, Newly ready and a compact Needs attention summary. Keep a prominent search entry and support discovery when the user wants something new. Use Library for the complete collection, with stable search, sort and filters for type, availability, watched state and monitored status. Provide a denser list option for larger collections. Keep file/location details available from the title page without creating a second competing title experience.

Use Activity as the place to understand active and blocked requests: title, requested scope, useful status, next action and controls. Put the journal directly behind each request and group technical execution details and cost analysis in an expandable diagnostics area. The manual release browser is useful expert depth, but its instructions still reference “Downloads”; align that entry point and terminology with the current navigation.

**Success:** a user knows where to watch, where to find owned media and where to fix a request. Normal use does not require reading session types or token tables.

Evidence: [Home](product-review/home-desktop.png), [Library](product-review/library-desktop.png), [Activity](product-review/activity-desktop.png), [release browser](product-review/release-browser-desktop.png).

**11. Make preferences coherent, persistent and easy to change. — Daily use**

Title quality choices are local component state and reset on reload. The page does say they apply to new requests from that page, but placing them in Preferences beside persistent monitoring creates two different persistence models in one surface. Audio, minimum quality and urgency appear on search-card Options but are not equally accessible from the title's preference flow. Settings shows models and agent prompts before its Advanced section, and the mobile form is about 3,250 pixels tall.

Define global defaults, title overrides and request-specific choices. Show inheritance plainly: “Using your default: 1080p, English.” Saving a title override should persist and explain whether it affects active work or future requests. Use practical quality profiles with an indication of the quality/storage trade-off, while retaining exact controls for users who need them. Separate normal viewing/library preferences, connections and advanced diagnostics. Keep save feedback and validation close to the edited fields, especially on mobile.

**Success:** a user sets a preference once, sees where it applies, and can predict the effect of changing it. Models and prompts do not have to be understood to manage a collection.

Evidence: `quality_before`, `quality_after`; [title preferences](product-review/show-preferences-desktop.png), [mobile Settings](product-review/settings-mobile.png); [Settings.tsx](../frontend/src/pages/Settings.tsx), [Show.tsx](../frontend/src/pages/Show.tsx).

**12. Make artwork, navigation and accessibility reliable. — Daily use**

Library treats every slash-prefixed image path as a TMDB image path. The metadata service stores cached artwork under `/art/…`, so Library rewrites valid local artwork into the wrong external URL. This explains the broken posters in the fixture Library capture; the finding concerns URL handling, not the synthetic art itself. A failed image also needs a proper title-based fallback.

Phone layouts avoid document-level horizontal overflow in the measured viewports, but the horizontally scrolling top navigation clips later items without a strong affordance. The title hero consumes much of the first screen before useful actions and episode information. Compact the phone hero, keep the main action prominent, and preserve search/filter/scroll context when returning from a title. The title's back action currently routes to the home page regardless of where it was opened.

Keyboard testing found that Enter on a Library Details button navigated to the title while a mouse click opened the drawer. The cause is nested interaction: an outer card handles Enter around an inner button. The drawer also lacks dialog semantics and does not move focus inside when opened. Use proper modal focus handling, a labelled dialog, keyboard containment and focus restoration as described in the [W3C modal-dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/).

Automated desktop checks found nested-interactive issues in Library, duplicate main landmarks across multiple views, and insufficient contrast in small Progress, Preferences and Activity text. Measured examples were around 4.0–4.1:1 where normal text requires 4.5:1 under the [W3C contrast criterion](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html). Give visual toggles an accessible checked state and respect reduced-motion preferences. Automated findings are a starting point; this was not a complete WCAG conformance audit.

**Success:** cached posters work consistently, the next action is easy to reach on a phone, and keyboard users can perform the same actions as mouse users without losing focus or context.

Evidence: [mobile title](product-review/show-active-mobile.png), [drawer](product-review/library-drawer-mobile.png), `keyboard_details`, `mouse_details`, and desktop accessibility results; [Library.tsx](../frontend/src/pages/Library.tsx), [metadata_service.py](../backend/services/metadata_service.py), [Layout.tsx](../frontend/src/components/Layout.tsx).

**13. Bound the cost of unattended automation. — Essential for unattended use**

Activity's usage ledger is useful visibility, but visibility alone does not bound spending. The runtime's declared 40-step wake limit sends the model an instruction to stop and resets the counter. A finite scripted model completed 46 calls before stopping voluntarily, demonstrating that the limit is advisory. There is no hard household/day or per-request cost budget exposed in the product. The displayed ledger also covers agent sessions, while description resolution uses a separate model-call path.

Provide enforceable per-request and overall limits, with a clear state when a budget is exhausted. Count every paid model path. Make idle behaviour cheap and predictable; use known events and dates to avoid unnecessary repeated reasoning. Show useful estimates and accumulated cost per request, while labelling rate-based calculations as estimates. Let the user choose whether to increase a budget or leave a request waiting.

**Success:** leaving Sparrow unattended cannot exceed the chosen policy through repeated model turns, and hitting a limit produces a recoverable status rather than a silent failure.

Evidence: `wake_step_limit`; [runtime.py](../backend/agents/runtime.py), [resolution.py](../backend/agents/resolution.py), [Activity.tsx](../frontend/src/pages/Activity.tsx).

**14. Close the loop when media is wrong and when the user is away. — Daily use**

The current product mainly provides journal inspection and a generic nudge. It lacks a direct way to report that a supposedly ready item is the wrong episode, has unsuitable audio, or has a subtitle problem. There is also no notification preference or delivery flow for a request becoming usable or requiring intervention.

Add contextual “Report a problem” actions with concrete choices and a short free-text explanation. Preserve the current usable copy during a replacement attempt, and show what the new attempt is trying to improve. Notify on meaningful outcomes: the next requested episode is ready, a request needs a decision, or storage prevents watching. Avoid reporting every agent step. Offer in-app notifications first, with optional external delivery and quiet hours when needed.

**Success:** the user can correct a bad result without understanding its download history, and can leave the app closed while Sparrow works.

**15. Make the collection manageable as ownership grows. — Daily use, with location support tied to node expansion**

Basic Remove is clear about retaining files, but the product has no complete workflow for freeing space, resolving duplicate copies, moving items, protecting favourites, or distinguishing watched from unwatched media. These are central to managing a collection over months, especially with a final storage machine.

Provide collection search and useful sort/filter controls first. Then add deliberate bulk actions, a space view, duplicate inspection and separate actions for removing a catalogue entry, deleting a copy, and stopping future acquisition. Where practical, make catalogue changes reversible. Explain when a future scan will rediscover a retained file.

For multiple storage machines, display human names, availability, capacity and which copies live where. “Stored on Windows PC · offline since yesterday” is useful. A path alone is insufficient. Keep owned items visible during disconnection, and explain whether an acquisition is waiting for destination space or connectivity. These are product requirements for the node ambition; they do not select a transport or deployment design.

**Success:** a user can understand where their media is, recover space intentionally, and reconnect a storage machine without losing the collection's identity or request history.

**A coherent set of target journeys.** The findings above should converge on these ordinary experiences:

| Journey | Proposed flow | Decisive quality check |
|---|---|---|
| Try a new show | Search → identify the right version → choose first episode or season → see exact request summary → receive readiness → Play | No accidental whole-show request; episode one remains playable while later episodes arrive |
| Return tonight | Home → Resume or Next episode → choose audio/subtitles if needed → watch | Progress survives returning; unavailable storage is explained before a failed play attempt |
| Follow a current show | Title → follow new episodes → Upcoming entry → episode becomes available → notification | The subscription works before any episode is owned and can be stopped predictably |
| Bring existing files | Select library/location → scan preview → resolve uncertain matches → import → browse/play | Rescans preserve evidence and unrelated unmatched files remain distinct |
| Recover a problem | Needs attention → plain reason → specific action → persistent request resumes | No lost failure history, duplicate request or hidden retry loop |
| Free space | Filter watched/large items → inspect copies → choose removal effect → review result | The user understands which files and monitoring choices are affected |

**Screen-by-screen direction.** Each current surface was inspected; these changes give each a clear purpose.

| Surface | Most important change | Evidence capture |
|---|---|---|
| Onboarding | Shorten the path to imported media or a first request; make readiness an actual capability check | [Desktop](product-review/onboarding-desktop.png), [phone](product-review/onboarding-mobile.png) |
| Discover/Home | Prioritise returning-viewer actions; retain prominent search and useful discovery | [Desktop](product-review/home-desktop.png), [phone](product-review/home-mobile.png) |
| Search result cards | Show ownership/request state; accept explicit scope and retain query intent | [Request state](product-review/search-active-mobile.png), [Get](product-review/search-scope-before-get.png) |
| Title / Watch | Play, Resume or a precise request as the main action; informative episode rows | [Movie](product-review/movie-ready-desktop.png), [TV](product-review/show-active-desktop.png), [episodes](product-review/show-episodes-mobile.png) |
| Title / Progress | One concise outcome/status, then actionable problems, request controls and journal | [Desktop](product-review/show-progress-desktop.png), [phone](product-review/show-progress-mobile.png) |
| Title / Preferences | Persist overrides, show inherited defaults, separate acquisition scope from future monitoring | [Desktop](product-review/show-preferences-desktop.png), [phone](product-review/show-preferences-mobile.png) |
| Library | Correct artwork and state; improve findability, import and collection actions | [Desktop](product-review/library-desktop.png), [phone](product-review/library-mobile.png), [empty](product-review/library-empty.png) |
| Library drawer | Make it accessible; focus it on copy/file details that supplement the title page | [Phone](product-review/library-drawer-mobile.png) |
| Activity | Lead with active/blocked requests; let users expand journal, cost and execution detail | [Desktop](product-review/activity-desktop.png), [phone](product-review/activity-mobile.png) |
| Settings | Make saves truthful; organise defaults, connections and advanced diagnostics | [Desktop](product-review/settings-desktop.png), [phone](product-review/settings-mobile.png) |
| Manual release browser | Keep expert selection optional; correct obsolete navigation copy and connect actions to title/request context | [Desktop](product-review/release-browser-desktop.png), [phone](product-review/release-browser-mobile.png) |

**What to prioritise in the product discussion.** First establish the complete request-to-watch experience and make its labels and controls trustworthy: findings 1–6 and the unattended limits in 13. Then make importing, following shows, preferences, navigation, accessibility and feedback work well enough for everyday use. Collection location/capacity becomes essential as the storage-node experience arrives. This is a dependency judgement about user value, not a proposed delivery schedule.

Music, social features, elaborate recommendations and extensive household/profile management are expansion scope. They should not distract from the media ownership and watching loop. Music will also need its own concepts—artists, albums, tracks and listening queues—rather than inheriting TV episode assumptions. Keep that future possibility open without exposing empty product areas now.

**How to know the product has improved.** Evaluate successful first use, time/actions to start an available item, scope comprehension, readiness accuracy, successful recovery after interruption, scan repeatability, and unattended cost. Exercise these with realistic small and large collections, poor metadata, subtitle/audio variations, disconnected storage and a real player. Passing backend tests and attractive screenshots do not establish those outcomes. For this version, the evidence already identifies concrete improvements; observation of real usage should validate the proposed defaults and screen hierarchy.
