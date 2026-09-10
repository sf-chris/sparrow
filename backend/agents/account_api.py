"""Authenticated local setup and household preferences; no external access wizard."""

import asyncio
import hmac
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from .accounts import Accounts, Preferences, Policy, digest, password_hash

COOKIE = "sparrow_session"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignIn(Input):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)


class SignUp(SignIn):
    name: str = Field(min_length=1, max_length=100)
    invitation: str = ""
    setup_code: str = ""
    defaults: Preferences | None = None


class PreferencePatch(Input):
    values: dict


class DefaultPatch(Input):
    defaults: Preferences
    policy: Policy


class Invite(Input):
    role: Literal["viewer", "requester", "admin"] = "viewer"
    library_scope: list[str] | None = None


class AccessPatch(Invite):
    disabled: bool = False


def administrator(request: Request):
    if request.state.user["role"] != "admin":
        raise HTTPException(403, "Administrator access is required.")
    return request.state.user


def install_accounts(app, storage, get_service):
    accounts = Accounts(storage.data_dir)
    code_path = Path(storage.data_dir) / "owner-setup-code"
    if not accounts.has_users() and not code_path.exists():
        import secrets, os

        fd = os.open(code_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(18))
    router = APIRouter(prefix="/api/v1")

    @app.middleware("http")
    async def identity_boundary(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/v1/node/"):
            return await call_next(
                request
            )  # Node routes enforce their own unique credentials.
        protected = path.startswith("/api/") or path.startswith("/art/")
        if not protected:
            return await call_next(request)
        public = path in {
            "/api/v1/auth/status",
            "/api/v1/auth/login",
            "/api/v1/auth/bootstrap",
            "/api/v1/auth/join",
        }
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if request.headers.get("x-sparrow-request") != "1" or (
                origin and origin.rstrip("/") != expected
            ):
                return JSONResponse(
                    {"detail": "This action must come from the Sparrow interface."},
                    status_code=403,
                )
        user = accounts.from_session(request.cookies.get(COOKIE))
        request.state.user = user
        if not public and not user:
            return JSONResponse({"detail": "Sign in to Sparrow."}, status_code=401)
        if not public and user["role"] != "admin" and not path.startswith("/api/v1/"):
            # Legacy APIs expose server-wide diagnostics and mutable filesystem paths.
            # Household clients use the scoped product API exclusively.
            if path.startswith("/art/"):
                allowed = any(
                    path in (i.poster_path, i.backdrop_path)
                    and accounts.can_access(user, i.metadata.get("library_id", "local"))
                    for i in storage.get_library()
                )
                if not allowed:
                    return JSONResponse(
                        {"detail": "Artwork unavailable."}, status_code=404
                    )
            else:
                return JSONResponse(
                    {"detail": "Administrator access is required."}, status_code=403
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def session_cookie(request, response, user):
        token = accounts.new_session(
            user["id"], request.headers.get("user-agent", "Browser")
        )
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=30 * 86400,
            path="/",
        )
        return {"user": user, "preferences": accounts.resolve(user["id"])}

    @router.get("/auth/status")
    def auth_status(request: Request):
        user = request.state.user
        return {
            "needs_setup": not accounts.has_users(),
            "user": user,
            "preferences": accounts.resolve(user["id"]) if user else None,
        }

    @router.post("/auth/bootstrap")
    async def bootstrap(body: SignUp, request: Request, response: Response):
        if accounts.has_users():
            raise HTTPException(409, "An owner already exists. Sign in instead.")
        if not code_path.exists() or not hmac.compare_digest(
            body.setup_code.strip(), code_path.read_text().strip()
        ):
            raise HTTPException(
                403, "Enter the owner setup code from the server installation."
            )
        try:
            user = await asyncio.to_thread(
                accounts.create_user,
                body.username,
                body.password,
                body.name,
                bootstrap=True,
            )
            if body.defaults:
                accounts.set_defaults(body.defaults.model_dump())
            accounts.set_preferences(user["id"], {}, welcomed=False)
            # Existing alpha data belongs to its first explicitly created owner.
            service = get_service()
            if service:
                for job in service.store.get_jobs():
                    if not job.user_id:
                        job.user_id = user["id"]
                        job.preferences = accounts.resolve(user["id"])
                        service.store.save_job(job)
            code_path.unlink(missing_ok=True)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return session_cookie(request, response, accounts.user(user["id"]))

    @router.post("/auth/login")
    async def login(body: SignIn, request: Request, response: Response):
        try:
            user = await asyncio.to_thread(
                accounts.authenticate,
                body.username,
                body.password,
                request.client.host if request.client else "local",
            )
        except ValueError as exc:
            raise HTTPException(401, str(exc)) from exc
        return session_cookie(request, response, user)

    @router.post("/auth/join")
    async def join(body: SignUp, request: Request, response: Response):
        try:
            user = await asyncio.to_thread(
                accounts.create_user,
                body.username,
                body.password,
                body.name,
                invitation=body.invitation,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return session_cookie(request, response, user)

    @router.post("/auth/logout")
    def logout(request: Request, response: Response):
        accounts.revoke(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @router.get("/preferences")
    def preferences(request: Request):
        return {
            "effective": accounts.resolve(request.state.user["id"]),
            "overrides": request.state.user["preferences"],
            "defaults": accounts.server_settings()["defaults"],
        }

    @router.patch("/preferences")
    def set_preferences(body: PreferencePatch, request: Request):
        try:
            return accounts.set_preferences(request.state.user["id"], body.values)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/admin/defaults")
    def defaults(request: Request):
        administrator(request)
        return accounts.server_settings()

    @router.put("/admin/defaults")
    def set_defaults(body: DefaultPatch, request: Request):
        administrator(request)
        return accounts.set_defaults(
            body.defaults.model_dump(), body.policy.model_dump()
        )

    @router.get("/admin/users")
    def users(request: Request):
        administrator(request)
        return accounts.users()

    @router.post("/admin/invitations")
    def invitation(body: Invite, request: Request):
        administrator(request)
        token = accounts.invite(body.role, body.library_scope)
        return {"path": "/join#" + token, "expires_in_days": 7}

    @router.patch("/admin/users/{user_id}")
    async def access(user_id: str, body: AccessPatch, request: Request):
        actor = administrator(request)
        if actor["id"] == user_id:
            raise HTTPException(
                422, "Use another administrator to change your own access."
            )
        if not accounts.user(user_id):
            raise HTTPException(404, "Account not found.")
        with accounts.connect() as db:
            db.execute(
                "UPDATE users SET role=?, library_scope=?, disabled=?, revision=revision+1 WHERE id=?",
                (
                    body.role,
                    (
                        json.dumps(body.library_scope)
                        if body.library_scope is not None
                        else None
                    ),
                    int(body.disabled),
                    user_id,
                ),
            )
            db.execute("DELETE FROM login_sessions WHERE user_id=?", (user_id,))
        user = accounts.user(user_id)
        service = get_service()
        if service:
            for job in service.store.get_jobs():
                if (
                    job.user_id == user_id
                    and job.status.value == "active"
                    and (
                        user["disabled"]
                        or user["role"] == "viewer"
                        or not accounts.can_access(user, job.library_id)
                    )
                ):
                    await service.pause_job(job.id)
        return user

    @router.get("/sessions")
    def sessions(request: Request):
        with accounts.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id, created, expires, label FROM login_sessions WHERE user_id=?",
                    (request.state.user["id"],),
                )
            ]

    @router.delete("/sessions/{session_id}")
    def revoke_session(session_id: str, request: Request):
        with accounts.connect() as db:
            db.execute(
                "DELETE FROM login_sessions WHERE id=? AND user_id=?",
                (session_id, request.state.user["id"]),
            )
        return {"ok": True}

    class PasswordChange(Input):
        current_password: str = Field(max_length=1024)
        new_password: str = Field(min_length=8, max_length=1024)

    @router.post("/auth/password")
    async def change_password(
        body: PasswordChange, request: Request, response: Response
    ):
        user = request.state.user
        try:
            await asyncio.to_thread(
                accounts.authenticate,
                user["username"],
                body.current_password,
                request.client.host if request.client else "local",
            )
            hashed = await asyncio.to_thread(password_hash, body.new_password)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with accounts.connect() as db:
            db.execute("UPDATE users SET password=? WHERE id=?", (hashed, user["id"]))
            db.execute("DELETE FROM login_sessions WHERE user_id=?", (user["id"],))
        return session_cookie(request, response, user)

    app.include_router(router)
    return accounts
