import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI

from backend.agents.account_api import install_accounts
from backend.agents.setup_info import log_setup, main, read_setup_code, setup_link
from backend.storage import Storage


class SetupInformationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Storage(self.temp.name)
        self.accounts = install_accounts(FastAPI(), self.storage, lambda: None)
        self.code = (Path(self.temp.name) / "owner-setup-code").read_text()

    def test_startup_handoff_uses_existing_code_and_stops_after_administrator_exists(
        self,
    ):
        with self.assertLogs("sparrow.setup", level="INFO") as logs:
            log_setup(self.temp.name)
        self.assertIn("Setup code: " + self.code, "\n".join(logs.output))
        self.assertEqual(read_setup_code(self.temp.name), self.code)
        # Even a stale code file must not produce a handoff for an established server.
        self.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )
        self.assertIsNone(read_setup_code(self.temp.name))
        with self.assertNoLogs("sparrow.setup"):
            log_setup(self.temp.name)

    def test_cli_returns_browser_link_without_creating_an_account(self):
        output = io.StringIO()
        with (
            patch(
                "sys.argv",
                [
                    "setup_info",
                    "--data-dir",
                    self.temp.name,
                    "--url",
                    "http://localhost:18888",
                ],
            ),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)
        self.assertIn(
            "http://localhost:18888/#setup_code=" + self.code, output.getvalue()
        )
        self.assertIn("Setup code: " + self.code, output.getvalue())
        self.assertFalse(self.accounts.has_users())
        self.assertEqual(read_setup_code(self.temp.name), self.code)

    def test_no_installation_is_created_for_a_wrong_data_directory(self):
        missing = Path(self.temp.name) / "does-not-exist"
        with self.assertRaisesRegex(ValueError, "Start Sparrow first"):
            read_setup_code(missing)
        self.assertFalse(missing.exists())

    def test_setup_link_uses_fragment_and_requires_a_browser_origin(self):
        self.assertEqual(
            setup_link("https://watch.example.com/", "fixture-code"),
            "https://watch.example.com/#setup_code=fixture-code",
        )
        for url in [
            "file:///tmp/setup",
            "http://0.0.0.0:8888",
            "https://owner:secret@example.com",
            "http://localhost:8888/path",
            "http://localhost:8888?code=secret",
            "http://localhost:99999",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                setup_link(url, self.code)
