"""Sign-in for GPR Studio: who may open it, and a record of every attempt.

- Passwords are hashed with Argon2id (argon2-cffi's defaults); only the hash is stored.
- A session is a random 256-bit token in an HttpOnly, SameSite=Lax cookie; the database holds only
  its SHA-256, so a leaked dump or backup cannot be replayed to sign in.
- Every attempt (success, failure, lock-out, sign-out) goes to login_events with time, address and
  browser. Five failures for a name, or twenty from one address, within 15 minutes lock further
  attempts until the window passes.
- Every route except the sign-in page needs a session. With no login database configured the
  Studio refuses everything (fails closed) rather than opening up.
"""

from __future__ import annotations

import hashlib
import html
import re
import secrets
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from string import Template
from typing import Protocol
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from store.auth_store import Account, AuthStore

SESSION_COOKIE = "gpr_session"
SESSION_HOURS = 12  # one working day; then sign in again
LOCK_WINDOW_MINUTES = 15
MAX_FAILURES_PER_NAME = 5
MAX_FAILURES_PER_ADDRESS = 20
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{2,64}$")  # the database enforces the same
PUBLIC_PATHS = frozenset({"/login"})
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
_LOGIN_PAGE = Template((Path(__file__).resolve().parent / "login.html").read_text(encoding="utf-8"))


class BadCredentialsError(Exception):
    """Wrong name or password. Deliberately does not say which."""


class LockedOutError(Exception):
    """Too many recent failures for this name or address."""


@dataclass(frozen=True)
class User:
    user_id: int
    username: str


class Authenticator(Protocol):
    def user_for_request(self, request: Request) -> User | None: ...


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def check_password_rules(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(f"a password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters")


class Auth:
    def __init__(self, store: AuthStore) -> None:
        self.store = store
        self._hasher = PasswordHasher()

    @cached_property
    def _decoy_hash(self) -> str:
        # Verified against when the name is unknown, so an unknown name costs the same time as a
        # wrong password and response timing does not reveal which names exist.
        return self._hasher.hash(secrets.token_urlsafe(16))

    # ---- accounts (the python -m studio.users command)

    def add_user(self, username: str, password: str) -> None:
        if not USERNAME_PATTERN.match(username):
            raise ValueError("a username is 2-64 letters, digits, dots, dashes or underscores")
        check_password_rules(password)
        if self.store.account(username) is not None:
            raise ValueError(f"{username} already exists")
        self.store.create_account(username, self._hasher.hash(password))

    def set_password(self, username: str, password: str) -> None:
        check_password_rules(password)
        account = self._existing(username)
        self.store.set_password_hash(account.user_id, self._hasher.hash(password), changed=True)
        self.store.revoke_all_sessions(account.user_id)  # a new password signs out every device

    def set_disabled(self, username: str, disabled: bool) -> None:
        account = self._existing(username)
        self.store.set_disabled(account.user_id, disabled)
        if disabled:
            self.store.revoke_all_sessions(account.user_id)

    def _existing(self, username: str) -> Account:
        account = self.store.account(username)
        if account is None:
            raise ValueError(f"no account named {username}")
        return account

    # ---- signing in and out

    def sign_in(self, username: str, password: str, ip: str | None, user_agent: str | None) -> str:
        """A new session token, or BadCredentialsError / LockedOutError. Every outcome is logged."""
        username = username.strip()[:200]
        by_name, by_address = self.store.failures_since(username=username, ip=ip, minutes=LOCK_WINDOW_MINUTES)
        account = self.store.account(username) if username else None
        if by_name >= MAX_FAILURES_PER_NAME or by_address >= MAX_FAILURES_PER_ADDRESS:
            self.store.record("locked_out", username, account.user_id if account else None, ip, user_agent)
            raise LockedOutError
        if not self._password_matches(account.password_hash if account else None, password) \
                or account is None or account.disabled:
            self.store.record("login_failed", username, account.user_id if account else None, ip, user_agent)
            raise BadCredentialsError
        if self._hasher.check_needs_rehash(account.password_hash):
            self.store.set_password_hash(account.user_id, self._hasher.hash(password), changed=False)
        token = secrets.token_urlsafe(32)
        self.store.create_session(_digest(token), account.user_id, SESSION_HOURS, ip, user_agent)
        self.store.record("login", account.username, account.user_id, ip, user_agent)
        return token

    def _password_matches(self, stored_hash: str | None, password: str) -> bool:
        if len(password) > MAX_PASSWORD_LENGTH:
            return False
        try:
            return self._hasher.verify(stored_hash or self._decoy_hash, password) and stored_hash is not None
        except (VerificationError, InvalidHashError):
            return False

    def user_for_token(self, token: str) -> User | None:
        found = self.store.session_account(_digest(token)) if token else None
        return User(*found) if found else None

    def user_for_request(self, request: Request) -> User | None:
        return self.user_for_token(request.cookies.get(SESSION_COOKIE, ""))

    def sign_out(self, token: str, ip: str | None, user_agent: str | None) -> None:
        found = self.store.revoke_session(_digest(token)) if token else None
        if found:
            self.store.record("logout", found[1], found[0], ip, user_agent)


# ---------------------------------------------------------------- HTTP


def _client(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None), request.headers.get("user-agent")


def _safe_next(target: str | None) -> str:
    """Only a path on this site: an open redirect to another site would make a fine phishing link."""
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")):
        return "/"
    return target


def _page(message: str = "", *, next_path: str = "/", username: str = "", status: int = 200) -> HTMLResponse:
    body = _LOGIN_PAGE.substitute(
        message=f'<p class="message" role="alert">{html.escape(message)}</p>' if message else "",
        next=html.escape(next_path, quote=True), username=html.escape(username, quote=True))
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _auth(request: Request) -> Auth | None:
    return getattr(request.app.state, "auth", None)


router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/") -> HTMLResponse:
    return _page(next_path=_safe_next(next))


@router.post("/login")
async def login(request: Request, username: str = Form(""), password: str = Form(""),
                next: str = Form("/")) -> Response:
    auth = _auth(request)
    if not isinstance(auth, Auth):
        return _page("Sign-in is not set up on this server (no login database).", status=503)
    ip, agent = _client(request)
    try:
        token = await run_in_threadpool(auth.sign_in, username, password, ip, agent)
    except LockedOutError:
        return _page(f"Too many failed attempts. Try again in {LOCK_WINDOW_MINUTES} minutes.",
                     next_path=_safe_next(next), username=username, status=429)
    except BadCredentialsError:
        return _page("Wrong username or password.", next_path=_safe_next(next), username=username, status=401)
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_HOURS * 3600, path="/", httponly=True,
                        samesite="lax", secure=request.url.hostname not in _LOOPBACK)
    return response


@router.post("/logout")
async def logout(request: Request) -> Response:
    auth = _auth(request)
    if isinstance(auth, Auth):
        ip, agent = _client(request)
        await run_in_threadpool(auth.sign_out, request.cookies.get(SESSION_COOKIE, ""), ip, agent)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/api/me")
def me(request: Request) -> dict[str, str]:
    return {"username": request.state.user.username}


async def require_login(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Every route but the sign-in page needs a live session; without a login database, nothing opens."""
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    api = request.url.path.startswith("/api/")
    auth: Authenticator | None = _auth(request)  # type: ignore[assignment]
    if auth is None:
        message = "Sign-in is not set up on this server (no login database)."
        return JSONResponse({"detail": message}, status_code=503) if api else _page(message, status=503)
    user = await run_in_threadpool(auth.user_for_request, request)
    if user is None:
        if api:
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
    request.state.user = user
    return await call_next(request)
