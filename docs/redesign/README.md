# Redesign: before and after

Every screen of the `main` design (before) and the TV-guide redesign (after), captured by
[`tests/browser/gallery.cjs`](../../tests/browser/gallery.cjs) from the same fresh fictional
fixture: an owner, a second household member, two titles in progress, two requests and one
followed series. Desktop is 1440 px wide, phone 390 px; both are full-page captures.

The design contract is [PRODUCT_DESIGN.md](../PRODUCT_DESIGN.md).

```sh
# Build the frontend first. The gallery needs a fresh fixture state.
export SPARROW_BROWSER_STATE="$(mktemp -d)" SPARROW_BROWSER_PORT=8891
.venv/bin/python tests/browser/server.py &
SPARROW_GALLERY_OUT=docs/redesign/after node tests/browser/gallery.cjs
```

## Cover, new server

| Before | After |
| --- | --- |
| ![Welcome page, before](before/landing-first-run-desktop.png) | ![Cover, new server, after](after/landing-first-run-desktop.png) |
| <img src="before/landing-first-run-mobile.png" width="260" alt="Welcome page on a phone, before" /> | <img src="after/landing-first-run-mobile.png" width="260" alt="Cover, new server on a phone, after" /> |

## Cover

| Before | After |
| --- | --- |
| ![Welcome page, before](before/landing-desktop.png) | ![Cover, after](after/landing-desktop.png) |
| <img src="before/landing-mobile.png" width="260" alt="Welcome page on a phone, before" /> | <img src="after/landing-mobile.png" width="260" alt="Cover on a phone, after" /> |

## Set up Sparrow

| Before | After |
| --- | --- |
| ![Owner account, before](before/account-setup-desktop.png) | ![Set up Sparrow, after](after/account-setup-desktop.png) |
| <img src="before/account-setup-mobile.png" width="260" alt="Owner account on a phone, before" /> | <img src="after/account-setup-mobile.png" width="260" alt="Set up Sparrow on a phone, after" /> |

## Sign in

| Before | After |
| --- | --- |
| ![Sign in, before](before/sign-in-desktop.png) | ![Sign in, after](after/sign-in-desktop.png) |
| <img src="before/sign-in-mobile.png" width="260" alt="Sign in on a phone, before" /> | <img src="after/sign-in-mobile.png" width="260" alt="Sign in on a phone, after" /> |

## Join from an invitation

| Before | After |
| --- | --- |
| ![Invitation, before](before/join-desktop.png) | ![Join from an invitation, after](after/join-desktop.png) |
| <img src="before/join-mobile.png" width="260" alt="Invitation on a phone, before" /> | <img src="after/join-mobile.png" width="260" alt="Join from an invitation on a phone, after" /> |

## First sign-in: household defaults

| Before | After |
| --- | --- |
| ![First preferences, before](before/welcome-desktop.png) | ![First sign-in: household defaults, after](after/welcome-desktop.png) |
| <img src="before/welcome-mobile.png" width="260" alt="First preferences on a phone, before" /> | <img src="after/welcome-mobile.png" width="260" alt="First sign-in: household defaults on a phone, after" /> |

## Server setup: how to start

| Before | After |
| --- | --- |
| ![Server setup, before](before/server-setup-desktop.png) | ![Server setup: how to start, after](after/server-setup-desktop.png) |
| <img src="before/server-setup-mobile.png" width="260" alt="Server setup on a phone, before" /> | <img src="after/server-setup-mobile.png" width="260" alt="Server setup: how to start on a phone, after" /> |

## Server setup: check

| Before | After |
| --- | --- |
| ![Setup review, before](before/server-setup-review-desktop.png) | ![Server setup: check, after](after/server-setup-review-desktop.png) |
| <img src="before/server-setup-review-mobile.png" width="260" alt="Setup review on a phone, before" /> | <img src="after/server-setup-review-mobile.png" width="260" alt="Server setup: check on a phone, after" /> |

## Guide (home and library in one)

| Before | After |
| --- | --- |
| ![Home, before](before/home-desktop.png) | ![Guide (home and library in one), after](after/home-desktop.png) |
| <img src="before/home-mobile.png" width="260" alt="Home on a phone, before" /> | <img src="after/home-mobile.png" width="260" alt="Guide (home and library in one) on a phone, after" /> |

## `/library` now opens the Guide

| Before | After |
| --- | --- |
| ![Library, before](before/library-desktop.png) | ![`/library` now opens the Guide, after](after/library-desktop.png) |
| <img src="before/library-mobile.png" width="260" alt="Library on a phone, before" /> | <img src="after/library-mobile.png" width="260" alt="`/library` now opens the Guide on a phone, after" /> |

## Find

| Before | After |
| --- | --- |
| ![Discover, before](before/discover-desktop.png) | ![Find, after](after/discover-desktop.png) |
| <img src="before/discover-mobile.png" width="260" alt="Discover on a phone, before" /> | <img src="after/discover-mobile.png" width="260" alt="Find on a phone, after" /> |

## Find: title matches

| Before | After |
| --- | --- |
| ![Discover results, before](before/discover-results-desktop.png) | ![Find: title matches, after](after/discover-results-desktop.png) |
| <img src="before/discover-results-mobile.png" width="260" alt="Discover results on a phone, before" /> | <img src="after/discover-results-mobile.png" width="260" alt="Find: title matches on a phone, after" /> |

## Series

| Before | After |
| --- | --- |
| ![Show page, before](before/title-show-desktop.png) | ![Series, after](after/title-show-desktop.png) |
| <img src="before/title-show-mobile.png" width="260" alt="Show page on a phone, before" /> | <img src="after/title-show-mobile.png" width="260" alt="Series on a phone, after" /> |

## Film

| Before | After |
| --- | --- |
| ![Movie page, before](before/title-movie-desktop.png) | ![Film, after](after/title-movie-desktop.png) |
| <img src="before/title-movie-mobile.png" width="260" alt="Movie page on a phone, before" /> | <img src="after/title-movie-mobile.png" width="260" alt="Film on a phone, after" /> |

## Request episodes

| Before | After |
| --- | --- |
| ![Request dialog, before](before/request-desktop.png) | ![Request episodes, after](after/request-desktop.png) |
| <img src="before/request-mobile.png" width="260" alt="Request dialog on a phone, before" /> | <img src="after/request-mobile.png" width="260" alt="Request episodes on a phone, after" /> |

## Player

| Before | After |
| --- | --- |
| ![Player, before](before/player-desktop.png) | ![Player, after](after/player-desktop.png) |
| <img src="before/player-mobile.png" width="260" alt="Player on a phone, before" /> | <img src="after/player-mobile.png" width="260" alt="Player on a phone, after" /> |

## Requests and Following

| Before | After |
| --- | --- |
| ![Activity, before](before/activity-desktop.png) | ![Requests and Following, after](after/activity-desktop.png) |
| <img src="before/activity-mobile.png" width="260" alt="Activity on a phone, before" /> | <img src="after/activity-mobile.png" width="260" alt="Requests and Following on a phone, after" /> |

## Preferences

| Before | After |
| --- | --- |
| ![Preferences, before](before/preferences-desktop.png) | ![Preferences, after](after/preferences-desktop.png) |
| <img src="before/preferences-mobile.png" width="260" alt="Preferences on a phone, before" /> | <img src="after/preferences-mobile.png" width="260" alt="Preferences on a phone, after" /> |

## Account

| Before | After |
| --- | --- |
| ![Account & security, before](before/account-desktop.png) | ![Account, after](after/account-desktop.png) |
| <img src="before/account-mobile.png" width="260" alt="Account & security on a phone, before" /> | <img src="after/account-mobile.png" width="260" alt="Account on a phone, after" /> |

## Logs

| Before | After |
| --- | --- |
| ![Logs, before](before/logs-desktop.png) | ![Logs, after](after/logs-desktop.png) |
| <img src="before/logs-mobile.png" width="260" alt="Logs on a phone, before" /> | <img src="after/logs-mobile.png" width="260" alt="Logs on a phone, after" /> |

## People

| Before | After |
| --- | --- |
| ![People, before](before/people-desktop.png) | ![People, after](after/people-desktop.png) |
| <img src="before/people-mobile.png" width="260" alt="People on a phone, before" /> | <img src="after/people-mobile.png" width="260" alt="People on a phone, after" /> |

## Invite someone

| Before | After |
| --- | --- |
| ![Invite dialog, before](before/invite-desktop.png) | ![Invite someone, after](after/invite-desktop.png) |
| <img src="before/invite-mobile.png" width="260" alt="Invite dialog on a phone, before" /> | <img src="after/invite-mobile.png" width="260" alt="Invite someone on a phone, after" /> |

## Storage

| Before | After |
| --- | --- |
| ![Storage & import, before](before/storage-desktop.png) | ![Storage, after](after/storage-desktop.png) |
| <img src="before/storage-mobile.png" width="260" alt="Storage & import on a phone, before" /> | <img src="after/storage-mobile.png" width="260" alt="Storage on a phone, after" /> |

## Household defaults

| Before | After |
| --- | --- |
| ![Defaults, before](before/defaults-desktop.png) | ![Household defaults, after](after/defaults-desktop.png) |
| <img src="before/defaults-mobile.png" width="260" alt="Defaults on a phone, before" /> | <img src="after/defaults-mobile.png" width="260" alt="Household defaults on a phone, after" /> |

## Connections

| Before | After |
| --- | --- |
| ![Server settings, before](before/server-desktop.png) | ![Connections, after](after/server-desktop.png) |
| <img src="before/server-mobile.png" width="260" alt="Server settings on a phone, before" /> | <img src="after/server-mobile.png" width="260" alt="Connections on a phone, after" /> |

## Themes

The Guide is the official theme; the other three are personal choices in
Preferences. Each row is the same fixture, captured by the theme screenshot
run: the Guide at 1440 px and a phone at 390 px.

| Theme | Guide | Phone |
| --- | --- | --- |
| Guide | ![Guide: the Guide](themes/guide-guide.jpg) | <img src="themes/guide-guide-phone.jpg" width="200" alt="Guide: the Guide on a phone" /> |
| Cinema | ![Cinema: the Guide](themes/cinema-guide.jpg) | <img src="themes/cinema-guide-phone.jpg" width="200" alt="Cinema: the Guide on a phone" /> |
| Clear | ![Clear: the Guide](themes/clear-guide.jpg) | <img src="themes/clear-guide-phone.jpg" width="200" alt="Clear: the Guide on a phone" /> |
| Saturday | ![Saturday: the Guide](themes/saturday-guide.jpg) | <img src="themes/saturday-guide-phone.jpg" width="200" alt="Saturday: the Guide on a phone" /> |

