# Design follow-up: a personal cinema, simpler settings and useful logs

Consolidated issue draft from owner feedback, 10 September 2026. Target repository:
`sf-chris/sparrow`. Publication is pending GitHub authentication in this workspace.
These are iteration notes; no application changes are included.

## Keep the style; make the experience feel like a movie app

The owner likes the current bright, whimsical visual style. Keep that direction,
the smaller controls, and the landing page. The composition still feels too much
like a B2B SaaS dashboard. Future design iterations should feel like a personal
cinema: films, shows, artwork, discovery and watching take precedence over
administration, metrics and generic product panels.

- [ ] Carry this distinction into the next design iteration without discarding
  the visual style the owner likes.

## Find nearby servers and storage more easily

- [ ] Investigate local-network discovery so people can choose a named nearby
  Sparrow server or compatible storage device without typing an IP and port.
  The reference experience is Emby automatically appearing in a TV client;
  this note does not restart the deferred TV/client work.
- [ ] Prefer service discovery such as mDNS/DNS-SD or a narrowly scoped discovery
  protocol over a general sweep of every host and port. Distinguish discovering
  a Sparrow node from discovering a NAS/share: arbitrary storage devices will
  not all implement Sparrow's handshake. Prototype in the server/node layer.
- [ ] Separate discovery from trust. A discovered name/address is only a
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

- [ ] Remove the **Manage your server** section from the main preferences page.
  It currently repeats Storage & import, People, Server settings and Household
  defaults, which already exist in the settings navigation. It does not manage
  a separate set of servers. Keep those destinations available in navigation.

## Make sign-out obvious

- [ ] Put **Sign out** at the bottom of the left settings navigation, separated
  from the settings destinations. Keep an equally obvious placement in the
  phone layout. It must not require scrolling through preferences or sessions.
- [ ] After signing out, return to the public landing page.

## Replace account-page noise with focused operational logs

- [ ] Remove the verbose browser-session rows from the main settings page.
  Those rows are currently active sessions, not an application event log.
  Keep compact session/security controls available separately when someone
  needs to inspect devices or revoke access; do not turn them into the default
  operational feed.
- [ ] Add a dedicated **Logs** page focused on what Sparrow is doing: a request
  submitted, a download started/stalled/completed, imports and matching,
  storage availability, playback/subtitle preparation failures and recovery.
  Routine successful browser logins should not dominate this page.
- [ ] Use readable summaries with timestamp, title/request context, status or
  severity, and a useful next action. Put technical detail behind expansion.
  Group repetitive messages so important changes remain visible.
- [ ] Provide pagination and filtering by time, category/severity and title or
  request. Consider text search if it helps locate relevant events. Preserve
  filters while paging; include clear empty and failure states.
- [ ] Keep the everyday Activity page focused on requests and progress. Use
  Logs for operational history and diagnosis, with appropriate account scope
  and admin access to system details. Keep security/session management distinct.

Example of useful content: “Harbour Lights — download stalled; retrying another
source.” A repeated “Browser signed in” row does not help someone understand
whether their film is becoming available.

## Clarify what the header search finds

- [ ] Replace **Find something** with a label that explicitly means movies and
  shows, for example **Search movies & TV**. The current wording suggests
  searching settings or other content within the site. Keep the destination,
  visible copy and accessible label consistent on desktop and phones.

## Outcome to assess in the next iteration

The app still has the visual personality the owner likes, but feels centred on
choosing and watching films. Settings has no duplicate navigation or long
session feed, sign-out is easy to find, search is unambiguous, and operational
history is useful when something needs attention. Discovery is a documented
usability/security investigation, not an unapproved network scan.
