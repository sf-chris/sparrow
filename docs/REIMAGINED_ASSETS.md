# Sparrow illustration and typography

The current bright direction uses original SVG illustrations written directly
in `frontend/src/product/Brand.tsx`: the sparrow mark, television-and-bird welcome
illustration, and the star, heart and orbit discovery drawings. The favicon in
`frontend/public/icon.svg` repeats the mark. SVG preserves crisp lines at every
screen size without external image requests.

The rejected cinematic WebP illustrations, Archivo font, and their validation
screenshots have been removed from the project. None is used by this direction.

DM Sans remains bundled at `frontend/src/product/assets/dm-sans.woff2`, with its
existing SIL OFL notice in `frontend/public/assets/font-licenses`. No new fonts,
image-generation assets or runtime dependencies were added.

`tests/browser/server.py` creates six original geometric posters and backdrops
for clearly fictional browser fixtures. Production uses actual catalogue images
and the shared SVG/CSS fallback when those are missing.

The current [README gallery](screenshots/README.md) is captured directly from
that fixture with `tests/browser/screenshots.cjs`. Desktop and phone images
share the production frontend and its bundled assets; none is a mockup.
