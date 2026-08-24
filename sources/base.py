"""ScanSource ABC — the seam between ingestion and everything downstream."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from core.contracts import ScanFrame, SourceCapabilities


class ScanSource(ABC):
    """A source of ScanFrames. Downstream code depends only on this interface,
    never on which concrete source is running.
    """

    @abstractmethod
    def frames(self) -> Iterator[ScanFrame]:
        """Yield ScanFrames in order.

        May block between frames (live sources pacing to real time) or
        return immediately (replay in step mode). Callers that need
        single-step control simply call next() on the returned iterator
        instead of consuming it in a for-loop.
        """

    @abstractmethod
    def capabilities(self) -> SourceCapabilities:
        """Declare, honestly, what this source can and cannot provide."""

    @property
    def latency_class(self) -> str:
        return self.capabilities().latency_class
