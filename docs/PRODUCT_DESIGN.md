# Product design contract

Sparrow's interface is **distilled daylight**: white, ink and one leaf green.
The artwork carries the colour; the interface gets out of its way. It replaces
the earlier pastel illustrated style. It is deliberately not the rejected dark
cinematic direction on `design/sparrow-reimagined` or the picture-house print
proposal. [IMPLEMENTATION.md](IMPLEMENTATION.md) records the implemented scope;
[redesign/README.md](redesign/README.md) has before/after captures of every page.

## Principles

1. **Say it once.** No eyebrows, badges or captions that repeat a heading.
   "Ready to watch" only appears when something is *not* ready. Preference
   sources appear only when they differ from the household default.
2. **One accent, used for state.** Green marks progress, live work, focus and
   the active tab. Actions are ink; nothing else is coloured.
3. **Artwork in frames, not behind text.** Backdrops sit in rounded frames, and
   text never depends on an image for contrast.
4. **One typeface.** Hanken Grotesk (variable, bundled locally): 700–750 for
   titles with tight tracking, 550–650 for controls, 400 for reading. Tabular
   figures for times and counts.
5. **Every control is reachable.** 44px targets, visible focus, real labels,
   segmented controls built from native radios and tabs.

| Token          | Value                     | Use                           |
| -------------- | ------------------------- | ----------------------------- |
| `--bg`         | `oklch(0.99 0.002 120)`   | Page, inputs, dialogs         |
| `--bg-2`       | `oklch(0.962 0.004 120)`  | Grouped panels                |
| `--fg`         | `oklch(0.21 0.012 150)`   | Text, primary actions         |
| `--fg-2/3`     | `oklch(0.40 / 0.47 …)`    | Secondary text (AA on panels) |
| `--accent`     | `oklch(0.60 0.15 148)`    | Progress, live, focus         |
| `--accent-text`| `oklch(0.46 0.12 148)`    | Green text (AA)               |
| `--attention`  | `oklch(0.58 0.19 32)`     | Needs attention, errors       |

Radii are 8px (posters, inputs) and 16px (frames, panels); buttons are pills.
Motion is limited to hover lifts, a dialog rise and the landing shelf filling;
reduced motion disables transitions and animation.

## Screen compositions

- **Front page:** "Ask for a film. Sparrow does the rest." beside a shelf of
  poster slots filling themselves (two still arriving). Three one-line steps
  below: Ask, Sparrow fetches, Watch. One action: set up or sign in.
- **Entry:** a single narrow column with the mark; no illustration. Setup-code
  prefill, invitations, password visibility and autocomplete are unchanged.
- **Home:** greeting, then one feature: the most recent in-progress title (or
  the newest title) in a 16:9 frame with Resume/Play and Details. Below:
  *Continue watching* (other in-progress copies), *On the way* (active requests,
  linked to Activity) and *Recently added* as a single scrolling row.
- **Library:** title and count, a sticky filter row (search, Films/Series
  segments, availability, sort — all in the URL) and a poster grid.
- **Title:** back link, a wide artwork frame, the poster overlapping it, then
  title, facts, synopsis and actions. Episodes are a numbered list with
  progress; collection care follows.
- **Discover:** one large underlined field. "I know the name" searches as you
  type; "Describe it" starts the discovery agent. Mood prompts fill the draft
  and never start paid work on their own.
- **Activity:** requests as a list with a status dot, scope, plain-language
  state and an expandable timeline of what Sparrow did.
- **Player:** the video in a dark frame on the light page; audio and subtitle
  choices, playback help and subtitle care below.
- **Settings:** profile block and a text index on the left (chips on phones,
  with Sign out beside the profile); forms in grey panels with white inputs.

Desktop navigation is a quiet top bar with a green dot under the active link;
phones use a four-item tab bar. `Ctrl/Cmd+K` opens Discover. `Page`, `Section`,
`Field`, `Dialog`, `Poster`, `Progress` and the state components live in
`frontend/src/product/ui.tsx`; `frontend/src/product/sparrow.css` owns every
visual token and layout. Tailwind is not used.

## Product and verification boundaries

Use actual collection and account facts. Never invent availability, progress,
recommendations, household permissions or request authority for presentation.
Existing copies, preference inheritance, scope, acquisition limits, media
verification and backend permissions remain authoritative.

Inspect real browser renders at 360, 390, 768 and 1440px, including errors, empty
collections, unavailable artwork and supporting dialogs. Browser scripts and
screenshots live in [reimagined-validation](reimagined-validation/README.md) and
[follow-up-validation](follow-up-validation/README.md).
Fictional geometric fixture covers are test data, not real library titles or
recommendations. Physical-device, Windows and live-provider boundaries remain
as recorded in [IMPLEMENTATION.md](IMPLEMENTATION.md).

## Artwork and font provenance

The sparrow mark (one silhouette, one eye) lives in `frontend/src/product/Brand.tsx`;
`frontend/public/icon.svg` repeats it. Missing artwork is replaced by the title
set in type on a tint derived from its name. Hanken Grotesk is bundled locally
with its SIL OFL notice under `frontend/public/assets/font-licenses/`.

The browser fixture creates original geometric posters/backdrops for fictional
titles. Production uses actual catalogue images.
