# Product design contract

Sparrow looks like printed cinema material: tickets, posters and programme
listings. Before and after captures of every page are in
[redesign/README.md](redesign/README.md).

## Principles

1. **Watching first.** Play and Resume are the most prominent controls on any
   page.
2. **Only Play is round and red.** Everything else has square corners, hairline
   rules and no shadows. Red marks what is live: playback, progress, the current
   page and the current setup step. Other primary buttons are solid ink.
3. **Say it once.** No labels above headings and no taglines under them. Add a
   line of explanation only when the control can't explain itself. Show where a
   preference comes from only when it differs from the household default.
4. **Plain words.** Buttons and errors describe exactly what happens.
5. **Verify reality.** Use actual collection, account and progress data. Never
   invent availability, recommendations or permissions.

## Palette

Tokens live in `frontend/src/product/styles/base.css`. Every state has a text
label as well as a colour.

| Role | Light | Dark |
| --- | --- | --- |
| Paper | `#eee8db` | `#14120e` |
| Sheet (raised) | `#f7f3ea` | `#1f1c17` |
| Ink | `#17140f` | `#eee7d9` |
| Secondary ink | `#4b453b` | `#c0b7a6` |
| Signal (fills, large marks) | `#cc3a16` | `#f0532d` |
| Signal ink (small text) | `#b0300f` | `#ff7b58` |
| Ready / attention / problem | `#1d6a44` / `#8a5300` / `#a8261a` | `#6fcf9a` / `#f0b75a` / `#ff8a7a` |

Dark mode follows the system setting. The player (`.sp-dark`) is always dark.
Light text on the signal colour is 4.7:1, and small red text on paper uses
signal ink. Axe colour-contrast audits run at 360, 390, 768 and 1440px.

## Type

Three OFL families are bundled, so no font requests leave the server.

- **Big Shoulders Display** (800–900): page and section titles, numerals.
- **Schibsted Grotesk** (400–700, italic for Sparrow's discovery answers):
  body, controls and buttons. Body is 15px; inputs are 15–16px.
- **IBM Plex Mono** (400–500, uppercase, 0.08em tracking): field labels,
  metadata, timestamps and the request journal.

## Components

- **Page header**: a display title, with at most one short line under it.
- **Continue watching**: ticket cards with a perforated stub, a round play
  button and a progress line. The row scrolls horizontally.
- **Poster cards**: kind and year above the title. Titles without artwork get a
  typographic poster generated from the title.
- **Episode list**: numbered rows with large numerals.
- **Journal**: each request's log in mono, with times in the margin.
- **Entry layout**: sign-in, first-run setup and invitations share a split
  layout with an "Admit one" ticket on the dark panel.

## Screens

- **Landing (public)**: headline, projector illustration and three steps under
  "How it works". The header button goes to `/login`, or `/setup` on a new
  server.
- **Entry**: setup code, invitation and sign-in forms keep their contracts
  (prefill, show/hide password, autocomplete, validation). Phones drop the
  ticket panel for a ruled header.
- **Home**: a greeting, Continue watching for up to twelve in-progress copies,
  then up to twelve posters. An empty library shows next steps for the
  account's role.
- **Library**: count, search, All/Films/Series, availability and sort. Every
  filter persists in the URL.
- **Title**: backdrop band, overlapping poster, title, Play/Resume, request and
  follow controls, and the episode list with a season filter.
- **Find**: one large field with two tabs ("I know the title", "Help me
  choose") and three example requests that only fill in the field. Sparrow's
  answer is set in italics. Keyboard tab navigation is preserved.
- **Requests**: Open and All tabs; each request shows status, scope, controls
  and an expandable journal. Followed titles are listed below.
- **Player**: dark page with the video, audio and subtitle selectors, playback
  help and subtitle repair.
- **Settings**: a sticky index on desktop and a horizontal index with a visible
  Sign out on phones. Storage uses cards with capacity gauges. Server setup has
  a numbered stepper and two choice cards.
- **Dialogs**: a red top rule, display title and sticky footer, shown as a
  bottom sheet on phones. Focus trapping, Escape and focus restoration are
  unchanged.

Navigation is Home · Library · Find · Requests in the desktop bar and a
four-item dock on phones. `Ctrl/Cmd+K` opens Find.

## Implementation map

`frontend/src/product/ui.tsx` holds the shared pieces (`Page`, `Section`,
`Field`, `Dialog`, `Poster`, `PrintCard`, `Status` and the loading, empty and
error states). Styles are split by layer under `frontend/src/product/styles/`:
`base.css` (tokens, fonts, type), `components.css`, `shell.css`, `front.css`,
`screens.css` and `settings.css`. Tailwind is no longer used.

## Verification

`tests/browser/ci.sh` runs the functional journeys, axe audits and overflow
checks at 360, 390, 768 and 1440px, then captures the
[screenshot gallery](screenshots/README.md) from fictional fixtures.

## Artwork and font provenance

The sparrow mark, wordmark, projector illustration and generated posters are
original SVG/CSS in `frontend/src/product/Brand.tsx` and `ui.tsx`;
`frontend/public/icon.svg` repeats the mark. Big Shoulders Display, Schibsted
Grotesk and IBM Plex Mono are bundled under `frontend/src/product/assets/fonts/`
with their SIL Open Font License notices in
`frontend/public/assets/font-licenses/`. DM Sans and the previous bird,
television and doodle illustrations have been removed.

The browser fixture's geometric posters and backdrops belong to fictional test
titles. Production uses real catalogue images.
