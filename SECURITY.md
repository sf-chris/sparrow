# Security policy

## Supported versions

Sparrow is an active alpha. Security fixes target the latest revision on `main`;
older alpha snapshots are not maintained separately. Include the commit from
`git rev-parse HEAD` when reporting an issue.

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability. Use **Security →
Report a vulnerability** if GitHub offers that option for this repository.
Otherwise, contact the repository owner, [@sf-chris](https://github.com/sf-chris),
through your existing private collaboration channel. Do not put sensitive
reproduction details in a public issue or pull request.

Include the affected revision, impact, reproduction steps and whether the issue
could expose credentials, bypass household/storage permissions, execute code,
escape the media-directory jail or delete library files. Use fictional titles
and generated media; omit real API keys, setup/invitation codes, session cookies
and personal library data.

## Current deployment boundary

The current application includes local accounts, household roles, scoped storage
access and revocable browser sessions. The supported installation target is a
Linux server used locally or on an explicitly configured trusted home network.
The normal Compose configuration binds to loopback; LAN binding is an explicit
installation choice.

Guided external DNS/HTTPS/sharing remains deferred. The presence of authentication
does not establish that a publicly exposed deployment has passed security and
operational acceptance. Native Windows storage and physical-device validation
also have open gates; see [the implementation record](docs/IMPLEMENTATION.md).

Keep provider credentials, node pairing state, setup codes and state backups
private. [The installation guide](docs/INSTALLATION.md) covers persistent state,
backup and upgrade behavior. Report any failure of file-preservation, permission,
request-revision or resource-limit enforcement as a security-relevant issue.

The retired CWM executor is disabled unless an operator deliberately sets
`SPARROW_ENABLE_LEGACY_CWM=1`. Keep it disabled on network-accessible instances.
