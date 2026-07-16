# Deployment and recovery

## Private alpha model

The supported alpha topology is one trusted household machine, one local
torrent client, local media storage, and optional same-Wi-Fi browser access.
Public-internet access is unsupported.

## macOS service

Install dependencies and prove readiness first:

```bash
./scripts/install.sh
.venv/bin/python -m backend.doctor
./scripts/install-launchd.sh
```

Inspect service state and logs:

```bash
launchctl print "gui/$(id -u)/com.sparrow.media"
tail -f data/logs/launchd.err.log
```

Remove the service without deleting Sparrow state or media:

```bash
./scripts/uninstall-launchd.sh
```

## Backup and restore

Create a private state archive:

```bash
./scripts/backup.sh /path/to/private/backups
```

Stop Sparrow before restoring. Extract the selected archive into the configured
`SPARROW_DATA_DIR`, preserve owner-only permissions, then run the doctor and
restart. Media files are not included in the state archive and should use the
normal storage backup strategy.

## Network

Loopback is the default. Trusted-LAN use requires both a non-loopback host and
the explicit safety acknowledgement:

```dotenv
SPARROW_HOST=0.0.0.0
SPARROW_ALLOW_LAN=1
```

The production UI is same-origin. `SPARROW_ALLOWED_ORIGINS` is needed only for
intentional cross-origin development clients. Authentication must ship before
any supported reverse proxy or public endpoint.
