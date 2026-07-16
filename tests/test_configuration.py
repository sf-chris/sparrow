from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.configuration import apply_config_update, public_config
from backend.models import SparrowConfig, TorrentClientConfig, TorrentClientType
from backend.runtime_settings import validate_bind, websocket_origin_allowed


class ConfigurationTests(unittest.TestCase):
    def test_public_config_never_returns_credentials(self) -> None:
        config = SparrowConfig(
            tmdb_api_key="tmdb-secret",
            anthropic_api_key="anthropic-secret",
            torrent_client=TorrentClientConfig(
                type=TorrentClientType.TRANSMISSION,
                username="sparrow",
                password="client-secret",
                url="http://user:secret@example.test/rpc",
            ),
        )

        data = public_config(config)

        self.assertEqual(data["tmdb_api_key"], "")
        self.assertEqual(data["anthropic_api_key"], "")
        self.assertTrue(data["tmdb_api_key_configured"])
        self.assertTrue(data["anthropic_api_key_configured"])
        self.assertEqual(data["torrent_client"]["password"], "")
        self.assertEqual(data["torrent_client"]["url"], "")
        self.assertTrue(data["torrent_client"]["password_configured"])
        self.assertTrue(data["torrent_client"]["url_configured"])

    def test_blank_browser_secrets_preserve_stored_values(self) -> None:
        config = SparrowConfig(
            tmdb_api_key="tmdb-secret",
            anthropic_api_key="anthropic-secret",
            torrent_client=TorrentClientConfig(password="client-secret", url="http://secret"),
        )
        updated = apply_config_update(config, {
            "tmdb_api_key": "",
            "anthropic_api_key": "",
            "torrent_client": {"type": "none", "password": "", "url": ""},
        })

        self.assertEqual(updated.tmdb_api_key, "tmdb-secret")
        self.assertEqual(updated.anthropic_api_key, "anthropic-secret")
        self.assertEqual(updated.torrent_client.password, "client-secret")
        self.assertEqual(updated.torrent_client.url, "http://secret")

    def test_paths_are_absolute_and_overlapping_roots_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            config = SparrowConfig()
            updated = apply_config_update(config, {
                "staging_dir": str(parent / "Temp"),
                "library_dir": str(parent / "Library"),
            })
            self.assertTrue(Path(updated.staging_dir).is_absolute())

            with self.assertRaisesRegex(ValueError, "separate sibling roots"):
                apply_config_update(SparrowConfig(), {
                    "staging_dir": str(parent / "Library" / "Temp"),
                    "library_dir": str(parent / "Library"),
                })

    def test_non_loopback_bind_requires_explicit_lan_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            validate_bind("127.0.0.1")
            with self.assertRaisesRegex(RuntimeError, "SPARROW_ALLOW_LAN"):
                validate_bind("0.0.0.0")
        with patch.dict(os.environ, {"SPARROW_ALLOW_LAN": "1"}, clear=True):
            validate_bind("0.0.0.0")

    def test_websocket_accepts_same_origin_and_rejects_unknown_origin(self) -> None:
        self.assertTrue(websocket_origin_allowed("http://192.168.1.5:8888", "192.168.1.5:8888"))
        with patch.dict(os.environ, {"SPARROW_ALLOWED_ORIGINS": "http://localhost:3000"}, clear=True):
            self.assertTrue(websocket_origin_allowed("http://localhost:3000", "localhost:8888"))
            self.assertFalse(websocket_origin_allowed("https://example.test", "localhost:8888"))
