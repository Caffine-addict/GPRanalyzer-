"""Tests for reason/engine.py — mocked LLMClient throughout; no real Groq calls in tests."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from core.config import ReasoningConfig
from core.contracts import Evidence
from reason.engine import ReasoningEngine
from reason.schema import ReasoningResult
from risk.score import RiskAssessment

_VALID_RESPONSE = {
    "what": "A linear reflector consistent with a buried utility line.",
    "where": "Depth unavailable; position unavailable.",
    "why": "Moderate detector confidence.",
    "how": "Low confidence overall; no calibration data available.",
    "recommended_action": "Confirm with a second survey pass.",
}


def _config(**overrides: object) -> ReasoningConfig:
    base: dict[str, object] = {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "fallback_model": "openai/gpt-oss-20b",
        "temperature": 0.2,
        "max_tokens": 1024,
        "timeout_s": 20,
        "strict_json": True,
        "prompt_version": "v1_finding",
    }
    base.update(overrides)
    return ReasoningConfig(**base)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> Evidence:
    base: dict[str, object] = {
        "detection_class": "cavities",
        "detection_confidence": 0.85,
        "depth_m": None,
        "depth_confidence": "unavailable",
        "position_m": None,
        "position_confidence": "unavailable",
        "amplitude": None,
        "amplitude_confidence": "unavailable",
        "hyperbola_width_px": 12.0,
    }
    base.update(overrides)
    return Evidence(**base)  # type: ignore[arg-type]


_RISK = RiskAssessment(score=0.5, level="MEDIUM", rules_fired=())


class _FakeClient:
    def __init__(self, response: dict[str, Any] | None = None, raises: Exception | None = None) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self,
        prompt: str,
        *,
        schema: dict,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
        strict: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "schema": schema,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout_s": timeout_s,
                "strict": strict,
            }
        )
        if self._raises is not None:
            raise self._raises
        assert self._response is not None
        return self._response


def test_valid_response_parses() -> None:
    client = _FakeClient(response=_VALID_RESPONSE)
    engine = ReasoningEngine(client, _config())

    result, latency_ms = engine.reason(_evidence(), _RISK)

    assert isinstance(result, ReasoningResult)
    assert result.what == _VALID_RESPONSE["what"]
    assert latency_ms >= 0.0


def test_client_receives_config_values() -> None:
    client = _FakeClient(response=_VALID_RESPONSE)
    config = _config(
        model="openai/gpt-oss-20b", temperature=0.4, max_tokens=512, timeout_s=5, strict_json=False
    )
    engine = ReasoningEngine(client, config)

    engine.reason(_evidence(), _RISK)

    call = client.calls[0]
    assert call["model"] == "openai/gpt-oss-20b"
    assert call["temperature"] == 0.4
    assert call["max_tokens"] == 512
    assert call["timeout_s"] == 5
    assert call["strict"] is False  # config.strict_json must actually reach the client, not be ignored


def test_prompt_build_failure_returns_none_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    # A prompt-construction error (bad template path, bad Evidence, etc.)
    # must degrade the same way a client-level failure does — reasoning is
    # best-effort end to end, not just from the client call onward.
    import reason.engine as engine_module

    def _broken_build_prompt(evidence, risk, template_path):
        raise RuntimeError("template rendering exploded")

    monkeypatch.setattr(engine_module, "build_prompt", _broken_build_prompt)
    client = _FakeClient(response=_VALID_RESPONSE)
    engine = ReasoningEngine(client, _config())

    result, latency_ms = engine.reason(_evidence(), _RISK)

    assert result is None
    assert latency_ms >= 0.0
    assert client.calls == []  # never reached the client at all


def test_malformed_json_shape_returns_none_without_raising() -> None:
    client = _FakeClient(response={"what": "only one field"})
    engine = ReasoningEngine(client, _config())

    result, latency_ms = engine.reason(_evidence(), _RISK)

    assert result is None
    assert latency_ms >= 0.0


def test_non_dict_response_returns_none_without_raising() -> None:
    client = _FakeClient(response=["not", "a", "dict"])  # type: ignore[arg-type]
    engine = ReasoningEngine(client, _config())

    result, _ = engine.reason(_evidence(), _RISK)
    assert result is None


def test_client_timeout_returns_none() -> None:
    client = _FakeClient(raises=TimeoutError("request timed out"))
    engine = ReasoningEngine(client, _config())

    result, latency_ms = engine.reason(_evidence(), _RISK)

    assert result is None
    assert latency_ms >= 0.0


def test_client_arbitrary_exception_returns_none_not_raises() -> None:
    # Any client-level failure (network error, API error, rate limit, ...)
    # must degrade gracefully — reasoning is best-effort.
    client = _FakeClient(raises=RuntimeError("connection reset"))
    engine = ReasoningEngine(client, _config())

    result, _ = engine.reason(_evidence(), _RISK)
    assert result is None


def test_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient(raises=RuntimeError("boom"))
    engine = ReasoningEngine(client, _config())

    with caplog.at_level(logging.WARNING, logger="reason.engine"):
        engine.reason(_evidence(), _RISK)

    messages = [r.getMessage() for r in caplog.records]
    # Each attempt is logged with its model and error, then one final line naming every model
    # tried. An operator reading logs after a survey needs to see that the fallback was used and
    # also failed — "reasoning failed" alone hides whether the second model was even reached.
    assert any("reason.attempt_failed" in m and "RuntimeError" in m for m in messages)
    assert any("reason.failed" in m and "models_tried" in m for m in messages)


def test_success_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeClient(response=_VALID_RESPONSE)
    engine = ReasoningEngine(client, _config())

    with caplog.at_level(logging.INFO, logger="reason.engine"):
        engine.reason(_evidence(), _RISK)

    messages = [r.getMessage() for r in caplog.records]
    assert any("reason.success" in m for m in messages)


def test_prompt_sent_to_client_reflects_unavailable_evidence() -> None:
    client = _FakeClient(response=_VALID_RESPONSE)
    engine = ReasoningEngine(client, _config())

    engine.reason(_evidence(depth_confidence="unavailable", depth_m=None), _RISK)

    prompt = client.calls[0]["prompt"]
    depth_line = next(line for line in prompt.splitlines() if line.startswith("- depth"))
    assert "unavailable" in depth_line
    assert "do not state a depth value" in depth_line


def test_schema_passed_to_client_matches_reason_schema() -> None:
    from reason.schema import JSON_SCHEMA

    client = _FakeClient(response=_VALID_RESPONSE)
    engine = ReasoningEngine(client, _config())

    engine.reason(_evidence(), _RISK)

    assert client.calls[0]["schema"] == JSON_SCHEMA


class _SequencedClient:
    """A client whose outcome differs per call, so the fallback path can be exercised.

    `_FakeClient` holds one outcome for every call and so cannot express "fail, then succeed",
    which is exactly the sequence the fallback exists for. Each call's model is recorded, and an
    unexpected extra call fails loudly rather than silently returning something.
    """

    def __init__(self, *outcomes: Exception | dict[str, Any]) -> None:
        self._outcomes = list(outcomes)
        self.models: list[str] = []

    def complete_json(
        self,
        prompt: str,
        *,
        schema: dict,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
        strict: bool,
    ) -> dict[str, Any]:
        self.models.append(model)
        if not self._outcomes:
            raise AssertionError(f"unexpected extra call with model={model!r}")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_a_primary_model_failure_falls_back_to_the_smaller_model() -> None:
    # The two failures a live survey actually hits are the provider rate-limiting and the primary
    # model timing out. Both are worth one retry on the faster model before giving up.
    client = _SequencedClient(RuntimeError("rate limited"), _VALID_RESPONSE)
    engine = ReasoningEngine(client, _config())

    result, _ = engine.reason(_evidence(), _RISK)
    assert result is not None
    assert result.what == _VALID_RESPONSE["what"]
    assert client.models == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_both_models_failing_still_degrades_to_none() -> None:
    client = _SequencedClient(RuntimeError("boom"), RuntimeError("boom again"))
    engine = ReasoningEngine(client, _config())

    result, latency_ms = engine.reason(_evidence(), _RISK)
    assert result is None
    assert latency_ms >= 0.0
    assert client.models == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_a_prompt_failure_is_not_retried_on_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # A broken prompt fails identically on every model, so a second billed call would learn
    # nothing. The client must not be reached at all.
    def _explode(*_args: object, **_kwargs: object) -> str:
        raise OSError("template missing")

    client = _SequencedClient(_VALID_RESPONSE)
    engine = ReasoningEngine(client, _config())
    monkeypatch.setattr("reason.engine.build_prompt", _explode)

    result, _ = engine.reason(_evidence(), _RISK)
    assert result is None
    assert client.models == []


def test_an_identical_fallback_model_is_not_called_twice() -> None:
    # Configuring the fallback to the primary model means "no fallback", not "try it again".
    client = _SequencedClient(RuntimeError("boom"))
    engine = ReasoningEngine(client, _config(fallback_model="openai/gpt-oss-120b"))

    result, _ = engine.reason(_evidence(), _RISK)
    assert result is None
    assert client.models == ["openai/gpt-oss-120b"]
