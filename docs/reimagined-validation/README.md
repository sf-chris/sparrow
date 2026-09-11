# Sparrow: the bright direction

The rejected dark design has been replaced on `design/sparrow-reimagined`.
The current interface has a public landing page, smaller controls and headings,
light paper and pastel surfaces, and original SVG bird and doodle illustrations.

Open [the screen gallery](index.html) or the same working development preview at
<http://192.168.1.103:3000>. The preview uses an isolated fictional collection;
its test account is `owner` / `fixture-password-123`. The existing installation
on port 8888 has not been redeployed.

## Measured validation

All results below are from the bright interface on 10 September 2026:

- `npm --prefix frontend run build`: TypeScript and production build pass. The
  existing separately loaded HLS library still has Vite's large-chunk notice.
- `check.cjs`: real browser sign-in, playback, seeking, saved progress, captions,
  alternate-audio conversion, episode requests and pause/resume pass. Twenty-two
  screenshots; no unexpected API failures or browser exceptions.
- `reimagined.cjs`: 55 layouts and 11 behavior groups pass, including the public
  landing and optional account entry, filters, sorting, return links, keyboard
  search, discovery drafts, season selection, dialog focus, storage, imports,
  title matching, permissions and missing-artwork fallbacks. Main screens and
  dialogs have no automated WCAG A/AA violations in their audited states.
- `design.cjs`: 14 landing, sign-in, discovery, empty and error layouts pass;
  bundled fonts load, images resolve, controls stay inside the viewport, and
  automated contrast/accessibility checks pass.
- `accessibility.cjs`: ten screens and the episode dialog pass WCAG A/AA audits;
  layout checks at 360, 390, 768 and 1440px find no horizontal overflow.
- `household.cjs`: invitations, personal preferences, password changes, permission
  revocation, collection care and subtitle-repair failures pass, with five
  mobile accessibility audits. The test waits for a completed care dialog to
  close before auditing its parent screen.
- `entry.cjs`: a separate fresh fixture server passes actual setup-code prefill,
  bootstrap, first preferences, sign-out to the public landing, failed-login
  input retention and sign-in back to a protected title. Twelve layouts pass
  automated audits. A second run against the LAN preview passes the existing
  account flow and four error-state layouts.
- Python compilation, JavaScript syntax and `git diff --check` pass.

The fresh setup test ran only against a temporary fixture on loopback port 8892;
that process was stopped after verification. It created no production account.
Physical phones, Safari, native Windows nodes and live providers/models are
outside these browser checks and remain the existing release gates.

## Reproduce

Build the frontend, then start the isolated fixture server:

```sh
npm --prefix frontend run build
SPARROW_BROWSER_STATE=/tmp/sparrow-browser-check .venv/bin/python tests/browser/server.py
```

With the server available on 8891, run the scripts in order from another terminal:

```sh
export SPARROW_BROWSER_STATE=/tmp/sparrow-browser-check
export SPARROW_VISUAL_OUT=docs/reimagined-validation
node tests/browser/design.cjs
node tests/browser/check.cjs
node tests/browser/entry.cjs
node tests/browser/accessibility.cjs
node tests/browser/household.cjs
node tests/browser/design.cjs
node tests/browser/reimagined.cjs
```

`tests/browser/ci.sh` runs this sequence with its own fixture lifecycle. The
fixture requires FFmpeg and the scripts use Chrome and Playwright. To verify a
Vite preview, `entry.cjs` and `reimagined.cjs` accept `SPARROW_BROWSER_URL`.

Artwork provenance is in [the interface contract](../PRODUCT_DESIGN.md#artwork-and-font-provenance), and the
interface contract is in [PRODUCT_DESIGN.md](../PRODUCT_DESIGN.md). JSON result
files and screenshots in this directory record the current direction only.
