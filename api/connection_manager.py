"""WebSocket connection tracking and broadcast. Knows nothing about surveys or the
orchestrator — just accepts connections and fans a JSON message out to all of them,
dropping any connection that fails to receive it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        # A snapshot, not a live view: `await websocket.send_json(...)` below
        # yields control back to the event loop, and a client connecting or
        # disconnecting during that window would otherwise mutate
        # self._connections while this loop is iterating it directly —
        # RuntimeError: Set changed size during iteration.
        for websocket in list(self._connections):
            try:
                await websocket.send_json(message)
            except Exception as e:  # noqa: BLE001 - one dead client must not break the broadcast
                logger.warning("connection_manager.send_failed error=%s", e)
                dead.append(websocket)
        for websocket in dead:
            self._connections.discard(websocket)

    @property
    def connection_count(self) -> int:
        return len(self._connections)
