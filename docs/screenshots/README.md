# A look around Sparrow

The current interface, captured from the production frontend on
28 September 2026 with the isolated browser fixture: six fictional titles,
original geometric covers and generated video. No personal library,
credentials or paid-provider results appear.

## Landing page

![Public landing page](landing-desktop.png)

## Home

![Home with Continue watching and library posters](home-desktop.png)

## Find

![Find with title search and example requests](discover-desktop.png)

## Title

![Title page with backdrop and episode list](title-desktop.png)

| Screen | Desktop · 1440 × 1000 | Phone · 390 × 844 |
| --- | --- | --- |
| Landing page | [View](landing-desktop.png) | [View](landing-mobile.png) |
| Home | [View](home-desktop.png) | [View](home-mobile.png) |
| Library | [View](library-desktop.png) | [View](library-mobile.png) |
| Find | [View](discover-desktop.png) | [View](discover-mobile.png) |
| Show detail | [View](title-desktop.png) | [View](title-mobile.png) |
| Preferences | [View](preferences-desktop.png) | [View](preferences-mobile.png) |

Phone images capture one viewport so the bottom navigation stays in its natural
position. The app scrolls normally to the remaining content. More account,
security, log, dialog and failure states are in the
[follow-up validation gallery](../follow-up-validation/README.md).

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
