# Changelog

All notable changes to Sparrow are documented here.

## Unreleased — the household guide and product foundation

- Found replaces Import files. Sparrow looks in every library folder by itself
  and adds a title when its name, year, TMDB and measured length agree. Each
  computer's card has a Found line: when it last looked, Look again, what it
  added, and how many files need a name, with Review to name them or set them
  aside. Files never move, and a copy still arriving is left until it settles.

- Folders change from the computer's card: click a folder's path. The chooser
  shows one folder at a time, opens beside the one in use, and its button says
  what happens ("Keep titles in Films"). A library that holds titles asks
  whether they move or both folders stay; moving keeps every title playable,
  is instant on the same disk, and drops the old folder once it's empty.
  Change folders and the two-step slot picker are gone, and a computer that
  downloads gets its incoming folder chosen for it.

- Sparrow Node updates itself. Each computer's card shows the date and commit
  it runs; with Update by itself on (the default) it takes each new version
  when the server gets one, checks it starts, swaps it in and restarts, going
  back to the version before if it can't reach the server. With it off, the
  card offers Update.
- Computers with jobs. Settings > Storage becomes Settings > Computers, where
  every computer, this server included, has one card: its disks, then a line
  per job (Storage, Playback, Downloads, Subtitles) ending in a tick, Off, or
  the problem with Fix. Change jobs turns them on and off; a computer follows
  on its next check-in. Add a computer asks what it does and only for the
  folders those jobs need, so a NAS that only keeps titles needs one folder.
- A title can be kept on one computer and downloaded on another: a computer
  that can't download (a NAS, or one paired without a download app) still
  takes new titles, and the finished request moves there. The placement line
  names who downloads and any computer it passed over.
- Sparrow Node brings its own Transmission, FFmpeg and ffprobe on Linux (x86_64
  and aarch64): no sudo, no package manager, no password. Running the setup
  command again repairs a computer and keeps its folders, jobs and download app.
- Subtitle listening runs on whichever computer has Subtitles on, on an NVIDIA
  graphics card when it works and the processor otherwise. A paired computer
  sets up its own speech engine and model the first time Subtitles is on;
  another computer's titles reach it as a small audio-only copy.
- The cost ledger tells a pick from an escalation and a stall, follows each
  reviewed pick to what came of it, and is readable by administrators at
  `GET /api/ledger`. Requests shows administrators when finding or subtitling
  a title, or a recent run, costs more than its target. A search benchmark
  replays fixed title sets offline and runs a live search-only pass that stops
  at the chosen download.
- Setup fits a laptop screen: the cover's button is always in view, the steps
  share the title's row and every step ends with Finish later and Continue.
  Each key says whether the server already has it, and a blank box keeps it.
- The download step finds Transmission or qBittorrent by itself, by brand on
  request, or runs the Transmission bundled in the Docker image for you.
- Compose mounts a media folder at `/media` (`SPARROW_MEDIA_DIR`,
  `SPARROW_MEDIA_GID`). Storage suggests folders, creates a missing last
  folder, says plainly when the server can't see a path, and keeps red for
  real problems.
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
- Retired the old curator (`SPARROW_LEGACY_CURATOR`), v2 request/goal/show
  routes, old search and suggestion routes, auto-organiser and filename rescan,
  together with their unowned cheap-model calls and the description call behind
  `/api/resolve`. Saved request history still loads.

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
