from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import configuration
from backend.configuration import (
    apply_config_update,
    effective_openai_key,
    prepare_media_folder,
    public_config,
    suggested_media_folders,
)
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

    def test_openai_key_is_saved_like_the_others_and_never_returned(self) -> None:
        config = apply_config_update(SparrowConfig(), {"openai_api_key": " sk-openai "})
        self.assertEqual(config.openai_api_key, "sk-openai")
        self.assertEqual(SparrowConfig.from_dict(config.to_dict()).openai_api_key, "sk-openai")
        data = public_config(config)
        self.assertEqual(data["openai_api_key"], "")
        self.assertTrue(data["openai_api_key_configured"])
        self.assertEqual(apply_config_update(config, {"openai_api_key": ""}).openai_api_key, "sk-openai")
        self.assertEqual(apply_config_update(config, {"clear_openai_api_key": True}).openai_api_key, "")
        # Settings saved before this key existed still load.
        old = SparrowConfig().to_dict()
        old.pop("openai_api_key")
        self.assertEqual(SparrowConfig.from_dict(old).openai_api_key, "")

    def test_saved_openai_key_wins_over_the_environment(self) -> None:
        saved = SparrowConfig(openai_api_key="saved")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env"}):
            self.assertEqual(effective_openai_key(SparrowConfig()), "env")
            self.assertEqual(effective_openai_key(saved), "saved")
            with patch.object(configuration, "_saved_config", lambda: saved):
                self.assertEqual(effective_openai_key(), "saved")
            with patch.object(configuration, "_saved_config", None):
                self.assertEqual(effective_openai_key(), "env")
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(public_config(SparrowConfig())["openai_api_key_configured"], False)

    def test_chosen_folder_is_created_only_beneath_an_existing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            configuration, "suggested_base", lambda: Path(tmp) / "Sparrow"
        ):
            prepare_media_folder(str(Path(tmp) / "Library"), writable=True)
            self.assertTrue((Path(tmp) / "Library").is_dir())
            with self.assertRaisesRegex(ValueError, "isn’t on this server"):
                prepare_media_folder(str(Path(tmp) / "typo" / "Library"), writable=False)
            self.assertFalse((Path(tmp) / "typo").exists())
            # The suggested home may be new as well.
            prepare_media_folder(str(Path(tmp) / "Sparrow" / "Incoming"), writable=True)
            self.assertTrue((Path(tmp) / "Sparrow" / "Incoming").is_dir())
            (Path(tmp) / "file").write_text("")
            with self.assertRaisesRegex(ValueError, "is a file"):
                prepare_media_folder(str(Path(tmp) / "file"), writable=False)

    def test_incoming_folder_must_be_writable(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can write anywhere")
        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "Locked"
            locked.mkdir()
            locked.chmod(0o555)
            try:
                prepare_media_folder(str(locked), writable=False)
                with self.assertRaisesRegex(ValueError, "can’t save into"):
                    prepare_media_folder(str(locked), writable=True)
            finally:
                locked.chmod(0o755)

    def test_folder_suggestions_reuse_existing_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            configuration, "suggested_base", lambda: Path(tmp)
        ):
            self.assertEqual(
                suggested_media_folders()["incoming"], str(Path(tmp) / "Incoming")
            )
            (Path(tmp) / "Temp").mkdir()
            (Path(tmp) / "Library").mkdir()
            suggested = suggested_media_folders()
            self.assertEqual(suggested["library"], str(Path(tmp) / "Library"))
            self.assertEqual(suggested["incoming"], str(Path(tmp) / "Temp"))

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
