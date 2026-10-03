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
    mounted_path,
    public_config,
    repair_media_folders,
    suggested_media_folders,
    usable_media_folder,
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

    def test_chosen_folders_are_made_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            configuration, "mounted_path", lambda value: None
        ):
            made = usable_media_folder(str(Path(tmp) / "Media" / "Library"), writable=True)
            self.assertEqual(made, str(Path(tmp) / "Media" / "Library"))
            self.assertTrue(Path(made).is_dir())
            (Path(tmp) / "file").write_text("")
            with self.assertRaisesRegex(ValueError, "is a file"):
                usable_media_folder(str(Path(tmp) / "file"), writable=False)

    def test_folder_that_cannot_be_made_says_so(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can write anywhere")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            configuration, "mounted_path", lambda value: None
        ):
            locked = Path(tmp) / "Locked"
            locked.mkdir()
            locked.chmod(0o555)
            try:
                with self.assertRaisesRegex(ValueError, "can’t create"):
                    usable_media_folder(str(locked / "Library"), writable=False)
                usable_media_folder(str(locked), writable=False)
                with self.assertRaisesRegex(ValueError, "can’t save into"):
                    usable_media_folder(str(locked), writable=True)
            finally:
                locked.chmod(0o755)

    def test_host_paths_map_to_their_docker_mount(self) -> None:
        mounts = configuration.bind_mounts(
            "1 2 0:1 /home/oem/Media /media rw - ext4 /dev/sda1 rw\n"
            "3 2 0:1 /var/lib/docker/volumes/x/_data /data rw - ext4 /dev/sda1 rw\n"
            "4 2 0:2 /chris/My\\040Films /films rw - ext4 /dev/sdb1 rw\n"
            "5 2 0:3 / / rw - overlay overlay rw\n"
        )
        self.assertEqual(mounted_path("/home/oem/Media/Library", mounts), Path("/media/Library"))
        self.assertEqual(mounted_path("/home/oem/Media", mounts), Path("/media"))
        # /home on its own disk: the mount root starts below it.
        self.assertEqual(mounted_path("/home/chris/My Films/Kids", mounts), Path("/films/Kids"))
        self.assertIsNone(mounted_path("/home/oem/Other/Library", mounts))
        self.assertIsNone(mounted_path("/media/Library", mounts))

    def test_saving_a_missing_folder_again_makes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            configuration, "mounted_path", lambda value: None
        ):
            gone = str(Path(tmp) / "Temp")
            before = SparrowConfig(staging_dir=gone)
            after = SparrowConfig(staging_dir=gone)
            configuration.settle_media_folders(after, before)
            self.assertTrue(Path(gone).is_dir())

    def test_docker_only_keeps_folders_on_a_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            configuration, "in_container", lambda: True
        ), patch.object(configuration, "mounted_path", lambda value: None), patch.object(
            configuration, "bind_mounts", lambda: [(Path("/host/media"), Path(tmp) / "media")]
        ):
            made = usable_media_folder(str(Path(tmp) / "media" / "Library"), writable=True)
            self.assertTrue(Path(made).is_dir())
            with self.assertRaisesRegex(ValueError, "choose a folder under /media"):
                usable_media_folder(str(Path(tmp) / "scratch" / "Library"), writable=True)
            self.assertFalse((Path(tmp) / "scratch").exists())

    def test_saved_host_paths_are_repaired_but_nothing_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mounted = Path(tmp) / "media"
            (mounted / "Library").mkdir(parents=True)
            seen = {"/host/Media/Library": mounted / "Library", "/host/Media/Temp": mounted / "Temp"}
            config = SparrowConfig(library_dir="/host/Media/Library", staging_dir="/host/Media/Temp")
            with patch.object(configuration, "mounted_path", lambda value: seen.get(value)):
                self.assertTrue(repair_media_folders(config))
            self.assertEqual(config.library_dir, str(mounted / "Library"))
            # Temp isn't there yet: it may be an unplugged drive, so it is left alone.
            self.assertEqual(config.staging_dir, "/host/Media/Temp")
            self.assertFalse((mounted / "Temp").exists())

    def test_choosing_a_host_path_uses_and_creates_its_mounted_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mounted = Path(tmp) / "media"
            mounted.mkdir()
            with patch.object(
                configuration, "mounted_path", lambda value: mounted / Path(value).name
            ):
                self.assertEqual(
                    usable_media_folder("/no/such/host/Temp", writable=True),
                    str(mounted / "Temp"),
                )
            self.assertTrue((mounted / "Temp").is_dir())

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
