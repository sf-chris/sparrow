# Install Sparrow

The server runs on Linux. Media can stay on a paired Windows machine. These
instructions cover local installation and accounts. Guided external DNS/HTTPS,
TV/Emby/casting and a deployment agent remain outside this implementation.

## Linux server

Install Docker Engine and its Compose plugin, then run from this checkout:

```sh
docker compose up -d --build
docker compose exec -T sparrow python -m backend.agents.setup_info --url http://localhost:8888
```

The second command prints a link that fills the setup code automatically, and
the code for manual entry. Open the link and choose **Create administrator account**,
then choose household defaults. This is a one-time first-account setup, entirely
on your server. The setup code stops working after the account is made.
The server setup flow then guides you through TMDB, Provider and optional AltProvider
keys, local or paired storage, a download app and a setup summary. Each key field
says whether the server already has that key; a blank box keeps it. The download
step looks for Transmission or qBittorrent by itself (this server, and the Docker
host and Compose services when in a container). If none is found, **Set up
Transmission for me** runs the Transmission bundled in the image, listening on
the container's loopback only with a generated password, and restarts it with
Sparrow. Choose **Watch my
existing collection** to import and watch without API keys or a download app.
Settings and the current step survive refresh, sign-out and server restart.
**Finish later** leaves setup unfinished and provides a **Continue setup** link;
return at any time through **Settings → Server setup** or `/setup`.
Existing administrator accounts with unfinished setup use this same flow.
The summary checks current storage availability and labels saved credentials
honestly; provider access is exercised by the first request, without paid model
calls during setup. Subtitle discovery can optionally use OpenSubtitles.

Sparrow also prints **Setup code:** in its startup logs until administrator setup
is complete. View it with `docker compose logs sparrow`, or rerun the command above
to retrieve the current code without changing it. The original `owner-setup-code`
file in the data directory remains available as a fallback. After setup, the
command reports that an administrator already exists and does not display a code.

If an agent installs Sparrow, its final handoff should include the browser URL,
the setup link and the code. Pass the address you will actually open to `--url`:
your LAN address for another device at home, or `http://localhost:8888` when using
an SSH tunnel that forwards your computer's port 8888. The link's fragment fills
the code locally in your browser and is then removed from the address bar.
Your name, username and password still require your input; opening the link
does not create an account. Invite family members afterward through **Settings → People**.

The initial build includes the frontend, FFmpeg, subtitle processing libraries
and the local speech model; it takes several minutes. A named Docker volume
retains accounts, library mappings, agent history, progress and prepared tracks.
The application runs as UID 10001 without root capabilities and with one
coordinator process. `docker compose logs --tail=100 sparrow` shows startup errors.

Subtitle inclusion is on by default; **Check subtitle sync** is a separate,
optional preference that uses the Provider key in Settings. Compose also accepts
`SPARROW_SMART_MODEL`, `SPARROW_CHEAP_MODEL` and `SPARROW_TRANSCRIPTION_MODEL`
from `.env`. The image bundles Whisper base. To use a different compatible local
speech model, first put its files in the persistent volume or a mounted directory,
then set its **container path**, for example `/data/models/whisper-medium`, and
recreate the container. The recorded Japanese/English anime trial used Whisper
medium, Small 4.5 and Medium 4.6; it does not establish equivalent results for base.
Subtitle timing uses whole-episode dialogue evidence built on the storage node. It
uses Whisper large-v3-turbo when present at `/data/models/whisper-large-v3-turbo`,
then medium at `/data/models/whisper-medium`, then the bundled base model, or the
directory in `SPARROW_EVIDENCE_MODEL`. `python packaging/fetch_model.py DESTINATION
whisper_evidence` fetches the pinned turbo revision (about 1.6 GB). The optional
subtitle agent (Preferences → Subtitle agent) needs an Provider key in Settings
and uses Assistant Large. Add an AltProvider key in **Settings → Connections** (or
`ALTPROVIDER_API_KEY` in `.env`; a saved key wins) so Large manages cheap
page checkers (the cheap model, with the smart model re-checking flags) instead of reading
every page itself; measured costs are under $1 per episode or film.
`SPARROW_SUBTITLE_MODEL`, `SPARROW_SUBTITLE_EFFORT`, `SPARROW_SUBTITLE_CONTRACTOR`,
`SPARROW_SUBTITLE_VERIFIER` and `SPARROW_SUBTITLE_BUDGET` (dollars per title;
default: the household's per-case AI allowance in Defaults) adjust it.

To serve your trusted home network, create `.env` containing the server's LAN
address, for example `SPARROW_BIND_ADDRESS=192.168.1.20`, then recreate the container
with `docker compose up -d`. Open that address on the phone and Windows node.
Set `SPARROW_PORT=8081` in the same file to use a different browser port, then
recreate the container and open `http://<server-address>:8081`. The container
continues to use port 8888 internally. Use the chosen browser port in setup links
and node server addresses too.
Plain HTTP is a local-network option; installing the PWA on a phone requires a
trusted secure browser context. External access setup is deferred.

Compose mounts a media folder at `/media`. Without settings it is a Docker volume
holding `/media/Library` and `/media/Incoming`. To use a folder on the server, set
`SPARROW_MEDIA_DIR=/srv/media` in `.env`, and set `SPARROW_MEDIA_GID` to that
folder's group ID (`stat -c %g /srv/media`) so UID 10001 can write to
group-writable folders. Then choose `/media/...` paths in Storage; setup suggests
them and creates a missing last folder. Library and incoming folders on the same
mount move files without copying. Windows paths belong in Sparrow Node's setup,
not Linux settings. A download app outside the container must save to the
incoming folder at the same path Sparrow sees, so Sparrow's own Transmission is
simpler in Docker.

## Pair a computer

Another computer can hold part of the collection. In **Settings → Storage**,
choose **Pair a computer**, pick its system and paste the one line shown into
Terminal (Mac, Linux) or PowerShell (Windows) on that computer:

```sh
curl -fsSL http://<server>/api/v1/node/setup/<code>.sh | sh   # Mac and Linux
irm http://<server>/api/v1/node/setup/<code>.ps1 | iex       # Windows
```

The command installs Sparrow Node, pairs it and keeps it running in the
background. The dialog follows along and, once the computer connects, asks for
its library folder and a separate incoming folder, browsed on that computer from
Sparrow. The computer takes its own name. Each command works once and for ten
minutes (longer once it has started); an old one says so and asks for a fresh
one.

What it sets up:

- **Python:** the computer's own Python 3.11 or newer, otherwise a private copy
  downloaded with [uv](https://docs.astral.sh/uv/) into Sparrow Node's folder.
  Sparrow Node's code and its one library (httpx) come from your server.
- **Linux:** a systemd user service that keeps running after you sign out
  (`loginctl enable-linger`; the command says if that needs `sudo`). Run as
  root (`| sudo sh`), it installs a system service for your user, and on Debian
  or Ubuntu also installs ffmpeg and Transmission. Without systemd it starts now
  and from cron at reboot.
- **Mac:** a LaunchAgent that starts when you sign in.
- **Windows:** a scheduled task. From PowerShell run as administrator it starts
  with Windows; otherwise when you sign in.
- **Downloads:** if Transmission is installed (`transmission-daemon`), the
  computer runs its own copy for Sparrow, saving into its incoming folder once
  that's chosen. Without it, the computer stores and plays media but doesn't
  download. ffmpeg enables media checks. The command prints how to add either.

Folders can only be chosen inside the computer's own places: its home, drives
and mounted disks (in Docker, the mounted folders). For this server, set
`SPARROW_FOLDER_ROOTS` (paths separated by `:`) to offer other places instead. Choose **Change folders** on
its card to move them later; other library disks keep their place. Reconnect
runs the same command with a fresh code and keeps the chosen folders.

State lives in `~/.local/share/SparrowNode` (Linux), `~/Library/Application
Support/SparrowNode` (Mac) or `%LOCALAPPDATA%\SparrowNode` (Windows), or
`/var/lib/sparrow-node` when installed by root. Don't share its `node.json`,
which holds the pairing credential. Revoke the computer in Sparrow when retiring
it. The same command was checked end to end on Linux (a clean Debian 12 without
Python, and as root with apt); Mac and Windows scripts are syntax-checked only,
so treat them as unproven until run on real hardware.

## Windows storage (installer)

The `Build Windows storage node` workflow builds an installer on Windows and
runs installation, NTFS, authenticated playback and service-restart checks.
Download its `SparrowNode-Windows-x64` artifact after that workflow passes. This
checkout does not imply that an installer has already been published or that
physical Windows hardware has passed validation.

The one-line command above is simpler. The installer is the alternative for a
machine that should run as a Windows service with its own account.

1. In the web app, open **Settings → Storage → Pair a computer** and choose
   Windows. The code is the last part of the command, before `.ps1`.
2. Run `SparrowNode-Setup-x64.exe` on Windows. Open **Connect Sparrow storage**.
   Windows requests administrator access once for service installation.
3. Enter the server origin, the ten-minute code, a library folder and a separate
   incoming folder. Choose local drive folders; this installer does not configure
   network-share credentials. If using HTTP on your trusted home network, select
   the explicit private-network option.
4. Optionally configure a local qBittorrent or Transmission Web/RPC connection for
   acquisition. Existing collection import and watching work without a download
   app. A download app must itself remain running when its Web/RPC service is used.
5. Choose **Pair storage & start service**. Close setup after it confirms success.
   The node runs as Windows LocalService with access granted to its own service
   SID for your selected folders, and starts automatically with Windows.
6. Refresh Storage in the web app, choose **Import files**, review the
   title/episode matches and import. Open the title to play or resume it.

No Python installation or separate subtitle service is needed on the storage
machine. Keep its drive connected and the machine awake while watching. Sleeping
storage appears unavailable; its catalogue and personal progress are retained.

Re-run setup with a fresh pairing code to reconnect; review credentials and
folders before starting. Upgrading the installer stops the service while binaries
are replaced. Uninstall stops/removes the service and preserves media and node
state in `%ProgramData%\SparrowNode`. Revoke the node in Sparrow when retiring it.
Logs are bounded under that state directory; access is limited to the service
and administrators. Do not share `node.json`, which contains its pairing credential.

## Linux storage in Docker

A Linux computer with Docker can also run Sparrow Node from this repository's
image. Set `SPARROW_MEDIA_DIR` (and `SPARROW_MEDIA_GID`) in `.env` as for the
server, take the code from **Pair a computer** (the part before `.sh`), then:

```sh
docker compose -f compose.node.yaml run --rm node configure \
  --server http://<server-address>:<port> --code <code> \
  --allow-private-http --managed-downloader
docker compose -f compose.node.yaml up -d
```

Choose its folders in Sparrow; inside Docker they're under `/media`. Folders can
still be given here with `--library` (repeat for each disk) and `--staging`.
Without `--managed-downloader` the computer stores and plays media but does not
download.

## Where new titles go

Each request is placed once, when it starts, and stays there. **Automatic** keeps
a show on the disk it already uses, skips storage that is offline, nearly full or
has no download app, then picks by the household's rule (most free space, fill in
order, or take turns) in **Settings → Storage**. A pool does the same within the
disks it lists. Each person can choose their own default in Preferences, and any
request can choose its own. A sleeping destination makes the request wait; it is
never downloaded again elsewhere. Administrators can move a title from its page:
every file is copied and checked before the original is removed.

## Checks and local development

Install Python 3.11, Node.js 22.12 or newer, FFmpeg/ffprobe and ripgrep first.

```sh
python3.11 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock
cd frontend
npm ci
cd ..
scripts/check.sh
```

Media tests require FFmpeg/ffprobe on PATH or `SPARROW_FFMPEG` and
`SPARROW_FFPROBE`. Tests skip live paid model evaluation unless explicitly enabled.
The speech-onset calibration additionally needs a local Whisper model. See
`tests/subtitle_sync_calibration.py` and its fixture attribution.

The [contributor guide](../CONTRIBUTING.md#browser-journeys-and-screenshots)
describes Chrome journeys and reproducible screenshots.

## Upgrade and back up

For a Compose installation, keep the checkout's directory name (and therefore
its Compose project/volume identity) the same when upgrading:

```sh
git pull --ff-only origin main
docker compose build
docker compose stop sparrow
mkdir -p backups
chmod 700 backups
docker compose run --rm --no-deps --entrypoint tar sparrow -czf - -C /data . > "backups/sparrow-state-$(date +%Y%m%d-%H%M%S).tar.gz"
chmod 600 backups/*.tar.gz
docker compose up -d
docker compose ps
```

The archive contains accounts, settings and credentials; keep it private.
Back up media separately if it lives in bind-mounted folders or paired nodes.
Keep the previous image available for rollback and restore the matching state
backup before reverting a schema change. Do not remove the named data volume
when updating. These local steps do not configure external access.

Before a schema generation changes, Sparrow snapshots existing SQLite databases
using SQLite's backup API and copies legacy JSON into `data/backups`. A failed
or malformed legacy migration stops with the original data retained. These local
migration snapshots are separate from a complete owner-managed media backup.
