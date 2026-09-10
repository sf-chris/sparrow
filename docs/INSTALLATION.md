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
Server settings accepts TMDB and reasoning credentials; imported media plays
without either provider. Subtitle discovery can optionally use OpenSubtitles.

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

To serve your trusted home network, create `.env` containing the server's LAN
address, for example `SPARROW_BIND_ADDRESS=192.168.1.20`, then recreate the container
with `docker compose up -d`. Open that address on the phone and Windows node.
Plain HTTP is a local-network option; installing the PWA on a phone requires a
trusted secure browser context. External access setup is deferred.

For server-local files, add explicit bind mounts for separate library/incoming
folders and choose their **container paths** in Storage settings. UID 10001 must
have access. Windows paths belong in Sparrow Node's setup, not Linux settings.

## Windows storage

The `Build Windows storage node` workflow builds an installer on Windows and
runs installation, NTFS, authenticated playback and service-restart checks.
Download its `SparrowNode-Windows-x64` artifact after that workflow passes. This
checkout does not imply that an installer has already been published or that
physical Windows hardware has passed validation.

1. In the web app, open **Storage & import → Pair storage**, name the node and
   generate a pairing code.
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
6. Refresh Storage in the web app, choose **Import existing media**, review the
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
The measured speech benchmark additionally needs the pinned local model. See
`tests/subtitle_benchmark.py` and its fixture attribution.

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
