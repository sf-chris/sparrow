"""Local identity and a single versioned preference contract for UI and agents."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ..models import quality_rank


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preferred_quality: Literal["480p", "720p", "1080p", "2160p"] = "1080p"
    min_quality: Literal["any", "480p", "720p", "1080p", "2160p"] = "720p"
    audio_pref: str = Field(default="original", min_length=2, max_length=32)
    subtitle_languages: list[str] = Field(default_factory=lambda: ["en"], max_length=8)
    subtitle_mode: Literal["auto", "always", "off"] = "auto"
    subtitle_kind: Literal["full", "forced", "sdh"] = "full"
    require_subtitles: bool = False
    max_file_size_gb: float = Field(default=0, ge=0, le=1000)
    monitoring: Literal["exact", "keep_current"] = "exact"
    urgency: Literal["tonight", "soon", "whenever"] = "soon"
    prefer_smaller_files: bool = False

    @field_validator("audio_pref")
    @classmethod
    def audio_language(cls, value):
        from .media_state import language_code

        value = language_code(value)
        if value not in ("original", "any") and not re.fullmatch("[a-z]{2,3}", value):
            raise ValueError("Choose original audio, any audio, or a language code.")
        return value

    @field_validator("subtitle_languages")
    @classmethod
    def languages(cls, values):
        from .media_state import language_code

        result = list(dict.fromkeys(language_code(v) for v in values))
        if any(not re.fullmatch("[a-z]{2,3}", v) for v in result):
            raise ValueError(
                "Subtitle languages must use two- or three-letter language codes."
            )
        return result

    @model_validator(mode="after")
    def compatible(self):
        if quality_rank(self.min_quality) > quality_rank(self.preferred_quality):
            raise ValueError("Minimum quality cannot exceed preferred quality.")
        if self.require_subtitles and (
            self.subtitle_mode == "off" or not self.subtitle_languages
        ):
            raise ValueError(
                "Required subtitles need an enabled mode and at least one language."
            )
        return self


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_quality: Literal["480p", "720p", "1080p", "2160p"] = "2160p"
    max_file_size_gb: float = Field(default=0, ge=0, le=1000)
    max_agent_calls: int = Field(default=40, ge=1, le=200)
    max_agent_dollars: float = Field(default=3, gt=0, le=100)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str, salt: str | None = None) -> str:
    if not 8 <= len(password) <= 1024:
        raise ValueError("Use a password between 8 and 1,024 characters.")
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=32768,
        r=8,
        p=1,
        maxmem=64 * 1024 * 1024,
    ).hex()
    return f"scrypt:{salt}:{hashed}"


def check_password(password: str, encoded: str) -> bool:
    try:
        _, salt, _ = encoded.split(":")
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


# Interface themes each person can choose. The empty value follows Sparrow's
# official theme, so a change to the default reaches everyone who never chose.
THEMES = ("", "cinema", "clear", "saturday")


class Accounts:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "sparrow.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL,
                    library_scope TEXT, disabled INTEGER NOT NULL DEFAULT 0,
                    preferences TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 1,
                    welcomed INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_sessions (
                    token_hash TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL,
                    expires REAL NOT NULL, created REAL NOT NULL, label TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS invitations (
                    token_hash TEXT PRIMARY KEY, role TEXT NOT NULL, library_scope TEXT,
                    expires REAL NOT NULL, used REAL);
                CREATE TABLE IF NOT EXISTS server_preferences (
                    id INTEGER PRIMARY KEY CHECK(id=1), defaults TEXT NOT NULL,
                    policy TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, window REAL NOT NULL);
            """)
            db.execute(
                "INSERT OR IGNORE INTO server_preferences VALUES (1, ?, ?, 1)",
                (Preferences().model_dump_json(), Policy().model_dump_json()),
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "theme" not in columns:
                db.execute(
                    "ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT ''"
                )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def has_users(self):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM users LIMIT 1").fetchone())

    @staticmethod
    def public(row):
        if row is None:
            return None
        user = dict(row)
        user.pop("password", None)
        user["preferences"] = json.loads(user["preferences"])
        user["library_scope"] = (
            json.loads(user["library_scope"]) if user["library_scope"] else None
        )
        user["disabled"] = bool(user["disabled"])
        user["welcomed"] = bool(user["welcomed"])
        return user

    def user(self, user_id):
        with self.connect() as db:
            return self.public(
                db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            )

    def users(self):
        with self.connect() as db:
            return [
                self.public(r)
                for r in db.execute("SELECT * FROM users ORDER BY created")
            ]

    def create_user(
        self, username, password, name, *, invitation=None, bootstrap=False
    ):
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", username):
            raise ValueError(
                "Use 3–64 letters, numbers, dots, underscores or hyphens for your username."
            )
        name = name.strip()
        if not 1 <= len(name) <= 100:
            raise ValueError("Enter a name of up to 100 characters.")
        encoded = password_hash(password)
        identity = secrets.token_hex(16)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if bootstrap:
                if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                    raise ValueError("An owner already exists. Sign in instead.")
                role, scope = "admin", None
            else:
                inv = db.execute(
                    "SELECT * FROM invitations WHERE token_hash=? AND used IS NULL AND expires>?",
                    (digest(invitation or ""), time.time()),
                ).fetchone()
                if not inv:
                    raise ValueError(
                        "This invitation has expired or has already been used."
                    )
                role, scope = inv["role"], inv["library_scope"]
                db.execute(
                    "UPDATE invitations SET used=? WHERE token_hash=?",
                    (time.time(), inv["token_hash"]),
                )
            try:
                db.execute(
                    "INSERT INTO users(id, username, password, name, role, library_scope, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (identity, username, encoded, name, role, scope, time.time()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("That username is already in use.") from exc
        return self.user(identity)

    def authenticate(self, username, password, address):
        now = time.time()
        keys = [digest("ip:" + address), digest("user:" + username.lower())]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for key in keys:
                row = db.execute(
                    "SELECT * FROM login_attempts WHERE key=?", (key,)
                ).fetchone()
                if row and row["window"] > now - 900 and row["attempts"] >= 12:
                    raise ValueError("Too many attempts. Try again in 15 minutes.")
            for key in keys:
                db.execute(
                    "INSERT INTO login_attempts VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET "
                    "attempts=CASE WHEN window<? THEN 1 ELSE attempts+1 END, "
                    "window=CASE WHEN window<? THEN excluded.window ELSE window END",
                    (key, now, now - 900, now - 900),
                )
            row = db.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone()
        # Perform the same expensive hash even for an unknown user.
        encoded = row["password"] if row else "scrypt:" + "0" * 32 + ":" + "0" * 128
        valid = check_password(password, encoded)
        if not valid or not row or row["disabled"]:
            raise ValueError("Username or password is incorrect.")
        with self.connect() as db:
            db.execute("DELETE FROM login_attempts WHERE key=?", (keys[1],))
            # Successful household logins must not consume the shared IP's
            # failed-attempt allowance. Preserve failures from other attempts.
            db.execute(
                "UPDATE login_attempts SET attempts=MAX(0, attempts-1) WHERE key=?",
                (keys[0],),
            )
        return self.public(row)

    def new_session(self, user_id, label="Browser"):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.connect() as db:
            db.execute("DELETE FROM login_sessions WHERE expires<?", (now,))
            db.execute(
                "INSERT INTO login_sessions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    digest(token),
                    secrets.token_hex(12),
                    user_id,
                    now + 30 * 86400,
                    now,
                    label[:120],
                ),
            )
        return token

    def from_session(self, token):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT users.* FROM users JOIN login_sessions ON users.id=login_sessions.user_id "
                "WHERE token_hash=? AND expires>? AND disabled=0",
                (digest(token), time.time()),
            ).fetchone()
        return self.public(row)

    def revoke(self, token):
        with self.connect() as db:
            db.execute(
                "DELETE FROM login_sessions WHERE token_hash=?", (digest(token),)
            )

    def invite(self, role="viewer", library_scope=None):
        if role not in ("viewer", "requester", "admin"):
            raise ValueError("Choose a valid role.")
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute(
                "INSERT INTO invitations VALUES (?, ?, ?, ?, NULL)",
                (
                    digest(token),
                    role,
                    json.dumps(library_scope) if library_scope is not None else None,
                    time.time() + 7 * 86400,
                ),
            )
        return token

    def server_settings(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM server_preferences WHERE id=1").fetchone()
        return {
            "defaults": json.loads(row["defaults"]),
            "policy": json.loads(row["policy"]),
            "revision": row["revision"],
        }

    def set_defaults(self, defaults, policy=None):
        defaults = Preferences.model_validate(defaults)
        policy = Policy.model_validate(policy or self.server_settings()["policy"])
        with self.connect() as db:
            db.execute(
                "UPDATE server_preferences SET defaults=?, policy=?, revision=revision+1 WHERE id=1",
                (defaults.model_dump_json(), policy.model_dump_json()),
            )
        return self.server_settings()

    def set_theme(self, user_id, theme):
        """Appearance only: it never changes the preference contract agents use."""
        if theme not in THEMES:
            raise ValueError("Choose one of the listed themes.")
        with self.connect() as db:
            changed = db.execute(
                "UPDATE users SET theme=? WHERE id=?", (theme, user_id)
            ).rowcount
        if not changed:
            raise ValueError("Account no longer exists.")
        return self.user(user_id)

    def set_preferences(self, user_id, patch, welcomed=True):
        user = self.user(user_id)
        if not user:
            raise ValueError("Account no longer exists.")
        overrides = {**user["preferences"], **patch}
        overrides = {
            key: value for key, value in overrides.items() if value is not None
        }
        self.resolve(user_id, personal_override=overrides)
        with self.connect() as db:
            db.execute(
                "UPDATE users SET preferences=?, revision=revision+1, welcomed=? WHERE id=?",
                (json.dumps(overrides), int(welcomed), user_id),
            )
        return self.resolve(user_id)

    def resolve(self, user_id, request=None, *, personal_override=None):
        user = self.user(user_id)
        if not user or user["disabled"]:
            raise ValueError("Account is unavailable.")
        settings = self.server_settings()
        values, sources = dict(settings["defaults"]), {
            k: "server" for k in settings["defaults"]
        }
        from pydantic import TypeAdapter
        from typing import Annotated

        personal = (
            user["preferences"] if personal_override is None else personal_override
        )
        for source, layer in [("personal", personal), ("request", request or {})]:
            for key, value in layer.items():
                if value is not None:
                    field = Preferences.model_fields.get(key)
                    if not field:
                        raise ValueError(f"Unknown preference: {key}")
                    value = TypeAdapter(
                        Annotated[field.annotation, *field.metadata]
                        if field.metadata
                        else field.annotation
                    ).validate_python(value)
                    values[key], sources[key] = value, source
        # Apply current policy before cross-field validation: changing a server
        # default must not make a previously valid personal minimum unusable.
        policy = settings["policy"]
        if quality_rank(values["preferred_quality"]) > quality_rank(
            policy["max_quality"]
        ):
            values["preferred_quality"], sources["preferred_quality"] = (
                policy["max_quality"],
                "policy",
            )
        if quality_rank(values["min_quality"]) > quality_rank(
            values["preferred_quality"]
        ):
            values["min_quality"], sources["min_quality"] = (
                values["preferred_quality"],
                "policy",
            )
        cap, asked = policy["max_file_size_gb"], values["max_file_size_gb"]
        if cap and (not asked or asked > cap):
            values["max_file_size_gb"], sources["max_file_size_gb"] = cap, "policy"
        values = Preferences.model_validate(values).model_dump()
        return {
            "principal": user_id,
            "version": f"{settings['revision']}:{user['revision']}",
            "values": values,
            "sources": sources,
            "policy": policy,
        }

    @staticmethod
    def can_access(user, library_id):
        return bool(
            user
            and not user["disabled"]
            and (
                user["role"] == "admin"
                or user["library_scope"] is None
                or library_id in user["library_scope"]
            )
        )
