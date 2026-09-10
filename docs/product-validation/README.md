# Product validation evidence

These screenshots use an isolated six-title catalogue with synthetic poster art
and generated test video. They are application captures, not proposed mockups or
evidence of the owner's real collection being deployed. The `redesign-live-*`
files separately capture the deployed sign-in screen without account details.

| Journey | Screens / machine-readable evidence |
| --- | --- |
| Collection and title | [Home](home-desktop.png), [Library on phone](library-mobile.png), [Title](movie-desktop.png) |
| Watching | [Player](player-desktop.png), [Converted audio](player-converted-desktop.png), [Journey results](browser-results.json) |
| Household settings | [Preferences](preferences-mobile.png), [Accounts](people-mobile.png), [Invitation](invite-link-mobile.png) |
| Administrator setup | [Desktop](owner-setup-desktop.png), [Phone](administrator-setup-mobile.png), [Setup layout checks](administrator-setup-results.json) |
| New visual identity | [Setup](administrator-setup-desktop.png), [Sign in](sign-in-desktop.png), [Discovery](discover-desktop.png), [Phone prompt](discovery-prompt-mobile.png), [Empty library](empty-library-mobile.png), [Load failure](library-error-mobile.png), [Design checks](redesign-results.json) |
| Exact intent and controls | [Episode selection](request-episodes-mobile.png), [Paused request](activity-paused-mobile.png) |
| Collection care | [Phone dialog](collection-care-dialog-mobile.png), [Following a show](following-show-mobile.png) |
| Subtitle recovery | [Repair](subtitle-repair-mobile.png), [Failed upload](subtitle-repair-failure-mobile.png), [Speech benchmark](subtitle-benchmark.json) |
| Accessibility | [Screen checks](accessibility-results.json), [Household/dialog checks](household-results.json) |
| Deployed redesign | [Sign in](redesign-live-desktop.png), [Phone](redesign-live-mobile.png), [Live checks](redesign-live-results.json) |
| Packaged installation | [Linux image checks](package-results.json), [Node layout on Linux Tk](node-layout-results.json) |

Coverage and remaining release gates are in [IMPLEMENTATION.md](../IMPLEMENTATION.md).
