# Sparrow product plan

Updated 9 September 2026 following the product review and the owner's additions. This is the target product and decision record; capabilities described here are planned, not claims about the current alpha. [ROADMAP.md](../ROADMAP.md) records the work and acceptance gates. The [product review](PRODUCT_REVIEW.md) preserves the dated baseline; [IMPLEMENTATION.md](IMPLEMENTATION.md) records current measured behavior, and the [node plan](DISTRIBUTED_NODES_PLAN.md) defines the execution boundary.

**Latest clarification.** Built-in subtitle automation does the routine work; an agent reviews its output and investigates failures. Admin onboarding establishes inheritable defaults, with personal overrides and effective preferences passed to agents. The setup agent and all TV work are parked, including Emby integration, compatibility research, casting and native TV applications. TV notes below preserve future options only. These changes supersede the earlier implementation recommendations.

**The outcome.** Run the web application on Linux, keep media on Windows and eventually other nodes, and make it effortless to find, acquire, curate and watch a personal collection. The same account can request something from a phone, watch it in a phone or desktop browser with correctly timed subtitles, and resume elsewhere. An owner can invite someone and give them a working, appropriately restricted HTTPS address without assembling undocumented infrastructure.

Mobile use, accounts, guided self-hosting, subtitle preparation, agentic discovery/curation and browser playback are core product scope. The first usable release delivers the install/import/watch journey; the complete agreed product includes the remaining core capabilities. TV/Emby/casting and the setup/deployment agent are parked. Music, broad source expansion and native phone applications remain later scope. The [implementation sequence](../ROADMAP.md#current-implementation-sequence) defines delivery order and completion checks.

**Decisions and recommendations.**

| Area | Direction | Qualification |
|---|---|---|
| Watching | Sparrow owns its player, playback sessions and personal watch state | Reuse established media libraries and ffmpeg; playback must work while the reasoning provider is unavailable |
| TV (parked) | Retain the Samsung/Emby context for a future decision | No current client evaluation, bridge, casting or native-app work; no dependency on TV details for the active roadmap |
| Subtitles | Built-in automatic acquisition/alignment pipeline, followed by agent verification and exception handling | Bundle or vendor selected compatible components; no separate subtitle app/service for the owner to manage |
| Discovery | Add a real tool-using Discovery Agent with conversational refinement and structured results | Retain instant direct-title suggestions; discovery alone cannot initiate unrequested acquisition |
| Agent framework | Evaluate the Claude Agent SDK first for the new discovery session | Preserve Sparrow's durable jobs, permissions and evidence; migrate existing agents only after parity checks |
| Management | Extend Fetch, Media and Librarian; use an agent for subtitle quality review | Predictable subtitle processing runs automatically; the setup agent is parked |
| Mobile | Responsive web application and installable PWA | Phone usability is an acceptance criterion for every flow; offline video is separate future scope |
| Sharing | Local accounts, invitations, per-user progress and explicit permissions | No compulsory external identity provider or Sparrow-hosted account service |
| Preferences | Admin-defined server defaults, optional personal overrides, explicit request choices | One effective-settings resolver feeds UI, agents and validation; admin policy limits remain authoritative |
| Remote access | Guided direct HTTPS and private-network modes, plus a supported relay option when necessary | Domain ownership, ISP routing and client reachability must be verified; a tunnel is not automatically suitable for video |
| Deployment assistance | Guided forms, concrete instructions and deterministic diagnostics | Retain an extension point for a future setup agent; it is not a core deliverable |

**1. Use agents for discovery, judgement and verification; automate predictable processing.**

The existing Fetch, Media and Librarian are already agents: persistent model/tool loops with event wakeups, history and tools. The current title resolver is direct TMDB lookup plus a single description call. Installing an agent SDK would replace parts of the orchestration machinery; it would not, by itself, repair scope, verification or recovery.

| Role | Work it owns | Default reasoning approach |
|---|---|---|
| Discovery | Interpret descriptions and preferences; query catalogue and owned media; disambiguate editions; refine results; produce a precise request proposal | Cheap model for routine resolution; bounded escalation for ambiguous multi-constraint requests |
| Fetch | Inspect acquisition candidates, honour requested scope and urgency, recover from failed sources and deliver the requested item | Use the least expensive tier that passes acquisition evaluations; allow stronger reasoning for difficult cases |
| Media | Identify and verify landed/imported files, organise them, preserve good copies and report gaps | Cheap agent with measured file evidence and targeted escalation |
| Subtitle reviewer | Review the automatic pipeline's evidence and representative samples; identify wrong tracks, accept/reject proposed repairs and investigate complaints | One bounded review for a new/changed track under the review policy, targeted further work on exceptions; no model call for every processing step |
| Librarian | Maintain subscriptions, reconcile gaps, propose upgrades, investigate duplicates and apply authorised curation | Cheap, event-driven sessions over changes and selected audits |
| Setup and support (parked) | Potential future explanation/diagnostic assistant over the normal setup tools | No agent required for setup, account creation, DNS instructions or HTTPS renewal |

Discovery should handle “a funny film under two hours that we haven't watched” and “season two of the British version, English subtitles, keep future episodes off.” The agent can search, inspect results, notice ambiguity and refine. It returns poster cards with a short explanation and a structured intent carrying title identity, episode/season scope, languages and monitoring choice. A description, pasted metadata or a search result must never widen acquisition authority. Explicit user actions or previously granted policies authorise mutations.

Preserve fast autocomplete while an agent handles the richer submitted task. Cancel superseded discovery work, cache factual catalogue responses and retain conversational/search state when opening a title. Ordinary playback, local browsing, pause and account checks must remain available without a model call.

The Claude Agent SDK provides Python/TypeScript loops, sessions and context management, and supports custom tools through an in-process MCP server. That makes it a reasonable first candidate for the existing Anthropic/Python codebase. This is an architectural recommendation, not a completed evaluation. [SDK overview](https://code.claude.com/docs/en/agent-sdk/overview), [custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools).

The SDK trial must demonstrate cancellation, resumption after process loss, structured results, tool restrictions and bounded cost. Run it behind a small adapter. Sparrow's database remains the authority for request state and operation identity; SDK transcripts do not become the only record of an acquisition. Expose domain tools and disable unrelated filesystem/shell tools. Keep credentials in tool-side services and use isolated SDK configuration so a server worker cannot inherit an administrator's personal development tools or permissions.

Every consequential tool rechecks user/role authority, request revision, destination and resource budget. Durable operations prevent duplicate effects after retries. Agents wake on changed facts or bounded scheduled work; unchanged outages and subscriptions do not incur repeated reasoning. Limits must stop calls and new acquisitions in code, including concurrent work. Budget exhaustion becomes an understandable waiting state.

**2. Subtitles become built-in automation with agent quality review.**

The owner installs Sparrow and receives subtitle discovery, alignment and repair as one feature. Prefer well-maintained, pinned libraries or bundled local executables behind Sparrow interfaces. Vendor a small useful core when that gives better control; record its upstream revision, local changes and a test/update process. Do not copy whole applications just to remove a package name. Bundling removes separate installation/configuration, but reused code still has provenance, licences and maintenance needs. External subtitle catalogues remain external sources; local inspection and alignment should continue without them.

ffsubsync's published licence permits reuse with its notices; alass publishes GPLv3 terms. Choose exact components and their transitive dependencies at the integration proof, retain notices/source requirements as applicable, and make no licence changes to Sparrow merely for this plan. [ffsubsync licence](https://raw.githubusercontent.com/smacke/ffsubsync/master/LICENSE), [alass licence](https://raw.githubusercontent.com/kaegi/alass/master/LICENSE).

Use a tested built-in sequence to inspect, retrieve, align, validate and prepare a candidate. The verification agent works like a reviewer: inspect the requested language/type and edition, examine cue/audio timing at representative positions, compare sample transcripts where appropriate, and investigate suspicious gaps or jumps. It can request another candidate or repair when the standard path fails. Give it actual evidence through tools; a text-only model reading an exit code has not listened to the film. Audio/video review needs an appropriately capable model/tool, and spot checks must be labelled as sampled evidence rather than whole-film proof. Keep playback rendering checks in the end-to-end tests as well.

Automatic verification and agent review have separate recorded results. If the selected review policy requires agent approval and that service is unavailable, say review is pending and offer the permitted play-now choice; do not claim the agent checked it. Retain hard checks and measured evidence regardless of the reviewer's conclusion. Discovery and acquisition remain agent-managed; this specific subtitle-processing refinement does not require changing them to fixed workflows.

The normal experience is a suitable language/track selected automatically, with clear labels for full, forced, and accessibility captions. The player offers “Subtitles are out of sync,” “Wrong subtitles,” another track, and a simple earlier/later adjustment with reset. A correction is associated with the particular media copy and track. A personal temporary adjustment must not silently rewrite the shared track for everyone.

The built-in pipeline, with agent review before promotion where required, is:

1. Inspect the exact media asset and selected audio track, including existing subtitles, language, duration, edition and file identity.
2. Prefer a suitable existing embedded or sidecar track. Search configured providers when a required track is missing or unsuitable. Provider adapters own credentials, quotas, caching and service errors. OpenSubtitles is a candidate, with the actual integration contract verified against its [API documentation](https://opensubtitles.stoplight.io/docs/opensubtitles-api/e3750fd63a100-getting-started); provider access is not assumed to be unlimited or account-free.
3. Check candidate identity, language, coverage, encoding and cue structure. A filename or similar duration is evidence to investigate, not proof of matching dialogue. Full and forced subtitles need different coverage expectations.
4. Measure timing against the actual audio or a known-good reference track. Evaluate ffsubsync for normal offset/rate correction, and alass for cases needing segmented corrections around edits. ffsubsync aligns subtitle activity with detected speech; alass supports offsets, framerate changes and splits. [ffsubsync](https://github.com/smacke/ffsubsync), [alass](https://github.com/kaegi/alass).
5. Independently validate across the beginning, middle and end, and around detected discontinuities; give the verification agent the findings and tools to inspect representative samples. Compare against the original before promoting a correction. Store observations, review coverage and calibrated confidence; successful command exit or agreement with the same alignment score is insufficient.
6. If matching subtitles cannot be found, optionally use local transcription/alignment on a capable node. WhisperX is a candidate for speech transcription with word timing; alignment models have language-specific requirements. Generated captions and any translation remain labelled as such. Cloud processing requires an explicit policy because it sends audio externally. [WhisperX](https://github.com/m-bain/whisperX).
7. Publish a versioned subtitle derivative, retain the original, refresh playback availability and notify the viewer when a requested repair is ready.

Alignment can repair many timing errors. It cannot reliably turn dialogue from the wrong episode or an incompatible edit into correct subtitles. Dubbing, long silent scenes, music, sparse forced captions and translated dialogue need separate evaluation. Speech-activity agreement alone cannot establish semantic correctness. Image-based captions require a separate OCR/conversion or replacement path; do not assume every subtitle can be processed like SRT text.

Subtitle records must reference media-file version, audio-track identity, language/type, source, original/derived track, transformation, evidence and tool/model version. Replacing a film with a different cut invalidates the old timing assurance. Perform audio extraction and alignment near the storage node where practical; send concise results to the coordinator rather than moving a whole movie for analysis.

Readiness has two layers: the media is playable, and the viewer's requested language/subtitle requirements are satisfied. If subtitles were mandatory, “ready for you” waits for those requirements; the user can explicitly choose to play without them. If optional, playback is available while subtitle preparation continues.

**Subtitle proof:** use labelled good, offset, progressive-drift, edited-cut, wrong-episode, missing, forced, SDH and multilingual examples. Measure false acceptance and timing error on independently checked speech cues. Set promotion thresholds from that evidence. Preserve already-good timing; never silently replace it with a worse result. Validate selection, seeking and sync after a quality upgrade in supported phone and desktop browsers. Broader language/format support follows measured results rather than a blanket “perfect sync” claim.

**3. Mobile is a product constraint throughout.**

The initial phone client is the responsive web application, with PWA installation. Test narrow screens, landscape, keyboard opening, safe areas, large text, touch, keyboard navigation and screen-reader labels. Primary actions cannot depend on hover or a desktop-sized drawer. Keep the title hero compact, make episode rows actionable, retain search state, and keep form errors/save feedback beside the relevant controls.

Phone users must be able to complete setup, import or request a title, review exact scope, follow a show, play/resume, switch audio/subtitles, report a problem, invite a user and recover a blocked request. Remote administration should use the same accessible flows. Browser-specific playback and notification capabilities need a stated support matrix; PWA installation does not imply native-app capabilities everywhere. Casting is parked with TV work.

Cache useful interface/catalogue state with a clear offline indication. Do not cache private media or other users' data accidentally. Offline downloads and native iOS/Android applications are separate additions if real browser limitations justify them.

**4. Accounts and sharing are foundational.**

Introduce an owner bootstrap and real authenticated identities before supported public access. Use local accounts as the baseline, with mature authentication/password/session primitives. Optional OIDC can serve owners who already run an identity provider. A viewer profile and an account are different concepts: selecting a profile must not grant another account's access or administrative permissions.

| Capability | Owner/admin | Library manager | Viewer |
|---|---|---|---|
| Server, DNS, providers, users and node pairing | Yes | No | No |
| Curate shared media / manage authorised requests | Yes | Granted libraries and policies | No |
| Watch, resume and choose personal preferences | Yes | Yes | Granted libraries |
| Request new media | Yes | Within policy | Configurable permission, budget and approval policy |
| Delete shared files / change retention | Explicit administrative or separately granted authority | Optional explicit grant | No |

Support expiring single-use invitations, password/account recovery suitable for a self-hosted owner, device/session lists and revocation. Sharing an invitation must not grant administrative access. Per-user watch progress, history, watchlist, language preferences and library permissions must survive changing devices. Do not mark everyone's episode watched because one household member finished it.

Enforce permissions on APIs, WebSockets, artwork where restricted, subtitle delivery, byte ranges, HLS manifests/segments and remote-control sessions. A private-network connection is additional transport protection, not a replacement for app accounts. Node credentials are separate from user credentials. Agents act for an explicit principal and permitted libraries; titles, subtitles and tool outputs cannot grant access to other users' data or administration.

**Admin defaults and personal overrides.**

The current application already has admin configuration and a Fetch prompt that includes some system settings and the request contract. It lacks a shared per-user inheritance model, and the Media prompt receives fewer preference fields. Replace that inconsistency with a single typed effective-settings resolver.

Admin onboarding has a Preferences step: default audio languages, original/dub preference, subtitle languages, full/forced/SDH choices, when subtitles should be shown, quality/size preferences and default monitoring behaviour. Infrastructure credentials, storage locations, bandwidth/storage/spend limits and role permissions stay administrative. Defaults and mandatory limits are visibly different.

A new family member sees a short “Your server's defaults” summary and can continue unchanged or customise. They do not repeat server setup. Personal Settings is always discoverable and shows each value's source, with Use server default / Reset actions. Store only explicit overrides, not a copied snapshot of all defaults, so genuinely inherited values follow future default changes.

The normal precedence is **server defaults → personal overrides → explicit request/playback choices**, all within admin policy. A user-specific title override, where offered, is a more narrowly scoped personal override. Policy ceilings, acquisition authority, storage permissions and budgets are validated separately and cannot be relaxed by changing a preference or prompting an agent.

Resolve preferences before creating work. Pass the relevant effective values, user identity, sources and settings version to Discovery, Fetch, Media, the subtitle reviewer and subscription-driven Librarian work. The UI and verification tools consume that same resolved contract. Prompts receive structured settings, never credentials or unrelated household preferences. Existing jobs keep their recorded intent; editing defaults affects future work, while changing an active request creates an explicit revision. Current admin restrictions are still checked before consequential actions.

For example, the admin chooses original audio and English subtitles. One user inherits that; another prefers Spanish subtitles. Both can use the same film and retain separate playback preferences. Their subtitle needs can add tracks without replacing each other's good tracks or automatically downloading another whole film. Shared acquisition conflicts require a visible policy; one person's lower quality preference must not downgrade another's shared copy. Monitoring stays linked to the relevant owner's grant rather than whichever person last opened the show.

**Preference proof:** two users see defaults at first sign-in, change different fields, and receive different resolved agent contracts and playback choices. Updating a server default changes inherited fields only; resetting an override restores inheritance. Hard limits still hold, running job intent stays stable and private settings do not leak across accounts.

**5. Self-hosting gets a guided, tested access journey.**

Build a Connections/Sharing page with guided setup forms and deterministic diagnostics. It should explain the current mode, usable address, who can connect, which checks passed and what remains. It must support existing reverse-proxy installations as well as the recommended managed configuration. The application should not require its model provider merely to start, renew certificates or serve media.

| Access mode | Intended use | Product behaviour |
|---|---|---|
| Home network | Devices on the same network | Detect/select a LAN interface, show a stable local address/QR, explain router address reservation and test device access |
| Private remote network | Owner devices and willing household members | Guide an optional Tailscale setup; verify client/network access and the actual media route |
| Public domain with HTTPS | Invitees should use an ordinary browser | Configure a domain, DNS and a reverse proxy; guide inbound connectivity; verify from outside the LAN |
| Relay through a reachable host | ISP/router prevents direct access or the owner prefers a relay | Use an explicit relay deployment with measured bandwidth, costs and a working video path; keep direct/local use available |

For the domain route, recommend Caddy as the default reverse proxy while supporting an owner-managed proxy. It automates certificate issuance and renewal. HTTP/TLS certificate challenges require the corresponding inbound reachability; DNS validation can obtain a certificate without opening inbound ports, but does not make a private server reachable. Local certificates also need trust on each client; do not direct ordinary browser users to ignore certificate errors. [Caddy HTTPS documentation](https://caddyserver.com/docs/automatic-https).

The guided flow should:

1. Identify candidate LAN addresses and public egress address, with interface selection when there are VPNs or multiple networks. Determine likely double NAT/CGNAT and IPv6 availability from evidence; distinguish a suspicion from a confirmed external connectivity result.
2. Ask for the intended hostname and DNS provider. Offer exact record values and verification, or a narrowly scoped provider integration that previews the proposed changes. Support dynamic-address updates. Do not publish an unverified IPv6 record just because an interface has IPv6.
3. When direct inbound access is feasible, explain the actual route in plain language: router public HTTPS traffic goes to the Linux reverse proxy, which forwards to Sparrow. Generate the host-specific instructions and firewall checks. Router-specific instructions require a known model/version; otherwise show a clear generic mapping without inventing menu names. DNS, certificates and routing are separate checks.
4. When direct access is blocked, explain why ordinary port forwarding will not fix it and offer private-network access, a suitable relay, or the ISP's public-address option. Avoid sending the owner around repeated forwarding instructions.
5. Verify DNS, TLS, login and actual playback externally, including seeking, subtitles and reconnection. LAN loopback tests cannot prove remote access. Check upload capacity for the chosen video workload and diagnose local access through the public hostname separately.
6. Monitor renewal and address changes, retain a usable local recovery path and provide exportable configuration/diagnostics. Back up accounts, permissions, intent and pairing recovery alongside the catalogue; media backup remains a distinct owner policy.

Tailscale Serve supplies HTTPS access inside a tailnet; it does not turn a URL into a public service. Funnel is a different public-access option and documents non-configurable bandwidth limits. Therefore it is not the default high-bitrate video relay. [Serve](https://tailscale.com/docs/features/tailscale-serve), [Funnel](https://tailscale.com/docs/features/tailscale-funnel).

Likewise, do not promise an unrestricted free Cloudflare Tunnel for streaming the collection. Its published routing guidance specifies service restrictions for video/large files on relevant plans. Cloudflare DNS can still be useful without proxying the media traffic. [Cloudflare routing guidance](https://developers.cloudflare.com/tunnel/routing/).

The setup agent is parked. Keep diagnostic/configuration operations well-defined so an assistant could use them later, but deliver normal setup, useful errors, verified DNS/HTTPS and local recovery without a model. General autonomous router/cloud administration remains outside the initial scope.

**6. Parked: TV and Emby options.**

The owner has deferred this entire workstream. The following context, options and tests are retained for when it is reopened; they are not active work or acceptance criteria. No TV model, app version or Emby-server answer is needed to proceed with the current roadmap.

The owner reports a likely Samsung smart TV with Emby installed. “Platform” means the TV's app system and supported playback capabilities; the model/year and app version matter for validation, but the owner need not buy a streaming box or choose an operating system now. Emby publishes a Samsung app, which provides a possible client to test if this work resumes. Whether it already connects to an owner-controlled Emby server is still unknown. [Emby for Samsung](https://emby.media/emby-for-samsung-smart-tv.html).

| Route | What it requires | Recommendation |
|---|---|---|
| Integrate with a real Emby server and the installed TV app | Emby reads the prepared library; Sparrow maps users/items, refreshes media/subtitles and reconciles watch progress | Possible first proof when reopened, especially if the owner already runs Emby; an optional deployment integration, not a requirement for every Sparrow installation |
| Implement Emby-compatible endpoints in Sparrow | Enough server discovery, authentication, catalogue, playback negotiation, subtitle and progress behaviour for the exact client version | Research option only; a public API does not guarantee complete Samsung-app interoperability or remove future compatibility work |
| Build a native Sparrow Samsung client | App packaging/distribution, remote UI, player integration and device maintenance | Defer unless the existing-app route cannot meet important requirements or the owner wants the full Sparrow interface on TV |

An installed Emby app cannot automatically use Sparrow's current API. The normal supported route uses Emby Server. Its documented user authentication and playback APIs make an integration plausible, but do not prove this particular TV combination works. Keep account/item mapping explicit and do not share a server-admin token with television clients. [Emby authentication](https://dev.emby.media/doc/restapi/User-Authentication.html), [playback check-ins](https://dev.emby.media/doc/restapi/Playback-Check-ins.html).

For the bridge, Sparrow continues to own discovery, requests, curation, prepared media/subtitles, preferences and its web player. Emby supplies the TV browsing/playback surface. Native Sparrow features such as subtitle-repair requests remain available on the phone; do not promise new buttons inside the closed Emby app. This is a deliberate optional external-service trade-off, unlike bundling subtitle-processing code into Sparrow. If only the app is installed, adding Emby Server is a real extra component to evaluate before adopting the route.

Prefer one writer for files: Sparrow prepares/publishes them and Emby reads them, with any competing subtitle download/rename/delete features disabled for that library. Verify subtitle refresh after a repair. Emby must have a supported, authorised view of the Windows media; merely adding a node to Sparrow does not make it readable by Emby. Test local placement near Windows storage first when practical. Do not expose a public filesystem share.

Map users and media editions explicitly. Mirror library permissions and revocation into the bridge; block access that cannot be represented safely. Import/update each user's playback state using supported APIs/events or bounded reconciliation, with session ordering and loop prevention. Do not blindly synchronise the maximum timestamp: a deliberate rewind or mark-unwatched action must survive. When a client setting cannot inherit Sparrow defaults, show that limit and offer a discoverable per-client adjustment. App availability, entitlement, format support and exact API behaviour are part of the device proof, not assumed universal.

**Future TV proof, when reopened:** expose one prepared movie and one episode with external subtitles to the real TV; verify login, browse, play, seek, audio/subtitle selection, repaired-track refresh and per-user resume across TV and Sparrow web. Restrict/revoke a test user and confirm both systems enforce it. Restart/disconnect the relevant services. The output determines whether an Emby bridge is sufficient; a custom app is no longer an up-front requirement. Casting remains an optional companion after its receiver path is tested.

Sparrow's own web playback still uses authenticated sessions, direct play when possible and bounded remux/transcode fallback. Any later native TV client can share that contract. Subtitle rendering and media transport must be tested independently of whether the alignment process succeeded.

**7. Storage-node access and video delivery must be designed together.**

The Linux coordinator owns intent, users, catalogue, agent reasoning and playback-session authorisation. Nodes own permitted files, local observations and capabilities such as downloading, probing, subtitle alignment and transcoding. Use stable media identity independent of paths and node-local receipts tied to the inspected bytes.

An outbound node command connection does not automatically carry seekable video to a browser. The first Windows integration must prove both command delivery and the chosen media route. A player needs a reachable authenticated endpoint, certificate trust, range/HLS support and sufficient sustained throughput. A cloud-hosted Linux server proxying a Windows library also consumes both the home's upload and any relay/server bandwidth; home playback should use a suitable local route when available.

The playback-session service may select an authorised direct node route or an authenticated gateway/relay route from tested capabilities. Device credentials and scoped session authorisation must protect every byte path; no public Windows filesystem share is required. If the storage machine or volume is offline, keep the title visible, explain its availability and preserve resume state. Do not silently reacquire it elsewhere.

**8. Acceptance is based on ordinary use and recovery.**

| Scenario | Required evidence |
|---|---|
| First owner session | Clean Linux setup; owner created; Windows node paired; existing movie imported; phone and desktop browsers can play it |
| Agentic request | Description refined into the correct title/version and exact episode/language scope; no unrequested broad acquisition |
| Subtitles | Suitable embedded or fetched track; local timing correction validated; manual complaint repairs the same asset without degrading a good copy |
| Household use | Admin establishes defaults; invite viewer; show/edit inherited preferences; verify effective agent settings, separate progress and enforced permissions/revocation across web and media endpoints |
| Remote use | Chosen domain/network route works externally for login, first frame, seeking and subtitles; useful diagnosis when direct inbound access is impossible |
| Interruption | Restart coordinator/node, interrupt media transport, sleep Windows and reconnect; preserve user intent, good files and watch progress |
| Unattended management | New eligible episode triggers bounded agent work; stale decisions are rejected after pause/cancel; no unchanged-state reasoning bill |
| Maintenance | Upgrade and restore a backup; preserve identities, permissions and asset mappings; do not resurrect cancelled work or expose private state |

The [roadmap](../ROADMAP.md#current-implementation-sequence) orders delivery: reliability, accounts/preferences, Linux/Windows storage, browser/mobile playback, subtitle automation, complete agentic discovery/curation, then guided external access and operational acceptance. Early bounded subtitle and Discovery SDK evaluations inform later implementation, while the first Windows node proves a real authenticated media path. Existing Fetch, Media and Librarian agents remain operational. Stages 1–4 form the first usable install/import/watch release; stages 5–7 complete the agreed product. Mobile, failure handling and permission enforcement apply throughout.

**Open deployment facts.** Linux location, Windows hardware/GPU, network placement, subtitle languages and any domain/provider remain deployment inputs. They select concrete defaults and capability tests at the relevant stage. Samsung/Emby details remain unconfirmed and parked; they do not block current work.
