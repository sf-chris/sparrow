# Troubleshooting

Start with:

```bash
.venv/bin/python -m backend.doctor
```

## Torrent client is unreachable

Confirm Transmission or qBittorrent is running and its RPC/Web UI is enabled.
For the Homebrew Transmission daemon:

```bash
brew services restart transmission-cli
transmission-remote 127.0.0.1:9091 --session-info
```

## Media verification is unavailable

Install ffmpeg and confirm `ffprobe -version` succeeds. Sparrow will not perform
a verified library upgrade without it.

## The phone cannot connect

Confirm the phone and Sparrow host are on the same trusted network, the host
firewall allows port 8888 locally, and `.env` contains:

```dotenv
SPARROW_HOST=0.0.0.0
SPARROW_ALLOW_LAN=1
```

Never solve this by public port forwarding.

## A job stopped after restart

Open Activity and the job journal. Running sessions are repaired and woken after
boot; hibernating sessions retain their messages and wake conditions. Check
`data/logs/sparrow.log` and the LaunchAgent stderr log for environmental errors.

## Configuration appears to lose a secret

This is expected in the browser response. Stored keys and client passwords are
write-only: the API returns a configured flag and an empty secret field. Leave
the field blank to keep its current value.
