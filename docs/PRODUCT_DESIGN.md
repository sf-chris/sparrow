# Product design contract

Sparrow looks and behaves like the household's own weekly TV guide: what you
have is listed, what you want is circled, and what is coming is billed. It
replaces the earlier pastel "happy little home" design (and the rejected dark
cinematic direction). The owner chose this direction on 28 September 2026 from
an [impeccable](https://github.com/pbakaus/impeccable) direction round, with
structural UX change approved. [IMPLEMENTATION.md](IMPLEMENTATION.md) records
the implemented scope.

The use scene decides the details: evenings, on a phone on the couch and on a
TV-connected computer across the room. Type is large and condensed, focus is
visible at distance, and every list works with arrow keys.

## Print, pen and highlighter

| Role | Value | Use |
| --- | --- | --- |
| Newsprint | `#eae9e5` | Page |
| Lifted paper | `#f6f5f2` | Inputs, coupons, sheet footers |
| Print black | `#181410` | Text, rules, reversed bars, primary buttons |
| Secondary ink | `#433f3a` | Supporting text |
| Channel teal | `#156068` | Sparrow's band: masthead, day band, cover and sign-in |
| On teal | `#f3f2ec` | Type and rules on the band |
| Marigold | `#f1c45e` | Small doses only: the current choice on black, play buttons, progress on artwork, focus on teal |
| Biro blue | `#0a4ac5` | The household's hand: links, focus, request circles, ticks |
| Red | `#b70012` | Problems only |
| Screen | `#0b0a08` | The player |

Large fields are the mid-dark teal, never a bright colour, so the guide stays
comfortable in a dark room (the first bright-yellow version was too glaring).
Marigold never covers an area larger than a button and never carries text
meaning on newsprint. Selected tabs and segments are black with marigold text;
the selected masthead link is a newsprint pill. Focus rings are biro blue on
paper and marigold on teal.
All text pairs meet WCAG AA; the tokens live in `frontend/src/product/product.css`.

**Type.** Archivo is the single, locally bundled variable family (weight
100–900, width 62–125%), a Franklin-Gothic-style grotesk. Display uses width
62–68% at weight 900 in capitals; listing titles use 76–82% at weight 750–850;
bar labels use 112–115% capitals; body text is 100%. Numbers are tabular.
The root size grows from 16px on phones and laptops to about 18px at 1920px
and 22px at 2560px, so a TV across the room gets a proportionally larger guide.

**Grammar.** Reversed black bars name listing sections. Dotted leaders join a
title to its facts. A left column carries time: real event times in Logs, and
honest age since the last update (“now”, “12m”, “2h”) for requests, never an
invented broadcast time. A–Z index letters hang in the gutter. Flags are small boxed capitals, used only for exceptions (offline,
needs subtitles, checking, paused, stuck); a playable title carries no label.
Every input, select and segmented choice is a square coupon field; only
buttons and tabs are pills. The request form is a dashed coupon with a
scissors mark. The biro circle draws once around wanted titles (Coming up and
open requests) and nowhere else; a biro tick marks watched, saved and already
in your collection. The loading state is the sparrow doodle drawing itself.

## Voice

Sparrow reads like a listings page, not an assistant: short, factual, British
English. Apple TV, BBC iPlayer and printed TV guides are the reference.

- Sections are nouns (Tonight, Coming up, Collection, Following). Buttons say
  what happens (Play, Resume S1 E3, Request, Follow this series, Import files).
- No lede under a page title. Keep one only when it changes what someone will
  do, as on Defaults and Preferences.
- Hints state consequences people can't see: cost, sign-outs, expiry, what is
  left unchanged. Never reassurance, and never an explanation of the product.
- State, not narration: “Starting…”, “Paused”, “Stuck”. Sparrow never speaks
  in the first person; only Ask Sparrow and Sparrow's picks name it.
- Errors say what happened and what to do, in two short sentences at most:
  “This copy won't play. Try another format under Playback help.” No “please”
  and no apologies.
- No rhetorical rhythm: no lists of three for effect, no paired fragments, no
  semicolons, no em dashes.
- The household's words: film, series, season, episode, collection, storage,
  download app. Not media, content, assets, transfers, staging or reasoning.
- Digits for numbers: “7 days”, “24 min left”, “S1 E3”, “Airs 5 Oct”.

Request flags are Stuck (needs a retry), Stopped (cancelled or given up),
Failed and Arrived. Title flags are Offline, Needs subtitles and Checking.

## Structure

Navigation is **Guide · Find · Requests**, plus the account initial for
settings. Phones use a four-item tab bar (Guide, Find, Requests, You).
`Ctrl/Cmd+K` opens Find; `/` focuses the page's search field; arrow keys move
between titles, rows and covers anywhere they are listed.

- **Cover (public):** a teal magazine cover. Giant logotype, one line
  (“Say what you want to watch.”), one action (Sign in, or Set up Sparrow on a
  new server) and a labelled example listing showing a request, an arrival and
  a resume. Sign-in, setup and invitations are coupons on the same cover.
- **Guide (`/`):** the day's page and the whole library in one place. The day
  name, set huge in the teal band that continues from the masthead, is the
  heading and the only display-size title in the app besides a title's own name. **Tonight** lists up to four resumable items (twelve on
  demand) with a still, time left and a direct play button. **Coming up** lists
  open requests with their last update time, circled title and status.
  **Collection** is an A–Z listing with cover thumbnails, or a covers view.
  Search, type, sort, availability and view persist in the URL. `/library`
  redirects here with its filters.
- **Find (`/discover`):** one box for a title or a mood. Title matches appear
  as you type and are marked when already in your collection. **Ask Sparrow** is the
  only way to start paid discovery; its answer appears as Sparrow's picks. Ideas
  fill the box without starting anything. Drafts and sessions persist in the URL.
- **Title:** a feature spread. Big condensed title, facts line, synopsis, one
  primary action (Play or Resume with the episode) and Request. Open requests
  show as a circled “Coming up”. Following is a single line with Edit and
  Check now. Episodes and copies are listed below with progress and ticks.
- **Requests (`/activity`):** a timed listing of requests with scope, the
  agent's latest note, recovery actions and expandable notes, then Following.
  `?request=` narrows to one request, including completed work.
- **Player:** the black TV screen with Sparrow's own controls: a large marigold
  play button, back 10s and forward 30s, a marigold scrubber, tabular times, mute
  and full screen (Space/K, ←/→, M, F). Audio and subtitle choices sit under the
  video; Playback help and Subtitle help are disclosures. Subtitle help offers
  Get subtitles, optional Check subtitle sync, and Fix subtitle timing. Each
  title/episode also opens those controls. Available tracks are distinguished
  from sync-checked tracks; paid checking is optional in shared preferences.
- **Settings:** a left index grouped You (Preferences, Account, Logs),
  Household (People, Defaults) and Server (Storage, Connections, Setup), with
  Sign out beneath. On phones the index becomes a scrolling strip with Sign
  out beside it. People is a cast list; Storage lists devices; Logs is a timed
  listing (time, title, summary, action) whose filters persist through
  pagination and reload. Preferences label only personal or server-limited values.
- **Setup:** numbered steps with the current step circled; two start choices;
  a check list with ticks for what is ready.
- **Dialogs:** paper sheets headed by a black bar; bottom sheets on phones.
  Focus containment, Escape and focus restoration are required.

## Themes

The Guide is Sparrow's official theme. Each person can choose another under
You → Preferences → Theme; it applies at once, is stored with their account
(outside the preference contract agents use) and is painted before the app
loads on the device's next visit. Themes change colour, type, shape and
density. They never change structure, words or behaviour.

| Theme | For | Character |
| --- | --- | --- |
| Guide | Everyone (default) | Newsprint listings, teal band, condensed capitals |
| Cinema | Film nights, the TV across the room | Dark streaming shelves, Manrope, rounded art, no rules |
| Clear | Older eyes, low vision | Large print: Atkinson Hyperlegible Next, warm paper, no dark fields, one blue, amber focus halo |
| Saturday | Kids | Fredoka, round blue shelves, sunshine buttons that press down, tilted posters |

Cinema is the only dark theme. Clear and Saturday each use one colour and one
highlight; their character comes from type size and shape, not more colours.

Every theme passes the same axe and overflow checks as the Guide
(`tests/browser/themes.cjs`). Red stays reserved for problems and the player
stays dark in all of them. A theme restates the palette and faces in
`frontend/src/product/themes.css`.

## Product and verification boundaries

Use actual collection and account facts. Never invent availability, progress,
recommendations, household permissions or request authority for presentation.
Existing copies, preference inheritance, scope, acquisition limits, media
verification and backend permissions remain authoritative. Example content on
the public cover is labelled as an example.

Inspect real browser renders at 360, 390, 768 and 1440px, and at 1920px for
TV use, including errors, empty collections, unavailable artwork and dialogs.
`tests/browser/ci.sh` runs the journeys with axe checks; `tests/browser/gallery.cjs`
photographs every screen from a fresh fixture for design review. Fictional
geometric fixture covers are test data, not real titles or recommendations.

## Artwork and font provenance

The sparrow doodle, pen circle and tick are original SVG in
`frontend/src/product/Brand.tsx`; `frontend/public/icon.svg` repeats the
doodle on teal. Archivo (Omnibus-Type) is bundled locally under the SIL OFL
with its notice in `frontend/public/assets/font-licenses/`. The themes add
Manrope, Atkinson Hyperlegible Next and Fredoka, each a Latin subset bundled
locally under the SIL OFL with its notice in the same folder; a font downloads
only when someone uses its theme. DM Sans, the pastel illustrations and
Tailwind have been removed.
