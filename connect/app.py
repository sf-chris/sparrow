"""Sparrow Connect: the shared front door and phone book for Sparrow servers.

Run by the Sparrow project for every installation that opts in. It gives each
home server a stable HTTPS name (<id>.h.<domain>) and sends a person to their
own server. It never receives accounts, libraries, watch history or video.

What a home does, all over HTTPS with the token it got when it registered:

  POST   /api/v1/homes                  register; returns id, name and token
  PUT    /api/v1/homes/{id}/acme        publish a certificate proof (TXT)
  DELETE /api/v1/homes/{id}/acme        remove it
  POST   /api/v1/homes/{id}/check-in    "I answer at address:port"; Connect
                                        calls back over HTTPS, checking the
                                        certificate for the home's name and a
                                        proof only that home can make, and only
                                        then points the name at the address
  GET    /api/v1/homes/{id}             what Connect knows
  DELETE /api/v1/homes/{id}             forget the home and its records

A person's phone learns its home once from /h/{id}; / then sends it there.
"""

import asyncio
import base64
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import ssl
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

DOMAIN = os.environ.get("CONNECT_DOMAIN", "streamsparrow.com")
SUFFIX = f"h.{DOMAIN}"
DATA = Path(os.environ.get("CONNECT_DATA", "/data"))
COOKIE = "sparrow_home"
ID = re.compile(r"[a-z][a-z2-7]{9}")
# Registrations come through the office router's TLS passthrough, so every
# request shares one source address: the allowance is for the whole service.
REGISTRATIONS_PER_HOUR = int(os.environ.get("CONNECT_REGISTRATIONS_PER_HOUR", "60"))


class Route53:
    """The home records, in the Route 53 zone. The service's AWS key may
    change only names under h.<domain>."""

    def __init__(self, zone):
        import boto3

        self.zone = zone
        self.client = boto3.client("route53")

    def _change(self, action, name, kind, values, ttl):
        response = self.client.change_resource_record_sets(
            HostedZoneId=self.zone,
            ChangeBatch={
                "Changes": [
                    {
                        "Action": action,
                        "ResourceRecordSet": {
                            "Name": name,
                            "Type": kind,
                            "TTL": ttl,
                            "ResourceRecords": [{"Value": v} for v in values],
                        },
                    }
                ]
            },
        )
        return response["ChangeInfo"]["Id"]

    def current(self, name, kind):
        found = self.client.list_resource_record_sets(
            HostedZoneId=self.zone,
            StartRecordName=name,
            StartRecordType=kind,
            MaxItems="1",
        )["ResourceRecordSets"]
        if found and found[0]["Name"].rstrip(".") == name and found[0]["Type"] == kind:
            return found[0]
        return None

    def set(self, name, kind, values, ttl=60):
        return self._change("UPSERT", name, kind, values, ttl)

    def remove(self, name, kind):
        record = self.current(name, kind)
        if record:
            self._change(
                "DELETE",
                name,
                kind,
                [r["Value"] for r in record["ResourceRecords"]],
                record["TTL"],
            )

    def wait(self, change, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.client.get_change(Id=change)["ChangeInfo"]["Status"] == "INSYNC":
                return True
            time.sleep(3)
        return False


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS homes(
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created REAL NOT NULL,
                    seen REAL, address TEXT, port INTEGER, verified REAL, problem TEXT);
                CREATE TABLE IF NOT EXISTS events(kind TEXT NOT NULL, key TEXT NOT NULL, at REAL NOT NULL);
                """
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def allow(self, kind, key, limit, window):
        """A sliding-window allowance: record the event if under the limit."""
        now = time.time()
        with self.connect() as db:
            db.execute("DELETE FROM events WHERE at<?", (now - 24 * 3600,))
            used = db.execute(
                "SELECT COUNT(*) FROM events WHERE kind=? AND key=? AND at>?",
                (kind, key, now - window),
            ).fetchone()[0]
            if used >= limit:
                return False
            db.execute("INSERT INTO events VALUES(?,?,?)", (kind, key, now))
            return True


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def proof(token_hash, nonce):
    """What a home answers to a call-back. It holds the token; Connect holds
    only its hash, which keys the proof."""
    return hmac.new(token_hash.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def new_id():
    alphabet = "abcdefghijklmnopqrstuvwxyz234567"
    return secrets.choice(alphabet[:26]) + "".join(
        secrets.choice(alphabet) for _ in range(9)
    )


def page(title, body, status=200):
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="Your own film and TV library, on any screen.">
<style>
  :root {{ color-scheme: dark; --ink: #f4efe6; --muted: #a8a196; --accent: #f2b544; --bg: #11100e; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100dvh; display: grid; place-items: center; background: var(--bg);
         color: var(--ink); font: 17px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 24px 16px; }}
  main {{ max-width: 34rem; }}
  h1 {{ margin: 0 0 0.25em; font-size: clamp(2.5rem, 9vw, 4rem); letter-spacing: -0.03em; line-height: 1; }}
  h1 span {{ color: var(--accent); }}
  p {{ margin: 0.75em 0 0; color: var(--muted); }}
  p.lead {{ color: var(--ink); font-size: 1.25rem; }}
  a {{ color: var(--accent); }}
</style>
</head>
<body>
<main>
  <h1>sparrow<span>.</span></h1>
  {body}
</main>
</body>
</html>
""",
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


class Registered(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1


class Proof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(pattern=r"^[A-Za-z0-9_-]{20,128}$")


class CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(max_length=64)
    port: int = Field(ge=1, le=65535)


def create_app(store=None, dns=None, call_back=None):
    store = store or Store(DATA / "connect.db")
    dns = dns or Route53(os.environ["CONNECT_ZONE_ID"])

    async def default_call_back(name, address, port, nonce):
        """Reach the home as a TV would: by its name's certificate, at the
        address it claims. Connecting by address with the name as SNI means
        a wrong or unreachable address fails here, before DNS points at it."""
        context = ssl.create_default_context()
        transport = httpx.AsyncHTTPTransport(verify=context)
        host = f"[{address}]" if ":" in address else address
        async with httpx.AsyncClient(transport=transport, timeout=10) as client:
            response = await client.get(
                f"https://{host}:{port}/api/v1/connect/proof",
                params={"nonce": nonce},
                headers={"Host": f"{name}:{port}"},
                extensions={"sni_hostname": name},
            )
            response.raise_for_status()
            return response.json().get("proof", "")

    call_back = call_back or default_call_back

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(
        title="Sparrow Connect",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def home(home_id, authorization):
        if not ID.fullmatch(home_id or ""):
            raise HTTPException(404, "No Sparrow is registered with that name.")
        with store.connect() as db:
            row = db.execute("SELECT * FROM homes WHERE id=?", (home_id,)).fetchone()
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not row or not hmac.compare_digest(row["token_hash"], digest(token)):
            raise HTTPException(401, "This server's Connect registration isn't valid.")
        return row

    def status(row):
        name = f"{row['id']}.{SUFFIX}"
        return {
            "id": row["id"],
            "name": name,
            "link": f"https://{DOMAIN}/h/{row['id']}",
            "address": row["address"],
            "port": row["port"],
            "reachable": bool(row["verified"] and not row["problem"]),
            "problem": row["problem"],
            "checked": row["seen"],
        }

    @app.post("/api/v1/homes")
    def register(body: Registered):
        if not store.allow("register", "all", REGISTRATIONS_PER_HOUR, 3600):
            raise HTTPException(429, "Too many new servers right now. Try again in an hour.")
        token = secrets.token_urlsafe(32)
        with store.connect() as db:
            for _ in range(5):
                home_id = new_id()
                try:
                    db.execute(
                        "INSERT INTO homes(id, token_hash, created) VALUES(?,?,?)",
                        (home_id, digest(token), time.time()),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            row = db.execute("SELECT * FROM homes WHERE id=?", (home_id,)).fetchone()
        return {**status(row), "token": token}

    @app.get("/api/v1/homes/{home_id}")
    def describe(home_id: str, authorization: str = Header(default="")):
        return status(home(home_id, authorization))

    @app.put("/api/v1/homes/{home_id}/acme")
    async def publish_proof(
        home_id: str, body: Proof, authorization: str = Header(default="")
    ):
        row = home(home_id, authorization)
        if not store.allow("acme", row["id"], 20, 3600):
            raise HTTPException(429, "Too many certificate attempts. Try again in an hour.")
        name = f"_acme-challenge.{row['id']}.{SUFFIX}"
        change = await asyncio.to_thread(dns.set, name, "TXT", [f'"{body.value}"'], 60)
        ready = await asyncio.to_thread(dns.wait, change)
        return {"name": name, "ready": ready}

    @app.delete("/api/v1/homes/{home_id}/acme")
    async def remove_proof(home_id: str, authorization: str = Header(default="")):
        row = home(home_id, authorization)
        await asyncio.to_thread(dns.remove, f"_acme-challenge.{row['id']}.{SUFFIX}", "TXT")
        return {"removed": True}

    @app.post("/api/v1/homes/{home_id}/check-in")
    async def check_in(
        home_id: str, body: CheckIn, authorization: str = Header(default="")
    ):
        row = home(home_id, authorization)
        if not store.allow("check-in", row["id"], 30, 3600):
            raise HTTPException(429, "Checking in too often. Try again in a few minutes.")
        name = f"{row['id']}.{SUFFIX}"
        problem = None
        try:
            address = ipaddress.ip_address(body.address)
            if not address.is_global:
                raise ValueError
        except ValueError:
            address, problem = None, "That isn't a public internet address."
        if address:
            nonce = secrets.token_urlsafe(24)
            try:
                answer = await call_back(name, str(address), body.port, nonce)
                if not hmac.compare_digest(answer, proof(row["token_hash"], nonce)):
                    problem = f"Something else answered on port {body.port}."
            except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError):
                problem = (
                    f"Your server can't be reached from the internet on port {body.port}. "
                    "Forward that port on your router to this server, or turn on UPnP."
                )
        now = time.time()
        if not problem:
            kind = "AAAA" if address.version == 6 else "A"
            await asyncio.to_thread(dns.set, name, kind, [str(address)], 60)
        with store.connect() as db:
            db.execute(
                "UPDATE homes SET seen=?, address=?, port=?, problem=?, verified=CASE WHEN ? IS NULL THEN ? ELSE verified END WHERE id=?",
                (
                    now,
                    str(address) if address else row["address"],
                    body.port,
                    problem,
                    problem,
                    now,
                    row["id"],
                ),
            )
            row = db.execute("SELECT * FROM homes WHERE id=?", (row["id"],)).fetchone()
        return status(row)

    @app.delete("/api/v1/homes/{home_id}")
    async def forget(home_id: str, authorization: str = Header(default="")):
        row = home(home_id, authorization)
        name = f"{row['id']}.{SUFFIX}"
        for record, kind in ((name, "A"), (name, "AAAA"), (f"_acme-challenge.{name}", "TXT")):
            await asyncio.to_thread(dns.remove, record, kind)
        with store.connect() as db:
            db.execute("DELETE FROM homes WHERE id=?", (row["id"],))
        return {"forgotten": True}

    def reachable_home(home_id):
        if not ID.fullmatch(home_id or ""):
            return None
        with store.connect() as db:
            row = db.execute("SELECT * FROM homes WHERE id=?", (home_id,)).fetchone()
        return row if row and row["verified"] and row["port"] else None

    def address_of(row):
        port = "" if row["port"] == 443 else f":{row['port']}"
        return f"https://{row['id']}.{SUFFIX}{port}/"

    @app.get("/h/{home_id}")
    def link(home_id: str):
        """Opened once on a phone, from the server's Settings: remember it."""
        row = reachable_home(home_id)
        if not row:
            return page(
                "Sparrow",
                '<p class="lead">This Sparrow isn’t reachable right now.</p>'
                "<p>Check that the server is on, and that Sparrow Connect is on in its Settings.</p>",
                404,
            )
        response = RedirectResponse(address_of(row), status_code=303)
        response.set_cookie(
            COOKIE, row["id"], max_age=400 * 24 * 3600, secure=True, httponly=True, samesite="lax"
        )
        return response

    @app.get("/forget")
    def forget_device():
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/")
    def front(request: Request):
        row = reachable_home(request.cookies.get(COOKIE, ""))
        if row:
            return RedirectResponse(address_of(row), status_code=303)
        return page(
            "Sparrow",
            '<p class="lead">Your own film and TV library, on any screen.</p>'
            "<p>To open your library here, turn on Sparrow Connect in your server’s "
            "Settings, then open its link on this device.</p>",
        )

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app() if os.environ.get("CONNECT_ZONE_ID") else None
