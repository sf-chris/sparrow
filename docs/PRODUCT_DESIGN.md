# Product design contract

Sparrow is **a private picture house**: a cinema for one household that runs
itself. The interface borrows from repertory-cinema print — programmes,
tickets, marquees and the projectionist's log — rather than from software
dashboards. The before/after record of this redesign is in
[redesign/README.md](redesign/README.md).

## Principles

1. **Watching first.** Play and Resume are always the most obvious control on
   the page. Everything else is quieter.
2. **Only Play is round and red.** Square corners, hairline rules and no
   shadows everywhere else. The vermilion signal marks what is live: play,
   progress, the current page and the active step. Primary actions that are not
   playback use solid ink.
3. **Print, not panels.** Paper surfaces, ruled sections and set type do the
   structuring. Sheets (raised paper) are used sparingly for forms.
4. **Say it plainly, with a little theatre.** Headings may have character
   ("Take your seat.", "Nothing on order."); labels, buttons and errors say
   exactly what happens.
5. **Verify reality.** Use actual collection, account and progress facts. Never
   invent availability, recommendations or permissions for presentation.

## Palette

Tokens live in `frontend/src/product/styles/base.css`. Every state is paired
with words, never colour alone.

| Role | Light ("house lights") | Dark ("after hours") |
| --- | --- | --- |
| Paper | `#eee8db` | `#14120e` |
| Sheet (raised) | `#f7f3ea` | `#1f1c17` |
| Ink | `#17140f` | `#eee7d9` |
| Secondary ink | `#4b453b` | `#c0b7a6` |
| Signal (fills, large marks) | `#cc3a16` | `#f0532d` |
| Signal ink (small text) | `#b0300f` | `#ff7b58` |
| Inverse accent (red text on ink) | `#ff7b58` | `#b0300f` |
| Ready / attention / problem | `#1d6a44` / `#8a5300` / `#a8261a` | `#6fcf9a` / `#f0b75a` / `#ff8a7a` |

Dark mode follows the system setting. The player page (`.sp-dark`) is always
dark. Light text on the signal colour is 4.7:1; small red text on paper uses
signal ink. Axe colour-contrast audits run at 360, 390, 768 and 1440px.

## Type

Three OFL families are bundled locally — no font requests leave the server.

- **Big Shoulders Display** (800–900): mastheads, titles, section heads,
  numerals. Tall and condensed, like marquee lettering.
- **Schibsted Grotesk** (400–700, italic for Sparrow's discovery answers):
  body, controls and buttons. Body is 15px; inputs are 15–16px.
- **IBM Plex Mono** (400–500, uppercase, 0.08em tracking): kickers, field
  labels, metadata, timestamps and the agents' journal.

## Signature elements

- **Masthead**: a mono kicker (date, count or context) over a display title.
- **Tickets**: Continue watching cards have a perforated stub, punched notches,
  a round play disc and a progress line. They scroll horizontally.
- **Prints**: library posters carry a catalogue number (`Nº 04`), kind and
  year. Titles without artwork get a typographic print generated from the
  title, never invented imagery.
- **Programme listing**: episodes are numbered rows with large display numerals.
- **Journal**: each request's log is set in mono on ruled paper with times in
  the margin.
- **Admit-one ticket**: sign-in, first-run setup and invitations share a split
  layout whose ink panel holds a tilted red ticket.

## Screen compositions

- **Front of house (public)**: poster-scale headline, the projection
  illustration, a marquee band, the three-act programme and a closing call.
  Sign in / Set up Sparrow go to `/login` or `/setup`.
- **Entry**: setup code, invitation and sign-in forms keep their contracts
  (prefill, show/hide password, autocomplete, validation). Phones drop the
  ticket panel for a ruled header.
- **Tonight (home)**: time-aware greeting, tickets for up to twelve in-progress
  copies, up to twelve prints, and a box-office callout for people who can
  request. An empty house shows real next steps for the account's role.
- **Library**: count, search, All/Films/Series segments, availability and sort;
  every filter persists in the URL.
- **Title**: framed backdrop band, overlapping poster, display title, round
  Play/Resume, request and follow controls, episode listing with season filter.
- **Find**: one large typed field with two tabs ("I know the title", "Help me
  choose"), three starting-point slips that only fill a draft, and Sparrow's
  answer set as an italic note. Keyboard tab navigation is preserved.
- **Requests**: open/all tabs, request rows with status, scope, controls and an
  expandable journal; followed titles below.
- **Screening room**: dark page, the video, an audio/subtitle deck, playback
  help and subtitle care.
- **Settings**: a numbered, sticky index on desktop; a horizontal index with
  visible Sign out on phones. Preferences are numbered fieldsets with visible
  inheritance and reset. People is a cast list; storage uses device cards with
  capacity gauges; server setup has a numbered stepper and two choice cards.
- **Dialogs**: a red top rule, display title and a sticky footer; bottom sheet
  on phones. Focus trapping, Escape and focus restoration are unchanged.

Navigation: desktop bar with Tonight · Library · Find · Requests; phones use a
four-item dock. `Ctrl/Cmd+K` opens Find.

## Implementation map

`frontend/src/product/ui.tsx` holds the shared behaviour (`Page`, `Section`,
`Field`, `Dialog`, `Poster`, `PrintCard`, `Status`, states). Styles are split by
layer under `frontend/src/product/styles/`: `base.css` (tokens, fonts, type),
`components.css`, `shell.css`, `front.css`, `screens.css` and `settings.css`.
Tailwind is no longer used.

## Verification

`tests/browser/ci.sh` runs the functional journeys, axe audits and overflow
checks at 360, 390, 768 and 1440px, then captures the
[screenshot gallery](screenshots/README.md) from fictional fixtures.

## Artwork and font provenance

The sparrow mark, wordmark, projection illustration and generated poster prints
are original SVG/CSS in `frontend/src/product/Brand.tsx` and `ui.tsx`;
`frontend/public/icon.svg` repeats the mark. Big Shoulders Display, Schibsted
Grotesk and IBM Plex Mono are bundled under `frontend/src/product/assets/fonts/`
with their SIL Open Font License notices in
`frontend/public/assets/font-licenses/`. DM Sans and the previous bird,
television and doodle illustrations have been removed.

The browser fixture's geometric posters and backdrops belong to fictional test
titles; production uses actual catalogue images.
