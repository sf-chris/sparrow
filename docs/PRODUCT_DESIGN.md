# Product design contract

The current direction replaces the rejected dark cinematic redesign on
`design/sparrow-reimagined`. The owner's brief is a public landing page, smaller
controls and typography, a brighter palette, and a little whimsy throughout.

The owner likes this visual style. The next iteration should feel more like a
personal cinema and less like a business dashboard. Pending feedback on network
discovery, settings, sign-out, operational logs and search is collected in
[DESIGN_FOLLOW_UP.md](DESIGN_FOLLOW_UP.md); these are notes, not implemented changes.

## A happy little home for the good stuff

Sparrow uses light paper, plum ink, lilac actions, and mint and pink supporting
surfaces. Original line illustrations bring a small bird, a television, books,
a cup, and playful stars into the welcome experience. Discovery uses small
heart, star and orbit drawings. There are no landscape or sunset brand assets.
Real titles still use their catalogue artwork; missing art gets a geometric,
locally rendered fallback.

| Role           | Value     |
| -------------- | --------- |
| Page           | `#faf9f6` |
| Surface        | `#ffffff` |
| Text           | `#302d3c` |
| Secondary text | `#696372` |
| Primary action | `#7050b5` |
| Lilac          | `#eee8f8` |
| Mint           | `#e6f1e4` |
| Pink           | `#f9e8ed` |
| Border         | `#dedbe5` |

DM Sans is the single locally bundled typeface. Most page titles are 26–30px,
section headings 17–18px, body copy 12–13px, and desktop controls 36–39px high.
The landing headline is 49px at its largest, 38px on phones. Main touch controls
increase to 40–44px and text inputs to 16px on phones. Small decorative captions
never carry the only explanation of an action. Contrast and visible keyboard
focus remain required.

## Screen compositions

- **Public home:** a welcome page before any sign-in form. It introduces the
  collection, discovery, and household with a restrained headline, original
  illustration, and three pastel panels. Sign in and Open your Sparrow lead to
  `/login`. On an unconfigured server those actions lead to `/setup`.
- **Account entry:** a compact light card, with a quiet illustration on desktop
  and a simple header on phones. Setup and invitation links open their forms
  directly. Setup-code prefill, password visibility, autocomplete, validation,
  and invitations retain their existing contracts. Back navigation returns to
  the landing page; sign-out returns there too.
- **Home:** a short greeting, compact playable feature with separate artwork,
  and a lilac discovery aside on desktop. Continue-watching cards and poster
  shelves follow. Empty collections use a welcome illustration and real next
  steps appropriate to the account's permissions.
- **Library:** a compact header, result count, tidy filter bar, and poster grid.
  Search, type, availability and sort persist in the URL. Phone filters use
  two columns, with a full-width availability control.
- **Discover:** a modest centred heading and white search panel, followed by
  three pastel illustrated prompts. Prompts fill an editable draft without
  starting paid work or acquiring anything. Mode tabs support arrow/Home/End
  keys; drafts and title return links preserve context.
- **Title:** poster, plum title and synopsis on a light lilac panel. A subdued
  catalogue backdrop supplies texture without making text depend on the image.
  Real Play/Resume controls, episodes, season filtering, copies, exact request
  scope and collection care remain explicit.
- **Activity:** three compact pastel status cards, white request cards, small
  scope tags, and expandable journals. Errors cannot masquerade as empty work.
- **Settings:** a compact desktop sidebar, wrapping phone navigation, white
  grouped forms and visible inheritance/reset controls. People have initial
  tiles and access actions; storage has mint device icons and connected-folder
  cards. Server/defaults, import, matching, pairing, password and care flows
  share the same compact control and dialog system.
- **Player:** video remains on a dark viewing surface. Its surrounding page,
  track settings, subtitle repair and recovery use the light shared system.
- **Dialogs and states:** warm white dialogs, lilac focus and sticky action
  footers; restrained mint success and pink error states; lilac empty states
  with the bird. Focus containment, Escape and focus restoration remain intact.

Desktop navigation remains horizontal. Phones use four compact bottom links.
`Ctrl/Cmd+K` opens Discover. `Page`, `Section`, `Field`, `Dialog`, `Poster` and the
state components in `frontend/src/product/ui.tsx` define common behavior;
`product.css` owns visual tokens and responsive layouts.

## Product and verification boundaries

Use actual collection and account facts. Never invent availability, progress,
recommendations, household permissions or request authority for presentation.
Existing copies, preference inheritance, scope, acquisition limits, media
verification and backend permissions remain authoritative.

Inspect real browser renders at 360, 390, 768 and 1440px, including errors, empty
collections, unavailable artwork and supporting dialogs. Browser scripts and
screenshots live in [reimagined-validation](reimagined-validation/README.md).
Fictional geometric fixture covers are test data, not real library titles or
recommendations. Physical-device, Windows and live-provider boundaries remain
as recorded in [IMPLEMENTATION.md](IMPLEMENTATION.md).
