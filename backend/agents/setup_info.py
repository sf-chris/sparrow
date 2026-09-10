"""Local first-administrator handoff for server operators and installation agents."""

import argparse
from contextlib import closing
import logging
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlencode, urlsplit


def read_setup_code(data_dir):
    """Read existing setup state without creating or changing an installation."""
    root = Path(data_dir).expanduser().resolve()
    database = root / "sparrow.db"
    if not database.is_file():
        raise ValueError("Start Sparrow first, then run this command again.")
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro")) as db:
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                return None
            try:
                return (root / "owner-setup-code").read_text().strip()
            except FileNotFoundError:
                # An administrator may have finished setup while we were reading.
                if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                    return None
                raise ValueError(
                    "Sparrow is still starting. Try again when it is ready."
                )
    except sqlite3.Error as exc:
        raise ValueError(
            "Could not read Sparrow's setup state. Check the server startup logs."
        ) from exc


def setup_link(url, code):
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and parsed.hostname not in ("0.0.0.0", "::")
            and parsed.username is None
            and parsed.password is None
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "Use the HTTP or HTTPS browser address for this server, without a path, query or credentials."
        )
    # Fragments are not sent with HTTP requests; the interface removes it after reading.
    return url.rstrip("/") + "/#" + urlencode({"setup_code": code})


def log_setup(data_dir):
    logger = logging.getLogger("sparrow.setup")
    try:
        code = read_setup_code(data_dir)
    except ValueError as exc:
        logger.warning("%s", exc)
        return
    if code is not None:
        logger.info(
            "Set up your Sparrow server\n"
            "Setup code: %s\n"
            "Open Sparrow in your browser and create the first administrator account.\n"
            "Installation agents: return this code and the working browser address to the owner.\n"
            "For a link with the code filled in, run: python -m backend.agents.setup_info --url <browser-address>\n"
            "This code stops working after the administrator account is created.",
            code,
        )


def main():
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Show this server's first-administrator setup information."
    )
    parser.add_argument("--data-dir", default=os.getenv("SPARROW_DATA_DIR", "./data"))
    parser.add_argument(
        "--url", help="The address the owner will open, including the port when needed."
    )
    args = parser.parse_args()
    try:
        code = read_setup_code(args.data_dir)
        if code is None:
            print(
                "Administrator setup is already complete. Sign in with your existing account."
            )
            return 0
        link = setup_link(args.url, code) if args.url else None
        print("Set up your Sparrow server")
        if link:
            print("Create administrator account: " + link)
        print("Setup code: " + code)
        print(
            "Use this once to create the first administrator account. Invite other people from Settings > People afterward."
        )
        return 0
    except ValueError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
