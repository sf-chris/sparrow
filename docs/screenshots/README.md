# A look around Sparrow

The household-guide interface, captured from the production frontend on
28 September 2026. These are real application screens using the isolated
browser fixture: six fictional titles, original geometric covers and generated
video. They contain no personal library, credentials or paid-provider results.

## The cover

![Public cover page](landing-desktop.png)

## Tonight, coming up, and everything you have

![The Guide with Tonight, Coming up and the A–Z collection](home-desktop.png)

## A title, or a mood

![Find with one search box and ideas](discover-desktop.png)

## Exactly the episodes you want

![A series page with its episode listing](title-desktop.png)

| Screen | Desktop · 1440 × 1000 | Phone · 390 × 844 |
| --- | --- | --- |
| Cover | [View](landing-desktop.png) | [View](landing-mobile.png) |
| Guide | [View](home-desktop.png) | [View](home-mobile.png) |
| Collection as covers | [View](library-desktop.png) | [View](library-mobile.png) |
| Find | [View](discover-desktop.png) | [View](discover-mobile.png) |
| Series | [View](title-desktop.png) | [View](title-mobile.png) |
| Preferences | [View](preferences-desktop.png) | [View](preferences-mobile.png) |

Phone images capture one viewport so the bottom navigation stays in its natural
position. The app scrolls normally to the remaining content. Every other
screen and dialog, before and after the redesign, is in the
[redesign gallery](../redesign/README.md).

## Reproduce the captures

After installing the development prerequisites and building the frontend:

```sh
SPARROW_SCREENSHOT_OUT=docs/screenshots tests/browser/ci.sh
```

Use `SPARROW_BROWSER_PORT=8893` if the default fixture port, 8891, is already in
use. The runner creates isolated state, exercises the product journeys, then
runs `tests/browser/screenshots.cjs`. It refuses an occupied port; it does not
reuse an existing server. Test evidence and server logs go to the ignored
`tests/browser/artifacts/` directory. Default CI runs leave these checked-in
screenshots untouched.

Screenshots complement [automated checks](../IMPLEMENTATION.md); they do not
establish physical-phone, Safari/iOS, Windows or live-provider acceptance.
