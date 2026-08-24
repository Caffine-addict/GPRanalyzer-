"""Parser registry keyed by file extension — new formats register without touching callers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from core.contracts import ScanFrame


class Parser(Protocol):
    def __call__(self, path: Path) -> ScanFrame: ...


_REGISTRY: dict[str, Parser] = {}


def register(*extensions: str) -> Callable[[Parser], Parser]:
    """Decorator: register a parser function for one or more extensions.

    Extensions may be given with or without a leading dot ("jpg" or ".jpg").
    """

    def decorator(fn: Parser) -> Parser:
        for ext in extensions:
            key = ext.lower().lstrip(".")
            _REGISTRY[key] = fn
        return fn

    return decorator


def get_parser(path: Path | str) -> Parser:
    ext = Path(path).suffix.lower().lstrip(".")
    if ext not in _REGISTRY:
        raise KeyError(f"no parser registered for extension: .{ext}")
    return _REGISTRY[ext]


def registered_extensions() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
