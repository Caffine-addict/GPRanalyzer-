"""EdgeGatewaySource — for a device that pushes/streams frames to a local gateway process.

Structurally complete: it satisfies the ScanSource ABC and is wired into
sources/factory.py, so selecting it is a config change (source.type:
edge_gateway in config.yaml). What it does NOT do is talk to a real gateway
yet — the protocol (MQTT/HTTP-push/other), message framing, and auth are
unknown until the company confirms the edge device. frames() raises
NotImplementedError rather than guessing a protocol. See CLAUDE.md: "the
source seam" and "when a session proposes touching downstream code to
accommodate a source, that's the seam failing" — the reverse holds here too,
this stub does not touch downstream code, it only stays unimplemented.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.config import EdgeGatewaySourceConfig
from core.contracts import ScanFrame, SourceCapabilities
from sources.base import ScanSource


class EdgeGatewaySource(ScanSource):
    def __init__(self, config: EdgeGatewaySourceConfig) -> None:
        self._config = config

    def frames(self) -> Iterator[ScanFrame]:
        raise NotImplementedError(
            "EdgeGatewaySource is not yet implemented: the real protocol for "
            f"host={self._config.host} port={self._config.port} "
            f"protocol={self._config.protocol!r} topic={self._config.topic!r} "
            "is unknown until the company confirms the edge device. "
            "Implement against the real spec when it arrives — see CLAUDE.md."
        )
        yield  # pragma: no cover - unreachable; keeps this a generator function

    def capabilities(self) -> SourceCapabilities:
        # Conservative until the real gateway/device proves otherwise.
        return SourceCapabilities(
            has_calibrated_depth=False,
            has_real_position=False,
            has_true_amplitude=False,
            latency_class="realtime",
        )
