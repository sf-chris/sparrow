# Security Policy

## Supported versions

Only the latest `0.1.x-alpha` revision is supported while Sparrow is in alpha.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Once the GitHub
repository exists, use its private vulnerability-reporting/security-advisory
form. Until then, report privately to the repository owner who supplied this
checkout.

Include the affected revision, impact, reproduction steps, and whether the
issue could expose credentials, execute code, escape the media-directory jail,
or delete library files. Do not include real API keys, private media names, or
personal library data.

## Current boundary

Sparrow `0.1.0-alpha` supports local and explicitly enabled trusted-LAN use. It
does not have authentication and must not be port-forwarded, reverse-proxied to
the public internet, or deployed on an untrusted shared network.

The retired CWM executor is disabled unless an operator deliberately sets
`SPARROW_ENABLE_LEGACY_CWM=1`. Never enable it on a network-accessible instance.
