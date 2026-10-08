"""Sign-in for GPR Studio, against a real Postgres (a fresh schema per test, tests/pg.py)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from store.auth_store import AuthStore
from studio import auth as auth_module
from studio import users
from studio.auth import Auth
from studio.server import app
from tests.pg import fresh_url

pytestmark = pytest.mark.real_auth

PASSWORD = "correct horse battery"


@pytest.fixture
def db_url() -> str:
    return fresh_url()


@pytest.fixture
def auth(db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Auth]:
    real = Auth(AuthStore(db_url))
    real.add_user("alice", PASSWORD)
    monkeypatch.setattr(app.state, "auth", real, raising=False)
    yield real
    real.store.close()


@pytest.fixture
def client(auth: Auth) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", follow_redirects=False,
                      headers={"user-agent": "pytest-browser"})


def _sql(url: str, sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(url) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _sign_in(client: TestClient, username: str = "alice", password: str = PASSWORD, **extra: str):  # type: ignore[no-untyped-def]
    return client.post("/login", data={"username": username, "password": password, **extra})


def test_nothing_opens_without_signing_in(client: TestClient) -> None:
    assert client.get("/api/jobs").status_code == 401
    page = client.get("/field?x=1")
    assert page.status_code == 303 and page.headers["location"] == "/login?next=%2Ffield%3Fx%3D1"
    assert client.get("/static/js/app.js").status_code == 303  # the app's code too
    assert client.get("/login").status_code == 200


def test_without_a_login_database_the_studio_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(app.state, "auth", raising=False)
    client = TestClient(app, base_url="http://127.0.0.1", follow_redirects=False)
    assert client.get("/api/jobs").status_code == 503
    assert client.get("/").status_code == 503


def test_signing_in_opens_the_studio_and_names_the_user(client: TestClient) -> None:
    response = _sign_in(client, next="/field")
    assert response.status_code == 303 and response.headers["location"] == "/field"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=43200" in cookie
    assert client.get("/api/me").json() == {"username": "alice"}
    assert client.get("/").status_code == 200


def test_passwords_and_session_tokens_are_stored_only_as_hashes(client: TestClient, db_url: str) -> None:
    response = _sign_in(client)
    token = response.cookies[auth_module.SESSION_COOKIE]
    ((stored_hash,),) = _sql(db_url, "SELECT password_hash FROM users")
    assert stored_hash.startswith("$argon2id$") and PASSWORD not in stored_hash
    ((stored_token,),) = _sql(db_url, "SELECT token_sha256 FROM sessions")
    assert bytes(stored_token) == hashlib.sha256(token.encode()).digest()


def test_every_attempt_is_logged_with_time_address_and_browser(client: TestClient, auth: Auth) -> None:
    assert _sign_in(client, password="wrong password!").status_code == 401
    assert _sign_in(client, username="mallory").status_code == 401  # unknown names are logged too
    _sign_in(client)
    client.post("/logout")
    events = auth.store.history()
    assert [(e.event, e.username) for e in events] == [
        ("logout", "alice"), ("login", "alice"), ("login_failed", "mallory"), ("login_failed", "alice")]
    assert all(e.ip and e.user_agent == "pytest-browser" and e.at for e in events)


def test_a_wrong_password_and_an_unknown_name_get_the_same_answer(client: TestClient) -> None:
    wrong, unknown = _sign_in(client, password="wrong password!"), _sign_in(client, username="nobody")
    assert wrong.status_code == unknown.status_code == 401
    assert "Wrong username or password." in wrong.text and "Wrong username or password." in unknown.text


def test_repeated_failures_lock_the_name_even_against_the_right_password(client: TestClient, auth: Auth) -> None:
    for _ in range(auth_module.MAX_FAILURES_PER_NAME):
        _sign_in(client, password="wrong password!")
    locked = _sign_in(client)
    assert locked.status_code == 429 and auth_module.SESSION_COOKIE not in locked.cookies
    assert auth.store.history(1)[0].event == "locked_out"


def test_signing_out_ends_the_session(client: TestClient) -> None:
    _sign_in(client)
    assert client.post("/logout").headers["location"] == "/login"
    client.cookies.clear()
    assert client.get("/api/me").status_code == 401


def test_a_stolen_cookie_dies_with_sign_out(client: TestClient) -> None:
    token = _sign_in(client).cookies[auth_module.SESSION_COOKIE]
    client.post("/logout")
    thief = TestClient(app, base_url="http://127.0.0.1", cookies={auth_module.SESSION_COOKIE: token})
    assert thief.get("/api/me").status_code == 401


@pytest.mark.parametrize("change", ["disable", "passwd"])
def test_disabling_or_a_new_password_ends_existing_sessions(client: TestClient, auth: Auth, change: str) -> None:
    _sign_in(client)
    if change == "disable":
        auth.set_disabled("alice", True)
        assert _sign_in(client).status_code == 401
    else:
        auth.set_password("alice", "a brand new passphrase")
    assert client.get("/api/me").status_code == 401


def test_an_expired_session_is_refused(client: TestClient, db_url: str) -> None:
    _sign_in(client)
    _sql(db_url, "UPDATE sessions SET expires_at = now() - interval '1 second'")
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example", "/\\evil.example"])
def test_after_signing_in_it_never_sends_you_to_another_site(client: TestClient, target: str) -> None:
    assert _sign_in(client, next=target).headers["location"] == "/"


def test_a_sign_in_posted_from_another_site_is_refused(client: TestClient) -> None:
    response = client.post("/login", data={"username": "alice", "password": PASSWORD},
                           headers={"origin": "https://evil.example"})
    assert response.status_code == 403


def test_through_the_https_proxy_the_cookie_is_secure_and_the_page_may_write(auth: Auth) -> None:
    # an HTTPS proxy on this machine serves https://studio.example.net and forwards to 127.0.0.1 over http
    proxied = TestClient(app, base_url="https://studio.example.net", follow_redirects=False)
    response = proxied.post("/login", data={"username": "alice", "password": PASSWORD},
                            headers={"origin": "https://studio.example.net"})
    assert response.status_code == 303 and "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize(("name", "password", "message"), [
    ("alice", PASSWORD, "already exists"), ("bad name!", PASSWORD, "username"), ("bob", "short", "12"),
])
def test_accounts_are_validated(auth: Auth, name: str, password: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        auth.add_user(name, password)


def test_the_users_command_creates_and_lists_accounts(db_url: str, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setattr(users.getpass, "getpass", lambda _prompt: "site supervisor one")
    assert users.main(["add", "sup1"]) == 0
    assert users.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "created sup1" in out and "sup1" in out.splitlines()[-1] and "never" in out
