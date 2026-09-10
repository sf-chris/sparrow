# Design follow-up: a personal cinema, simpler settings and useful logs

Implemented from owner feedback, 10 September 2026, in this working tree.
The visual/settings/logging changes are implemented; network discovery is an
isolated investigation and executable prototype, as requested. Validation is in
[follow-up-validation](follow-up-validation/README.md); protocol choices and
remaining device/deployment checks are in [NEARBY_DISCOVERY.md](NEARBY_DISCOVERY.md).
The running Linux installation was updated on 11 September 2026 with its data
preserved. See [the integration record](IMPLEMENTATION.md#design-integration--11-september-2026).

## Keep the style; make the experience feel like a movie app

The owner likes the current bright, whimsical visual style. Keep that direction,
the smaller controls, and the landing page. The composition still feels too much
like a B2B SaaS dashboard. Future design iterations should feel like a personal
cinema: films, shows, artwork, discovery and watching take precedence over
administration, metrics and generic product panels.

- [x] Carry this distinction into the next design iteration without discarding
  the visual style the owner likes.

## Find nearby servers and storage more easily

- [x] Investigate local-network discovery so people can choose a named nearby
  Sparrow server or compatible storage device without typing an IP and port.
  The reference experience is Emby automatically appearing in a TV client;
  this note does not restart the deferred TV/client work.
- [x] Prefer service discovery such as mDNS/DNS-SD or a narrowly scoped discovery
  protocol over a general sweep of every host and port. Distinguish discovering
  a Sparrow node from discovering a NAS/share: arbitrary storage devices will
  not all implement Sparrow's handshake. Prototype in the server/node layer.
- [x] Separate discovery from trust. A discovered name/address is only a
  candidate: use authenticated pairing and explicit approval before granting
  access or mounting storage. Keep discovery local, avoid advertising secrets
  or file listings, and retain manual entry when discovery is unavailable.

Security assessment: this can be designed safely, but being on the same Wi-Fi
does not authenticate a device. Discovery announcements can be spoofed and reveal
service information. Emby documents UDP broadcast discovery on port 7359; that
is a discovery exchange, not a reason to trust every responder. See
[Emby's discovery documentation](https://github.com/MediaBrowser/Emby/wiki/Locating-the-Server),
[DNS-based service discovery](https://www.rfc-editor.org/rfc/rfc6763.html), and
[mDNS security considerations](https://www.rfc-editor.org/rfc/rfc6762.html#section-21).

## Remove redundant administration shortcuts

- [x] Remove the **Manage your server** section from the main preferences page.
  It currently repeats Storage & import, People, Server settings and Household
  defaults, which already exist in the settings navigation. It does not manage
  a separate set of servers. Keep those destinations available in navigation.

## Make sign-out obvious

- [x] Put **Sign out** at the bottom of the left settings navigation, separated
  from the settings destinations. Keep an equally obvious placement in the
  phone layout. It must not require scrolling through preferences or sessions.
- [x] After signing out, return to the public landing page.

## Replace account-page noise with focused operational logs

- [x] Remove the verbose browser-session rows from the main settings page.
  Those rows are currently active sessions, not an application event log.
  Keep compact session/security controls available separately when someone
  needs to inspect devices or revoke access; do not turn them into the default
  operational feed.
- [x] Add a dedicated **Logs** page focused on what Sparrow is doing: a request
  submitted, a download started/stalled/completed, imports and matching,
  storage availability, playback/subtitle preparation failures and recovery.
  Routine successful browser logins should not dominate this page.
- [x] Use readable summaries with timestamp, title/request context, status or
  severity, and a useful next action. Put technical detail behind expansion.
  Group repetitive messages so important changes remain visible.
- [x] Provide pagination and filtering by time, category/severity and title or
  request. Consider text search if it helps locate relevant events. Preserve
  filters while paging; include clear empty and failure states.
- [x] Keep the everyday Activity page focused on requests and progress. Use
  Logs for operational history and diagnosis, with appropriate account scope
  and admin access to system details. Keep security/session management distinct.

Example of useful content: “Harbour Lights — download stalled; retrying another
source.” A repeated “Browser signed in” row does not help someone understand
whether their film is becoming available.

## Clarify what the header search finds

- [x] The first iteration clarified the movie/TV search label. The owner's
  subsequent review chose **Discover** as the single navigation entry, removing
  the duplicate search beside the account on desktop and phones. The search
  field inside Discover explicitly labels movies and TV shows.

## Outcome to assess in the next iteration

The app still has the visual personality the owner likes, but feels centred on
choosing and watching films. Settings has no duplicate navigation or long
session feed, sign-out is easy to find, search is unambiguous, and operational
history is useful when something needs attention. Discovery is a documented
usability/security investigation, not an unapproved network scan.


## Implementation boundaries

Logs records new operational changes in a dedicated SQLite history. Existing
journals remain with requests; historical events are not invented or backfilled.
It retains up to 50,000 events / 90 days, groups equivalent events in five-minute
windows, and applies current account/storage permissions before filtering or
counting. Pagination uses a fixed event snapshot; Refresh returns to the latest
first page. Old records can expire at the retention boundary.

Personal request and playback/subtitle events stay personal. Shared import and
storage changes are visible only within the person's storage scope. Administrators
can see household/system events and expand event identifiers; raw exception text,
paths, credentials and release names are not copied into this feed. Security and
session controls live at Account & security. Successful logins create no log rows.

Nearby discovery opens no sockets on normal startup and cannot pair or mount.
The prototype was tested with controlled records and actual loopback mDNS only;
physical LAN/NAS/Windows behavior and an authenticated UI chooser remain future
release work. Stage 7 and TV/Emby/casting remain excluded.


## Owner review refinements — 10 September 2026

- [x] Replace the oversized playable Home feature with compact Continue watching
  cards in a horizontal shelf, retaining real progress, resume and offline states.
- [x] Keep Discover as the single navigation entry for search.
- [x] Explain playback conversion under Playback help and label the action
  **Try another playback format**. The original file remains unchanged.
- [x] Replace the nested password panel with a compact setting row and a focused
  password-change dialog.
- [x] Remove decorative page-header eyebrow lines and redundant Home, Library
  and Discover introductions; reduce shared heading and top spacing.
- [x] Reduce public landing space below navigation and align the hero copy to
  the top of its illustration.
