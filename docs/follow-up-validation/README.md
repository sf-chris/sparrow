# Personal cinema follow-up validation

10 September 2026. The fictional library and generated media are provided by
`tests/browser/server.py`; no acquisition or paid model calls were made.

`tests/browser/follow-up.cjs` checks Home, Library, Discover, preferences,
account/security, its password dialog and Logs, including empty/error history.
The ten states were audited at 360, 390, 768 and 1440 px: **40 layouts,
zero automated WCAG A/AA violations, zero page overflow,
and zero page errors**. [results.json](results.json) records the results.

The journey also checks Discover as the single search entry on a phone, compact
watching cards, aligned settings headings, password-dialog focus restoration,
no duplicate settings links, sign-out visible before scrolling preferences, successful sign-out to the
public landing page, browser-session revocation, log pagination with a fixed
snapshot, filters preserved through paging/reload, empty results, API failure
and retry recovery. The separate viewing journey verifies real playback, audio,
seeking, exact episode requests and pause. Household and public-entry journeys
also pass. The original reimagined suite (55 layouts, 11 behaviors) and backend
checks were completed before this compact-layout refinement; the viewing,
household, public-entry and follow-up journeys were rerun for it.
[checks.json](checks.json) records the repository and protocol checks.

| Screen | Desktop | Phone |
| --- | --- | --- |
| Public landing | [1440px](landing-1440.png) | [390px](landing-390.png) |
| Home | [1440px](home-1440.png) | [390px](home-390.png) |
| Library | [1440px](library-1440.png) | [390px](library-390.png) |
| Discover | [1440px](discover-1440.png) | [390px](discover-390.png) |
| Preferences | [1440px](preferences-1440.png) | [390px](preferences-390.png) |
| Account & security | [1440px](security-1440.png) | [390px](security-390.png) |
| Password dialog | [1440px](password-dialog-1440.png) | [390px](password-dialog-390.png) |
| Logs | [1440px](logs-1440.png) | [390px](logs-390.png) |
| Expanded time filters | [1440px](logs-time-range-1440.png) | [390px](logs-time-range-390.png) |
| Empty log results | [1440px](logs-empty-1440.png) | [390px](logs-empty-390.png) |
| Log service failure | [1440px](logs-error-1440.png) | [390px](logs-error-390.png) |

Screenshots were visually inspected as well as audited. Full-page screenshots
include the fixed phone navigation at its viewport position. Physical phones,
Safari, Windows and actual LAN discovery remain separate checks. The discovery
prototype's evidence and limits are in [NEARBY_DISCOVERY.md](../NEARBY_DISCOVERY.md).

The final phone review moved the optional time range into an expandable row and
uses full-width date/time controls on phones, so entered dates and times remain
readable. The journey checks those values survive pagination too.

The compact-layout review also checked the actual preview on port 3000. The
public landing passed four accessibility/overflow checks, with its tag starting
22–28px below navigation. The security header aligns with the sidebar at 2048px.
A focused browser check exercised the conversion action (playable HLS, retained
position, unchanged source hash), password mismatch/cancel/focus restoration,
and six progress cards in one internally scrolling row, including an offline
copy without a resume button. [preview-results.json](preview-results.json)
records these checks. The multi-card screenshots use explicitly mocked progress:
[desktop](continue-row-1440.png), [phone](continue-row-390.png).
