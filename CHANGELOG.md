# Changelog

All notable changes to Sparrow are documented here.

## Unreleased — the household guide and product foundation

- Every screen redesigned as the household's own TV guide: newsprint, print
  black, channel teal and biro blue in one bundled typeface (Archivo). The
  Guide replaces Home and Library, Find has one box for a title or a mood, and
  arrow keys move between titles for keyboards and TV remotes. Tailwind and
  DM Sans are removed; a before/after gallery covers all 25 screens.
- Personal themes: the Guide stays the official theme, and each person can
  choose Cinema, Clear or Saturday in Preferences. Every
  theme passes the same accessibility checks; fonts load only when used.
- All interface copy rewritten in a listings voice, including the request,
  log, Find, Following and subtitle status lines the server sends. The voice
  rules are in the product design contract.
- Dedicated operational Logs with scoped history, grouped repeats, filters,
  stable pagination and useful failure/recovery context.
- Focused account/security page, current-browser identification, session
  revocation, password dialog and visible sign-out in settings.
- Clearer optional playback conversion and one search destination in navigation.
- Refreshed open-source README, desktop/phone gallery and reproducible screenshot
  capture using fictional media. Isolated browser runs preserve existing previews.
- Browser checks accept both native and MediaSource HLS; release checks require
  ripgrep explicitly, and CI retains results and server logs for diagnosis.
- Opt-in nearby-service discovery prototype with bounded mDNS queries; no
  automatic scanning, device pairing or mounted shares.

- Owner accounts, invitations, storage permissions, inherited preferences and
  personal sessions/progress.
- Responsive collection, exact requests, activity recovery, native browser
  player, audio/captions, format conversion and PWA assets.
- Portable storage commands, safe import/correction, acquisition/publication,
  Linux container and Windows node installer/service build workflow.
- Built-in subtitle discovery/alignment/local speech evidence, agent review,
  repair, required-track readiness and per-person offsets.
- Tool-using Discovery and scoped collection subscriptions with no idle model
  polling, preserved upgrade copies and bounded reasoning/resource use.
- Migration snapshots and regression, actual-media, browser/accessibility and
  container acceptance checks. Platform/provider limitations are recorded in
  `docs/IMPLEMENTATION.md`; Windows installation is not yet physically verified.

## [0.1.0-alpha.1] - 2026-07-16

### Added

- Persistent Fetch, Media, and Librarian agent sessions.
- TMDB-backed resolution and job contracts.
- Agent journal, activity, job, session, and spend surfaces.
- Torrent-client plumbing and TPB search tools.
- ffprobe-backed media evidence and filesystem guardrails.
- Production doctor, macOS LaunchAgent installation, backup script, and alpha
  confidence suite.
- Open-source project documentation and CI.

### Security

- Secrets are write-only through the configuration API.
- Production defaults to loopback, controlled CORS, and no auto-reload.
- Non-loopback binding requires explicit trusted-LAN opt-in.
- Retired CWM execution routes return 404 in normal builds.
- Upgrade swaps verify real media durations and recover safely if placement
  fails; normal move cannot remove an existing library file.

### Fixed

- Movie and TV TMDB identifiers are treated as separate namespaces throughout
  resolution, job ownership, inventory, and title-page navigation.
- Verified inventory quality comes from ffprobe and cannot be overwritten by a
  legacy filename guess during artwork enrichment.
- Agent-managed downloads no longer race the legacy landing poller, and a
  verified Media session promotes its transfer to the organized state.
- A clean production install can start onboarding before credentials and media
  folders have been configured.
- Torrent file inspection accepts both observed APIBay list and keyed-object
  response shapes instead of failing the agent turn.
- Existing `.env` files are tightened to owner-only permissions on install and
  startup, not only when first created.

### Known limitations

- No authentication, built-in player, push notifications, durable subscription
  contract, signature subtitle workflow, remote relay, or music support.
