"""Opt-in DNS-SD prototype for server/node setup; never pairs or mounts.

Not installed at application startup. Use this module's CLI on an explicitly
chosen interface. The optional zeroconf dependency is for this prototype only.
"""

import argparse
import ipaddress
import json
import queue
import re
import time

from .node_executor import PROTOCOL

SPARROW = "_sparrow._tcp.local."
SMB = "_smb._tcp.local."
SERVICE_TYPES = (SPARROW, SMB)
PRIVATE = tuple(
    ipaddress.ip_network(net)
    for net in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "127.0.0.0/8",
    )
)


def interface_scope(value):
    if "/" not in value:
        raise ValueError(
            "Choose an interface address with its subnet prefix, for example 192.168.1.10/24."
        )
    interface = ipaddress.ip_interface(value)
    if interface.version != 4 or not any(
        interface.network.subnet_of(net) for net in PRIVATE
    ):
        raise ValueError("Choose a private IPv4 interface and its local subnet.")
    if interface.ip in (
        interface.network.network_address,
        interface.network.broadcast_address,
    ):
        raise ValueError("Choose the interface's host address.")
    return interface


def candidate(info, network, now=None):
    """Reduce an untrusted announcement to display-only, bounded fields."""
    if info.type not in SERVICE_TYPES or not info.name.endswith("." + info.type):
        return None
    label = info.name[: -(len(info.type) + 1)]
    # Reject control characters and oversized names rather than rendering them.
    if (
        not label
        or len(label.encode("utf8")) > 63
        or any(not char.isprintable() for char in label)
    ):
        return None
    if not isinstance(info.port, int) or not 1 <= info.port <= 65535:
        return None
    if not info.server or not re.fullmatch(r"[A-Za-z0-9_.-]+\.local\.", info.server):
        return None
    addresses = []
    for value in info.parsed_addresses()[:16]:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if (
            address.version == 4
            and address in network
            and address not in (network.network_address, network.broadcast_address)
        ):
            addresses.append(str(address))
    if not addresses:
        return None
    ttl = max(0, min(120, int(info.host_ttl), int(info.other_ttl)))
    if not ttl:
        return None
    properties = info.properties
    is_sparrow = info.type == SPARROW
    compatible = is_sparrow and properties.get(b"protocol") == str(PROTOCOL).encode()
    transport = properties.get(b"transport", b"") if is_sparrow else b"smb"
    if transport not in (b"https", b"http", b"smb") or (
        is_sparrow and transport == b"smb"
    ):
        return None
    return {
        "name": label,
        "kind": "sparrow_server" if is_sparrow else "storage_device",
        "addresses": sorted(set(addresses)),
        "port": info.port,
        "transport": transport.decode(),
        "compatible": bool(compatible),
        "trusted": False,
        "expires_at": (time.time() if now is None else now) + ttl,
        "next_step": (
            "Verify this server and use an administrator-created pairing code."
            if compatible
            else (
                "This Sparrow version is incompatible; use manual setup."
                if is_sparrow
                else "Select and authenticate a share separately; this device cannot pair as a Sparrow node."
            )
        ),
    }


def dependency():
    try:
        import zeroconf
    except ImportError as exc:
        raise ValueError(
            "Nearby discovery is optional. Install requirements-discovery.txt in a development environment, or enter the server address manually."
        ) from exc
    return zeroconf


def browse(interface, *, seconds=4, module=None):
    scope = interface_scope(interface)
    if not 1 <= seconds <= 10:
        raise ValueError("Discovery must last between one and ten seconds.")
    z = module or dependency()
    changes = queue.Queue(maxsize=128)
    found, seen = {}, set()

    def changed(zeroconf, service_type, name, state_change):
        if service_type not in SERVICE_TYPES or len(name) > 255:
            return
        try:
            changes.put_nowait((service_type, name, state_change))
        except queue.Full:
            pass

    client = z.Zeroconf(interfaces=[str(scope.ip)], ip_version=z.IPVersion.V4Only)
    browser = None
    deadline = time.monotonic() + seconds
    try:
        browser = z.ServiceBrowser(client, list(SERVICE_TYPES), handlers=[changed])
        while time.monotonic() < deadline:
            try:
                service_type, name, state = changes.get(
                    timeout=min(0.1, max(0.001, deadline - time.monotonic()))
                )
            except queue.Empty:
                continue
            key = (service_type, name)
            if state == z.ServiceStateChange.Removed:
                found.pop(key, None)
                continue
            if len(seen) >= 64 or key in seen:
                continue
            seen.add(key)
            info = client.get_service_info(
                service_type,
                name,
                timeout=max(1, min(300, int((deadline - time.monotonic()) * 1000))),
            )
            if info:
                value = candidate(info, scope.network)
                if value:
                    found[key] = value
        return sorted(
            (value for value in found.values() if value["expires_at"] > time.time()),
            key=lambda value: (value["kind"], value["name"]),
        )
    finally:
        if browser:
            browser.cancel()
        client.close()


def announcement(z, interface, name, port, transport):
    scope = interface_scope(interface)
    if (
        not name
        or len(name.encode("utf8")) > 63
        or any(not char.isprintable() for char in name)
    ):
        raise ValueError(
            "Use a display name of 1–63 UTF-8 bytes without control characters."
        )
    if not 1 <= port <= 65535 or transport not in ("https", "http"):
        raise ValueError("Choose the actual server port and HTTP or HTTPS transport.")
    # No device IDs, setup codes, account data, folder paths, or file lists.
    host = "sparrow-" + str(scope.ip).replace(".", "-") + ".local."
    return z.ServiceInfo(
        SPARROW,
        f"{name}.{SPARROW}",
        addresses=[scope.ip.packed],
        server=host,
        port=port,
        host_ttl=120,
        other_ttl=120,
        properties={
            b"protocol": str(PROTOCOL).encode(),
            b"transport": transport.encode(),
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("browse", "advertise"))
    parser.add_argument(
        "--interface",
        required=True,
        help="Explicit private IPv4 address/prefix, e.g. 192.168.1.10/24",
    )
    parser.add_argument(
        "--seconds", type=int, default=4, help="Browse duration, 1–10 seconds"
    )
    parser.add_argument("--name", default="Sparrow")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--transport", choices=("https", "http"), default="https")
    args = parser.parse_args()
    try:
        if args.mode == "browse":
            print(
                json.dumps(
                    {
                        "candidates": browse(args.interface, seconds=args.seconds),
                        "manual_entry": True,
                    },
                    indent=2,
                )
            )
            return
        z = dependency()
        scope = interface_scope(args.interface)
        info = announcement(z, args.interface, args.name, args.port, args.transport)
        client = z.Zeroconf(interfaces=[str(scope.ip)], ip_version=z.IPVersion.V4Only)
        registered = False
        try:
            client.register_service(info)
            registered = True
            print(
                "Advertising the named server on the selected interface. Ctrl+C stops it.",
                flush=True,
            )
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if registered:
                client.unregister_service(info)
            client.close()
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
