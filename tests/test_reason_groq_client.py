"""Tests for reason/engine.py's GroqClient — the real Groq wiring, mocked at the SDK boundary.

No real API calls: `groq.Groq` is patched at the module level. GroqClient
imports it lazily inside __init__ (`from groq import Groq`), which re-reads
the current attribute of the groq module at call time, so patching
groq.Groq before construction is sufficient.
"""

from __future__ import annotations

import pytest

from reason.engine import GroqClient
from reason.schema import JSON_SCHEMA

_VALID_CONTENT = '{"what": "x", "where": "y", "why": "z", "how": "w", "recommended_action": "a"}'


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str | None) -> None:
        self._content = content
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content: str | None) -> None:
        self.completions = _FakeCompletions(content)


class _FakeGroqSDK:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.chat = _FakeChat(_VALID_CONTENT)


def test_groq_client_constructs_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import groq

    monkeypatch.setattr(groq, "Groq", _FakeGroqSDK)
    client = GroqClient(api_key="test-key")
    assert client._client.api_key == "test-key"  # type: ignore[attr-defined]


def test_groq_client_complete_json_passes_correct_params(monkeypatch: pytest.MonkeyPatch) -> None:
    import groq

    monkeypatch.setattr(groq, "Groq", _FakeGroqSDK)
    client = GroqClient(api_key="test-key")

    result = client.complete_json(
        "a prompt",
        schema=JSON_SCHEMA,
        model="openai/gpt-oss-120b",
        temperature=0.2,
        max_tokens=1024,
        timeout_s=20,
        strict=True,
    )

    call = client._client.chat.completions.calls[0]  # type: ignore[attr-defined]
    assert call["model"] == "openai/gpt-oss-120b"
    assert call["temperature"] == 0.2
    assert call["max_tokens"] == 1024
    assert call["timeout"] == 20
    assert call["messages"] == [{"role": "user", "content": "a prompt"}]
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["response_format"]["json_schema"]["schema"] == JSON_SCHEMA
    assert result == {"what": "x", "where": "y", "why": "z", "how": "w", "recommended_action": "a"}


def test_groq_client_passes_strict_false_through_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # reasoning.strict_json is loaded from config specifically so it can
    # disable strict mode (e.g. for a model that doesn't support it) — must
    # not be silently hardcoded to True regardless of what's configured.
    import groq

    monkeypatch.setattr(groq, "Groq", _FakeGroqSDK)
    client = GroqClient(api_key="test-key")

    client.complete_json(
        "a prompt", schema=JSON_SCHEMA, model="m", temperature=0.1, max_tokens=10, timeout_s=5, strict=False
    )

    call = client._client.chat.completions.calls[0]  # type: ignore[attr-defined]
    assert call["response_format"]["json_schema"]["strict"] is False


def test_groq_client_raises_clear_error_on_none_content(monkeypatch: pytest.MonkeyPatch) -> None:
    import groq

    class _NoneContentSDK(_FakeGroqSDK):
        def __init__(self, api_key: str | None = None) -> None:
            super().__init__(api_key)
            self.chat = _FakeChat(None)

    monkeypatch.setattr(groq, "Groq", _NoneContentSDK)
    client = GroqClient(api_key="test-key")

    with pytest.raises(ValueError, match="no message content"):
        client.complete_json(
            "prompt",
            schema=JSON_SCHEMA,
            model="m",
            temperature=0.1,
            max_tokens=10,
            timeout_s=5,
            strict=True,
        )
