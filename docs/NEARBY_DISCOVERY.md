# Nearby server and storage discovery investigation

10 September 2026. This completes the bounded investigation/prototype requested
in [DESIGN_FOLLOW_UP.md](DESIGN_FOLLOW_UP.md). It does not enable discovery in the
application, restart TV/client work, or add a share-mounting feature.

## Proposed experience

The node setup application offers **Find nearby servers** beside the existing
server-address field. It shows a short list of names with an address and a clear
unverified state. Choosing a result fills a candidate address; it does not send
credentials or pair. Manual entry remains available at all times.

The server's Storage screen can eventually offer **Find nearby storage**. This
is a different list: an advertised SMB service identifies a possible storage
device, not a mounted share or a Sparrow node. Some NAS devices do not advertise
SMB, and many devices expose multiple shares with different permissions. Absence
from discovery does not mean the storage is unavailable.

An ordinary browser cannot run this multicast exchange through the existing
HTTP API. Native node setup discovers on the node's network. A future admin-only
server endpoint would discover on the server's network, which may differ from
the browser's. Display that distinction in any shipped chooser.

## Protocol and boundary

Use DNS-SD over mDNS. DNS-SD's service records provide named instances, a target
and port, and a small metadata record. It supports the chooser without trying
arbitrary hosts and ports. The prototype queries exactly `_sparrow._tcp.local.`
and `_smb._tcp.local.`; `_sparrow` is a prototype service name, not a claim of an
IANA registration. [RFC 6763](https://www.rfc-editor.org/rfc/rfc6763.html)

Emby's documented UDP 7359 exchange is a useful example of discovering a server
address. Sparrow does not use that exchange or implement an Emby client.
[Emby server discovery](https://github.com/MediaBrowser/Emby/wiki/Locating-the-Server)

Local announcements can be forged. They can also reveal that a service exists
on the network. A local network name, address, advertised protocol version, or
claim to use HTTPS is not an identity proof. The mDNS security considerations
explain why discovery needs a separate authentication boundary.
[RFC 6762, section 21](https://www.rfc-editor.org/rfc/rfc6762.html#section-21)

Before a discovered server receives pairing secrets, validate its HTTPS identity
using a trusted certificate or an independently verified/pinned fingerprint.
An administrator still generates the existing short-lived enrollment code, and
the node operator explicitly approves the server and local folders. The current
manual private-HTTP escape hatch is not permission to send pairing secrets to a
discovery responder automatically. A shipped chooser must enforce this boundary.

NAS setup separately requires explicit selection and credentials for a share,
least-privilege access, and deliberate mount/folder approval. The prototype never
contacts SMB, enumerates files or shares, mounts anything, generates enrollment
codes, or changes server/node configuration. It does not reuse Sparrow's node
handshake for arbitrary storage devices.

## Executable prototype

[backend/agents/nearby.py](../backend/agents/nearby.py) contains a server advertiser,
a bounded node/server browser, and a shared candidate validator. Importing it
opens no sockets; normal Sparrow startup does not invoke it. The optional
[requirements-discovery.txt](../requirements-discovery.txt) is separate from the
packaged application dependencies. The implementation uses python-zeroconf's
interface selection, service browser and service registration APIs.
[python-zeroconf API](https://python-zeroconf.readthedocs.io/en/latest/api.html)

For a deliberate development experiment, install the optional dependency in an
isolated environment and run from the repository root:

```sh
python3 -m venv /tmp/sparrow-discovery
/tmp/sparrow-discovery/bin/pip install -r requirements-discovery.txt
# Use the actual private address and subnet of the chosen interface.
/tmp/sparrow-discovery/bin/python -m backend.agents.nearby browse \
  --interface 192.168.1.10/24 --seconds 4
# In another terminal on the server, announce its actual address/transport.
/tmp/sparrow-discovery/bin/python -m backend.agents.nearby advertise \
  --interface 192.168.1.20/24 --name 'Living room cinema' \
  --port 8888 --transport http
```

These are examples, not commands run on the owner's LAN. Advertisement runs
until Ctrl+C and withdraws its record when stopped. It does not start an HTTP
server or make an existing one reachable.

The prototype explicitly selects one private IPv4 interface and its subnet;
loopback is supported for local tests. It rejects off-subnet/public targets,
malformed names, unsupported service types, zero-TTL announcements and unusable
ports. Results include at most 120 seconds of candidate lifetime, a compatibility
hint and `trusted: false`. Only protocol version and transport are advertised;
setup codes, credentials, account identities, paths and file lists are absent.

Browsing lasts 1–10 seconds, queues at most 128 announcements and resolves at
most 64 distinct instances with at most 300 ms per resolution inside the overall
deadline. Sockets and the browser close on success or failure. Duplicate results
are collapsed and service removals remove a candidate. No generic service-type,
host or port sweep occurs. Candidate filtering reduces accidental address misuse;
it does not authenticate a malicious responder on the selected subnet.

## Measured result and remaining release work

`tests/test_nearby.py` checks candidate validation, spoofed fields, incompatible
versions, expiry, unknown service types, bounded browsing, cleanup and the
advertisement's minimal metadata. A real zeroconf 0.151.3 advertiser and browser
also found **Fixture cinema** over `127.0.0.1/8` in a two-second browse. Only the
loopback interface was used. There was no owner-LAN scan or physical NAS check.

Keep this opt-in prototype out of the normal setup UI until the authentication
chooser and the deployment checks are ready. Windows firewall/service behavior,
IPv6 interface scopes, Wi-Fi isolation, VLANs, VPNs, container multicast routing,
and a representative NAS remain unvalidated. No automatic firewall changes,
multicast relays or broader scans should be used to hide those limitations.
Ship an honest empty/error state and manual address entry when discovery cannot
work. Native Windows currently accepts local drive folders; discovery alone does
not remove that share/service-account limitation.
