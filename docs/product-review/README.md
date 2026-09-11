# Product review evidence

These artifacts support the [product review](../PRODUCT_REVIEW.md) of Sparrow at `b84450c`, performed on 9 September 2026. They are observations of the current application, not redesigned screens.

**Method.** The production-built React frontend ran in headless Chrome through Playwright. The two browser viewports were 1440 × 1000 and 390 × 844. Seven page components were inspected across eleven baseline route/state combinations at both widths. Axe checked the desktop baseline states. Additional interactions covered keyboard activation, form persistence, request scope, reloads, service failures and navigation.

The actual FastAPI application and routes ran against isolated temporary SQLite state. A review harness substituted catalogue artwork/metadata and downloader responses, and prevented agent wakes and background acquisition. Fictional titles represented active, paused, completed and abandoned requests; partial TV ownership; an unowned monitored show; verified/unverified movie records; and a missing file. Placeholder files were not real media. No inference about actual playback or ffprobe verification is drawn from those seeded records. Separate backend diagnostics exercised the real tools and runtime against temporary files and fake external actions.

`evidence.json` has five result groups: `browser_baseline`, `browser_interactions`, `browser_additional`, `isolated_backend`, and `additional_backend`. Observation names in the main report are keys within those groups. The description-search result comes from the installed Anthropic SDK rejecting the call before network activity. The step-limit diagnostic uses a finite scripted response sequence, so its 46 calls are simulated provider responses, with no paid usage.

The backend diagnostic is preserved in [backend_reproductions.py](backend_reproductions.py). With the repository's Python dependencies installed, run it from the repository root using `.venv/bin/python docs/product-review/backend_reproductions.py`. It prints JSON, uses temporary storage, disables service wake scheduling and substitutes downloader/model actions. It demonstrates current behaviour; it is not a regression test asserting that behaviour should continue. The browser screenshots require the synthetic fixture state and are not a claim that a clean installation contains these titles.

**Capture notes.** Most images are full-page captures, so a phone image can be much taller than the viewport. Fixed drawers remain viewport-height even in a full-page screenshot; background below a drawer in that image is not evidence of a modal-height defect. Progress images were recaptured after supplying consistent downloaded-byte counters in the synthetic transfer fixture. The initial counter discrepancy is excluded from findings. Automated accessibility results describe the captured desktop states and do not constitute a full conformance audit.

| Screen/state | Desktop | Phone |
|---|---|---|
| Initial setup | [Image](onboarding-desktop.png) | [Image](onboarding-mobile.png) |
| Discover / home | [Image](home-desktop.png) | [Image](home-mobile.png) |
| Populated Library | [Image](library-desktop.png) | [Image](library-mobile.png) |
| Active TV request / Watch | [Image](show-active-desktop.png) | [Image](show-active-mobile.png) |
| Active TV request / Progress | [Image](show-progress-desktop.png) | [Image](show-progress-mobile.png) |
| Title Preferences | [Image](show-preferences-desktop.png) | [Image](show-preferences-mobile.png) |
| Completed season, another missing | [Image](show-completed-season-desktop.png) | [Image](show-completed-season-mobile.png) |
| Movie marked ready | [Image](movie-ready-desktop.png) | [Image](movie-ready-mobile.png) |
| Settings | [Image](settings-desktop.png) | [Image](settings-mobile.png) |
| Activity | [Image](activity-desktop.png) | [Image](activity-mobile.png) |
| Manual release browser | [Image](release-browser-desktop.png) | [Image](release-browser-mobile.png) |

| Additional interaction | Capture | Recorded observation |
|---|---|---|
| Save download cap of one | [Image](settings-ignored-save.png) | UI reports Saved; value returns to three; config PATCH drops the field |
| Invalid overlapping folders | [Image](settings-invalid-no-feedback.png) | Backend returns validation error; Settings provides no visible error |
| Get a TV search result | [Image](search-scope-before-get.png) | Title-only request expands to all nine episodes across three seasons |
| Search API unavailable | [Image](search-outage-as-no-results.png) | Error presented as no results and spelling advice |
| Empty Library | [Image](library-empty.png) | Existing empty-state copy and available actions |
| Library details drawer | [Image](library-drawer-mobile.png) | Mouse opens drawer; Enter on Details navigates; focus stays outside drawer |
| Existing active request in search | [Image](search-active-mobile.png) | Card still offers Get |
| Abandoned request without inventory | [Image](show-abandoned-mobile.png) | Title no longer selects the abandoned request/history |
| Expanded TV episodes | [Image](show-episodes-mobile.png) | Episode numbers are informational spans, without play/request actions |
| Untested setup labelled ready | [Image](onboarding-false-ready-mobile.png) | Nonexistent folders, fake keys and untested client still allow completion |

**Code pointers for reproduced and source-traced issues.** Line numbers refer to the reviewed commit; the linked functions are the durable reference. “Browser” means application interaction with fixture state. “Isolated” means real backend code with fake external actions. “Source” means code tracing without an end-to-end external-service reproduction.

| Finding | Evidence type | Code location |
|---|---|---|
| Missing playback / episode actions | Browser + source | [Show.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Show.tsx), `EpisodePill` at 82 and `WatchTab` at 976 |
| Ready ignores verification | Browser/API | [library_view.py](../../backend/services/library_view.py), inventory projection at 117 |
| Movie detail trusts stored verification | Browser/API | [main.py](../../backend/main.py), movie detail at 1634 |
| Completion accepts missing file / does not enforce audio | Isolated | [tools.py](../../backend/agents/tools.py), completion tool at 741 |
| TV scan assigns verified from filenames | Isolated | [file_organizer.py](../../backend/services/file_organizer.py), `scan_show_episodes` at 245 |
| Search Get defaults to all seasons | Browser/API | [Home.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Home.tsx), `handleGet` at 291; [service.py](../../backend/agents/service.py), `create_job` at 468 |
| Description call missing required argument | Installed SDK | [resolution.py](../../backend/agents/resolution.py), `_describe_to_titles` at 57 |
| Episode request summarised as season | Isolated | [models.py](../../backend/agents/models.py), `Mandate.describe` |
| Complete job hides next season action | Browser + source | [Show.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Show.tsx), action condition at 1059, `loadJob` at 1145, header at 1468 |
| Cannot add a second active request for title | Source | [main.py](../../backend/main.py), create-job route at 1790 |
| Active search card still offers Get | Browser + source | [Home.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Home.tsx), `ResolveCardTile` |
| Abandoned job omitted from title/library selection | Browser + source | [Show.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Show.tsx), `loadJob`; [library_view.py](../../backend/services/library_view.py), job-status filter at 142 |
| Concurrent-download field dropped | Browser/API | [main.py](../../backend/main.py), `ConfigUpdate` at 854; [Settings.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Settings.tsx), save handler at 117 |
| Concurrent limit race | Isolated | [tools.py](../../backend/agents/tools.py), acquisition check/action at 622 |
| Pause/cancel do not reject an in-flight acquisition | Isolated + source | [service.py](../../backend/agents/service.py), pause at 536 and cancel at 583; [runtime.py](../../backend/agents/runtime.py), `_turn` at 247; acquisition tool above |
| Auto-organise setting only used by legacy handling | Source | [main.py](../../backend/main.py), legacy download handling at 366–415; [prompts.py](../../backend/agents/prompts.py), media instructions at 145 |
| Zero seeding-ratio semantics conflict | Source | [Settings.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Settings.tsx), ratio hint at 348; [main.py](../../backend/main.py), startup sweep at 777 |
| Error treated as first-run state | Browser failure injection | [App.tsx](../../frontend/src/App.tsx), setup-status error handling at 19 |
| Settings errors invisible / endless loading | Browser failure injection | [Settings.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Settings.tsx), fetch at 109 and save at 117 |
| Search outage treated as no matches | Browser failure injection | [Home.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Home.tsx), resolver catch at 259 |
| WebSocket reconnect after unmount | Browser + source | [useWebSocket.ts](../../frontend/src/hooks/useWebSocket.ts), reconnect at 24 and cleanup at 34 |
| Stale statistics called stalled | Source + rendered state | [Library.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Library.tsx), status line at 181 |
| Setup completeness uses field presence | Browser + source | [Onboarding.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Onboarding.tsx), readiness at 124 and finish at 194 |
| Scan misses current agent TV layout | Isolated + source | [file_organizer.py](../../backend/services/file_organizer.py), layout at 225; [prompts.py](../../backend/agents/prompts.py), convention at 154 |
| Rescan replaces richer inventory | Source | [main.py](../../backend/main.py), scan update at 2206 |
| Unmatched catalogue entries merge | Isolated | [library_view.py](../../backend/services/library_view.py), identity key at 96 |
| Unowned monitored show absent from normal overview | Isolated + API | [tools.py](../../backend/agents/tools.py), Librarian overview at 1096; [service.py](../../backend/agents/service.py), monitoring save at 455 |
| Title quality resets on reload | Browser + source | [Show.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Show.tsx), local quality state at 1112 |
| Cached poster path rewritten incorrectly | Browser + source | [Library.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Library.tsx), `posterUrl` at 83; [metadata_service.py](../../backend/services/metadata_service.py), cached path at 286 |
| Keyboard Details differs from mouse | Browser + axe | [Library.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Library.tsx), card keyboard handler at 225 and nested Details at 275 |
| Drawer lacks modal semantics/focus | Browser + source | [Library.tsx](https://github.com/sf-chris/sparrow/blob/b84450c/frontend/src/pages/Library.tsx), drawer aside at 346 |
| Advisory 40-step limit can be exceeded | Isolated runtime | [runtime.py](../../backend/agents/runtime.py), limit handling at 303 |

**Unvalidated areas.** This review does not establish live catalogue/provider availability, real acquisition success rates, file playback compatibility, actual remote-node behaviour, accessibility on assistive devices, or performance with a substantial collection. The proposed screen hierarchy, first-request defaults and notification preferences are product recommendations to validate through real use, not findings from user interviews.
