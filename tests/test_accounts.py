import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.agents.accounts import (
    Accounts,
    Preferences,
    Policy,
    password_hash,
    check_password,
)
from backend.agents.account_api import install_accounts
from backend.storage import Storage


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.accounts = Accounts(self.temp.name)
        self.owner = self.accounts.create_user(
            "owner", "fixture-password-123", "Owner", bootstrap=True
        )

    def tearDown(self):
        self.temp.cleanup()

    def invited(self, role="requester", scope=None):
        token = self.accounts.invite(role, scope)
        return self.accounts.create_user(
            "viewer", "fixture-password-456", "Viewer", invitation=token
        )

    def test_subtitle_checking_defaults_off_and_request_override_is_scoped(self):
        base = self.accounts.resolve(self.owner["id"])
        self.assertTrue(base["values"]["subtitle_auto_prepare"])
        self.assertFalse(base["values"]["verify_subtitles"])
        self.accounts.set_preferences(self.owner["id"], {"verify_subtitles": True})
        request = self.accounts.resolve(self.owner["id"], {"verify_subtitles": False})
        self.assertFalse(request["values"]["verify_subtitles"])
        self.assertEqual(request["sources"]["verify_subtitles"], "request")
        self.assertTrue(
            self.accounts.resolve(self.owner["id"])["values"]["verify_subtitles"]
        )

    def test_inheritance_overrides_reset_and_policy_are_one_contract(self):
        user = self.invited()
        contract = self.accounts.set_preferences(
            user["id"], {"subtitle_languages": ["es"], "preferred_quality": "2160p"}
        )
        self.assertEqual(contract["sources"]["subtitle_languages"], "personal")
        defaults = Preferences(audio_pref="en", subtitle_languages=["fr"]).model_dump()
        self.accounts.set_defaults(defaults, Policy(max_quality="1080p").model_dump())
        updated = self.accounts.resolve(user["id"])
        self.assertEqual(updated["values"]["audio_pref"], "en")
        self.assertEqual(updated["values"]["subtitle_languages"], ["es"])
        self.assertEqual(updated["values"]["preferred_quality"], "1080p")
        self.assertEqual(updated["sources"]["preferred_quality"], "policy")
        reset = self.accounts.set_preferences(user["id"], {"subtitle_languages": None})
        self.assertEqual(reset["values"]["subtitle_languages"], ["fr"])
        self.assertEqual(reset["sources"]["subtitle_languages"], "server")
        # An already-created request contract is a snapshot, not a mutable defaults reference.
        self.assertEqual(contract["values"]["audio_pref"], "original")
        self.assertEqual(
            self.accounts.resolve(self.owner["id"])["values"]["subtitle_languages"],
            ["fr"],
        )

    def test_theme_is_personal_validated_and_outside_the_preference_contract(self):
        user = self.invited()
        self.assertEqual(user["theme"], "")
        before = self.accounts.user(user["id"])["revision"]
        chosen = self.accounts.set_theme(user["id"], "clear")
        self.assertEqual(chosen["theme"], "clear")
        self.assertEqual(chosen["revision"], before)
        self.assertEqual(self.accounts.user(self.owner["id"])["theme"], "")
        with self.assertRaises(ValueError):
            self.accounts.set_theme(user["id"], "neon")
        self.assertEqual(self.accounts.set_theme(user["id"], "")["theme"], "")

    def test_databases_from_before_themes_gain_the_default_theme(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temp:
            with sqlite3.connect(Path(temp) / "sparrow.db") as db:
                db.execute(
                    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE "
                    "COLLATE NOCASE, password TEXT NOT NULL, name TEXT NOT NULL, role TEXT "
                    "NOT NULL, library_scope TEXT, disabled INTEGER NOT NULL DEFAULT 0, "
                    "preferences TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL "
                    "DEFAULT 1, welcomed INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL)"
                )
                db.execute(
                    "INSERT INTO users (id, username, password, name, role, created) "
                    "VALUES ('u1', 'old', 'x', 'Old', 'admin', 0)"
                )
            upgraded = Accounts(temp)
            self.assertEqual(upgraded.user("u1")["theme"], "")
            self.assertEqual(upgraded.set_theme("u1", "cinema")["theme"], "cinema")

    def test_invitations_are_single_use_and_do_not_grant_admin(self):
        token = self.accounts.invite()
        user = self.accounts.create_user(
            "viewer", "fixture-password-456", "Viewer", invitation=token
        )
        self.assertEqual(user["role"], "viewer")
        with self.assertRaisesRegex(ValueError, "already been used"):
            self.accounts.create_user(
                "another", "fixture-password-456", "Another", invitation=token
            )
        with self.assertRaises(ValueError):
            self.accounts.create_user(
                "another", "fixture-password-456", "Another", bootstrap=True
            )

    def test_scope_and_session_revocation_survive_restart(self):
        user = self.invited(scope=["family"])
        token = self.accounts.new_session(user["id"])
        restarted = Accounts(self.temp.name)
        self.assertEqual(restarted.from_session(token)["id"], user["id"])
        self.assertTrue(restarted.can_access(user, "family"))
        self.assertFalse(restarted.can_access(user, "private"))
        restarted.revoke(token)
        self.assertIsNone(self.accounts.from_session(token))
        self.assertNotIn("password", user)
        with self.accounts.connect() as db:
            hashes = str(
                [tuple(r) for r in db.execute("SELECT token_hash FROM login_sessions")]
            )
            self.assertNotIn(token, hashes)

    def test_password_checks_and_unknown_preferences_fail(self):
        self.assertEqual(
            self.accounts.authenticate("OWNER", "fixture-password-123", "local")["id"],
            self.owner["id"],
        )
        with self.assertRaises(ValueError):
            self.accounts.authenticate("owner", "wrong-password-123", "local")
        with self.assertRaises(ValueError):
            self.accounts.set_preferences(self.owner["id"], {"admin": True})

    def test_eight_character_passwords_without_complexity_rules(self):
        for password in ("movienow", "12345678", "two words", "a" * 1024):
            with self.subTest(length=len(password)):
                hashed = password_hash(password)
                self.assertTrue(check_password(password, hashed))
                self.assertFalse(check_password(password + "x", hashed))
        for password in ("", "a" * 7, "a" * 1025):
            with self.assertRaisesRegex(ValueError, "between 8 and"):
                password_hash(password)

    def test_successful_household_logins_do_not_exhaust_shared_ip_failure_limit(self):
        for _ in range(15):
            self.accounts.authenticate("owner", "fixture-password-123", "household")
        for _ in range(11):
            with self.assertRaisesRegex(ValueError, "incorrect"):
                self.accounts.authenticate("owner", "wrong-password-123", "household")
        self.accounts.authenticate("owner", "fixture-password-123", "household")
        with self.assertRaisesRegex(ValueError, "incorrect"):
            self.accounts.authenticate("owner", "wrong-password-123", "household")
        with self.assertRaisesRegex(ValueError, "Too many attempts"):
            self.accounts.authenticate("owner", "fixture-password-123", "household")


class AccountAPITests(unittest.TestCase):
    def test_household_artwork_includes_backdrops_within_library_scope(self):
        from unittest.mock import patch
        from backend.agents.account_api import COOKIE
        from backend.models import LibraryItem, MediaType

        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(temp)
            app = FastAPI()
            accounts = install_accounts(app, storage, lambda: None)
            accounts.create_user("owner", "movienow", "Owner", bootstrap=True)
            user = accounts.create_user(
                "viewer",
                "movienow",
                "Viewer",
                invitation=accounts.invite("viewer", ["family"]),
            )
            items = [
                LibraryItem(
                    id=library,
                    title=library,
                    media_type=MediaType.MOVIE,
                    path=f"/{library}/movie.mp4",
                    poster_path=f"/art/{library}-poster.svg",
                    backdrop_path=f"/art/{library}-backdrop.webp",
                    metadata={"library_id": library},
                )
                for library in ("family", "private")
            ]

            @app.get("/art/{name}")
            def artwork(name: str):
                return {"artwork": name}

            with patch.object(storage, "get_library", return_value=items):
                anonymous = TestClient(app)
                self.assertEqual(
                    anonymous.get("/art/family-backdrop.webp").status_code, 401
                )
                viewer = TestClient(app)
                viewer.cookies.set(COOKIE, accounts.new_session(user["id"]))
                for name, expected in (
                    ("family-poster.svg", 200),
                    ("family-backdrop.webp", 200),
                    ("private-poster.svg", 404),
                    ("private-backdrop.webp", 404),
                    ("unreferenced.webp", 404),
                ):
                    with self.subTest(artwork=name):
                        self.assertEqual(
                            viewer.get(f"/art/{name}").status_code, expected
                        )

    def test_bootstrap_login_csrf_and_legacy_routes_are_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(temp)
            app = FastAPI()
            accounts = install_accounts(app, storage, lambda: None)

            @app.get("/api/config")
            def legacy():
                return {"server_secret": "must stay private"}

            client = TestClient(app)
            self.assertEqual(client.get("/api/config").status_code, 401)
            data = {
                "username": "owner",
                "password": "movienow",
                "name": "Owner",
                "setup_code": (Path(temp) / "owner-setup-code").read_text(),
            }
            self.assertEqual(
                client.post("/api/v1/auth/bootstrap", json=data).status_code, 403
            )
            headers = {"X-Sparrow-Request": "1"}
            response = client.post("/api/v1/auth/bootstrap", json=data, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("HttpOnly", response.headers["set-cookie"])
            self.assertEqual(client.get("/api/config").status_code, 200)
            self.assertEqual(
                client.post(
                    "/api/v1/auth/logout",
                    headers={**headers, "Origin": "https://evil.example"},
                ).status_code,
                403,
            )
            invitation = accounts.invite("viewer")
            viewer = TestClient(app)
            joined = viewer.post(
                "/api/v1/auth/join",
                json={**data, "username": "viewer", "invitation": invitation},
                headers=headers,
            )
            self.assertEqual(joined.status_code, 200, joined.text)
            self.assertEqual(viewer.get("/api/config").status_code, 403)
            self.assertEqual(viewer.get("/api/v1/admin/users").status_code, 403)
            self.assertEqual(viewer.get("/api/v1/preferences").status_code, 200)
            themed = viewer.put(
                "/api/v1/appearance", json={"theme": "saturday"}, headers=headers
            )
            self.assertEqual(themed.json()["user"]["theme"], "saturday")
            self.assertEqual(
                viewer.get("/api/v1/auth/status").json()["user"]["theme"], "saturday"
            )
            self.assertEqual(
                viewer.put(
                    "/api/v1/appearance", json={"theme": "neon"}, headers=headers
                ).status_code,
                422,
            )
            sessions = viewer.get("/api/v1/sessions").json()
            viewer.delete("/api/v1/sessions/" + sessions[0]["id"], headers=headers)
            self.assertEqual(viewer.get("/api/v1/preferences").status_code, 401)
            # Creation, invitations and password changes share the same length rule.
            original_session = TestClient(app)
            self.assertEqual(
                original_session.post(
                    "/api/v1/auth/login",
                    json={"username": "owner", "password": "movienow"},
                    headers=headers,
                ).status_code,
                200,
            )
            for password, expected in (("seven77", 422), ("newmovie", 200)):
                changed = client.post(
                    "/api/v1/auth/password",
                    headers=headers,
                    json={"current_password": "movienow", "new_password": password},
                )
                self.assertEqual(changed.status_code, expected, changed.text)
            self.assertEqual(
                original_session.get("/api/v1/preferences").status_code, 401
            )
            self.assertEqual(client.get("/api/v1/preferences").status_code, 200)
            self.assertEqual(
                original_session.post(
                    "/api/v1/auth/login",
                    json={"username": "owner", "password": "newmovie"},
                    headers=headers,
                ).status_code,
                200,
            )
