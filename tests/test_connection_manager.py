"""Tests for api/connection_manager.py — minimal fake WebSocket-like objects, not a full
FastAPI test client (that's reserved for the actual endpoint tests in test_api_server.py).
"""

from __future__ import annotations

import pytest

from api.connection_manager import ConnectionManager


class _FakeWebSocket:
    def __init__(self, fail_on_send: bool = False) -> None:
        self.accepted = False
        self.received: list[dict] = []
        self._fail_on_send = fail_on_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        if self._fail_on_send:
            raise RuntimeError("connection closed")
        self.received.append(message)


@pytest.mark.asyncio
async def test_connect_accepts_and_tracks() -> None:
    manager = ConnectionManager()
    ws = _FakeWebSocket()
    await manager.connect(ws)  # type: ignore[arg-type]
    assert ws.accepted
    assert manager.connection_count == 1


@pytest.mark.asyncio
async def test_disconnect_removes_connection() -> None:
    manager = ConnectionManager()
    ws = _FakeWebSocket()
    await manager.connect(ws)  # type: ignore[arg-type]
    manager.disconnect(ws)  # type: ignore[arg-type]
    assert manager.connection_count == 0


def test_disconnect_unknown_connection_is_a_noop() -> None:
    manager = ConnectionManager()
    manager.disconnect(_FakeWebSocket())  # type: ignore[arg-type]  # must not raise


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_connections() -> None:
    manager = ConnectionManager()
    ws1, ws2 = _FakeWebSocket(), _FakeWebSocket()
    await manager.connect(ws1)  # type: ignore[arg-type]
    await manager.connect(ws2)  # type: ignore[arg-type]

    await manager.broadcast({"type": "finding.created"})

    assert ws1.received == [{"type": "finding.created"}]
    assert ws2.received == [{"type": "finding.created"}]


@pytest.mark.asyncio
async def test_broadcast_drops_dead_connection_without_failing_others() -> None:
    manager = ConnectionManager()
    dead = _FakeWebSocket(fail_on_send=True)
    alive = _FakeWebSocket()
    await manager.connect(dead)  # type: ignore[arg-type]
    await manager.connect(alive)  # type: ignore[arg-type]

    await manager.broadcast({"type": "finding.created"})

    assert alive.received == [{"type": "finding.created"}]
    assert manager.connection_count == 1  # dead connection was dropped
