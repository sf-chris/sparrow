import ipaddress
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from backend.agents.nearby import (
    SPARROW,
    SMB,
    announcement,
    browse,
    candidate,
    interface_scope,
)
from backend.agents.node_executor import PROTOCOL


class NearbyTests(unittest.TestCase):
    def info(self, **changes):
        fields = dict(
            type=SPARROW,
            name="Living room." + SPARROW,
            server="sparrow.local.",
            port=8888,
            host_ttl=120,
            other_ttl=120,
            properties={
                b"protocol": str(PROTOCOL).encode(),
                b"transport": b"https",
                b"secret": b"never-copy",
            },
            parsed_addresses=lambda: ["192.168.1.20"],
        )
        return SimpleNamespace(**(fields | changes))

    def test_candidates_cannot_grant_trust_or_expose_txt_secrets(self):
        network = ipaddress.ip_network("192.168.1.0/24")
        value = candidate(self.info(), network, now=100)
        self.assertFalse(value["trusted"])
        self.assertTrue(value["compatible"])
        self.assertEqual(value["expires_at"], 220)
        self.assertNotIn("never-copy", str(value))
        storage = candidate(
            self.info(type=SMB, name="My NAS." + SMB, port=445), network
        )
        self.assertEqual(storage["kind"], "storage_device")
        self.assertFalse(storage["compatible"])
        self.assertIn("share separately", storage["next_step"])
        incompatible = candidate(
            self.info(properties={b"protocol": b"999", b"transport": b"http"}), network
        )
        self.assertFalse(incompatible["compatible"])

    def test_rejects_off_subnet_public_malformed_expired_and_wrong_service_records(
        self,
    ):
        network = ipaddress.ip_network("192.168.1.0/24")
        for changes in [
            dict(parsed_addresses=lambda: ["8.8.8.8", "10.0.0.1", "::1", "bad"]),
            dict(name="Bad\nname." + SPARROW),
            dict(name="x" * 64 + "." + SPARROW),
            dict(host_ttl=0),
            dict(port=0),
            dict(server="example.com."),
            dict(type="_http._tcp.local."),
            dict(properties={b"transport": b"javascript"}),
        ]:
            self.assertIsNone(candidate(self.info(**changes), network), changes)
        for interface in [
            "0.0.0.0/0",
            "8.8.8.8/24",
            "192.168.1.1/0",
            "192.168.1.0/24",
            "::1/128",
            "192.168.1.1",
        ]:
            with self.assertRaises(ValueError):
                interface_scope(interface)

    def test_browse_is_bounded_service_only_and_closes_resources(self):
        module = SimpleNamespace(
            IPVersion=SimpleNamespace(V4Only="v4"),
            ServiceStateChange=SimpleNamespace(Removed="removed"),
        )
        client = Mock()
        client.get_service_info.return_value = self.info()
        module.Zeroconf = Mock(return_value=client)
        browser = Mock()

        def make_browser(zc, types, handlers):
            self.assertEqual(types, [SPARROW, SMB])
            for _ in range(500):
                handlers[0](zc, SPARROW, "Living room." + SPARROW, "added")
            return browser

        module.ServiceBrowser = make_browser
        values = browse("192.168.1.10/24", seconds=1, module=module)
        self.assertEqual(len(values), 1)
        self.assertEqual(client.get_service_info.call_count, 1)
        module.Zeroconf.assert_called_once_with(
            interfaces=["192.168.1.10"], ip_version="v4"
        )
        browser.cancel.assert_called_once()
        client.close.assert_called_once()
        client.get_service_info.side_effect = OSError("Multicast unavailable")
        with self.assertRaises(OSError):
            browse("192.168.1.10/24", seconds=1, module=module)
        self.assertEqual(client.close.call_count, 2)
        with patch(
            "backend.agents.nearby.dependency",
            side_effect=ValueError("Enter address manually"),
        ):
            with self.assertRaisesRegex(ValueError, "manually"):
                browse("192.168.1.10/24")

    def test_announcements_only_contain_version_and_transport(self):
        module = SimpleNamespace(ServiceInfo=Mock(return_value="announcement"))
        self.assertEqual(
            announcement(module, "192.168.1.10/24", "Movie room", 8888, "https"),
            "announcement",
        )
        fields = module.ServiceInfo.call_args.kwargs
        self.assertEqual(set(fields["properties"]), {b"protocol", b"transport"})
        self.assertEqual(
            fields["addresses"], [ipaddress.ip_address("192.168.1.10").packed]
        )
