# Sparrow

Sparrow is a local media autopilot. You choose a film or show; a persistent
agent searches, acquires, verifies, and organises it, then keeps responsibility
for the job until the library actually satisfies the request.

This repository is an early alpha. The v3 agent loop works and is suitable for
a technical private deployment. First-class subscriptions, Sparrow-native
playback, automatic subtitles, remote access, and music are on the
[roadmap](ROADMAP.md), not silently implied features.

## Why Sparrow

Traditional media automation makes the operator assemble search managers,
indexer protocols, downloaders, library managers, and recovery rules. Sparrow
puts uncertain decisions in persistent tool-using agents while deterministic
tools establish facts and enforce safety.

- A job is a contract against TMDB, not a "download started" flag.
- Torrent listings and ffprobe verify what actually exists.
- A Media Agent reports landed-file evidence back to the Fetch Agent.
- Sessions hibernate and recover across restarts.
- The journal explains progress in ordinary language.
- The filesystem jail and verified upgrade swap live below the model.

Read [DESIGN.md](DESIGN.md) before changing the architecture.

## Alpha preview

The current alpha has a guided local setup and a plain-language agent journal
that only declares a title ready after file and inventory verification.

![Sparrow onboarding readiness screen](docs/assets/alpha-onboarding.png)

![A verified Big Buck Bunny job ready to watch](docs/assets/alpha-ready.png)

## Alpha requirements

- Python 3.11+
- Node.js 20+ for installation/frontend builds
- ffmpeg/ffprobe
- Transmission 4 or qBittorrent with its RPC/Web UI enabled
- A TMDB API key
- An Anthropic API key

Sparrow currently uses TPB as its first search source. Operate Sparrow only with
media you are legally entitled to acquire and store.

## Quick start

### macOS

```bash
brew install ffmpeg transmission-cli
brew services start transmission-cli
./scripts/install.sh
./start.sh
```

Open <http://127.0.0.1:8888>, complete onboarding, and run the doctor:

```bash
.venv/bin/python -m backend.doctor
```

If Transmission uses a different download location, set it to Sparrow's staging
folder. For the default private layout:

```bash
transmission-remote 127.0.0.1:9091 \
  --download-dir "$HOME/Sparrow/Temp"
```

To start Sparrow automatically at login:

```bash
./scripts/install-launchd.sh
```

### Debian/Ubuntu (documented, not manually validated yet)

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv nodejs npm ffmpeg transmission-daemon
./scripts/install.sh
./start.sh
```

Distribution Node.js packages vary; use a maintained Node 20+ installation if
the packaged version is older. CI validates the Python and frontend build on
Ubuntu; the complete daemon/client flow has only been manually exercised on
macOS for this alpha.

## Home-network use

Sparrow defaults to loopback. To open it from a phone on a trusted home network,
set the following in `.env` and restart:

```dotenv
SPARROW_HOST=0.0.0.0
SPARROW_ALLOW_LAN=1
```

Then open `http://<computer-lan-ip>:8888`. Do not port-forward Sparrow or expose
it to the public internet. Authentication and the hosted relay are not part of
this alpha.

## Development

```bash
./scripts/install.sh
./dev.sh
```

The backend runs at <http://localhost:8888> and Vite at
<http://localhost:3000>.

Run the confidence suite:

```bash
./scripts/check.sh
```

The important API surfaces are:

- `GET /api/resolve?q=`
- `POST /api/jobs`
- `GET /api/jobs/{id}`
- `GET /api/journal`
- `GET /api/agent-sessions`
- `GET /api/doctor`
- WebSocket `/ws`

Stored credentials are write-only through the browser API. `GET /api/config`
returns configured flags and blank secret fields.

## State, logs, and recovery

Private state lives under `data/` by default. Agent jobs/sessions and legacy
records share `data/sparrow.db`; agent notes live under `data/memory/`.

```bash
./scripts/backup.sh
tail -f data/logs/launchd.err.log
```

See [deployment and recovery](docs/DEPLOYMENT.md) and
[troubleshooting](docs/TROUBLESHOOTING.md).

## Security and limitations

- Public-internet exposure is unsupported.
- Retired CWM code-execution routes are disabled in normal builds.
- The first source connector is TPB; the connector SDK is not ready.
- There is no built-in player or push notification yet.
- Monitoring exists only as early Librarian behavior, not the durable
  subscription contract described in the roadmap.

Please report vulnerabilities according to [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Sparrow is licensed under the GNU Affero
General Public License v3.0 or later; see [LICENSE](LICENSE).
