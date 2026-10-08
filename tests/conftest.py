"""Suite-wide fixtures."""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest

from tests import pg


@pytest.fixture(scope="session", autouse=True)
def _drop_test_schemas() -> Iterator[None]:
    yield
    pg.drop_all()


class _SignedIn:
    """Test-only stand-in for studio.auth.Auth: every request is from one signed-in user.

    Only tests use it; the real Studio fails closed without a login database. Tests of signing in
    itself are marked `real_auth` and install the real thing.
    """

    def user_for_request(self, _request: object) -> object:
        from studio.auth import User

        return User(user_id=1, username="tester")


@pytest.fixture(autouse=True)
def _studio_signed_in(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    server = sys.modules.get("studio.server")
    if server is not None and request.node.get_closest_marker("real_auth") is None:
        monkeypatch.setattr(server.app.state, "auth", _SignedIn(), raising=False)
