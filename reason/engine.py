"""Reasoning over Evidence + risk, behind an LLMClient interface so the model can be swapped.

Groq is the current provider (see CLAUDE.md for why — no vision call needed,
since Evidence is already text; strict JSON schema mode is only supported on
the openai/gpt-oss-* models, hence config.yaml's reasoning.model choice).
This will be swapped for a local model once the company confirms one is
available — that should only ever require a new LLMClient implementation,
never a change to ReasoningEngine or its callers.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol

from core.config import ReasoningConfig
from core.contracts import Evidence
from reason.prompt import build_prompt
from reason.schema import JSON_SCHEMA, ReasoningResult, parse_reasoning_response
from risk.score import RiskAssessment

logger = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


class LLMClient(Protocol):
    def complete_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
        strict: bool,
    ) -> dict[str, Any]: ...


class GroqClient:
    """LLMClient backed by Groq's OpenAI-compatible chat completions API."""

    def __init__(self, api_key: str) -> None:
        from groq import (
            Groq,  # deferred: keeps this import cost out of code paths that don't need it
        )

        self._client = Groq(api_key=api_key)

    def complete_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
        strict: bool,
    ) -> dict[str, Any]:
        response = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout_s,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "gpr_finding", "strict": strict, "schema": schema},
            },
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("Groq response had no message content")
        result: dict[str, Any] = json.loads(content)
        return result


class ReasoningEngine:
    """Wraps an LLMClient with the project's prompt-building and failure-handling rules."""

    def __init__(self, client: LLMClient, config: ReasoningConfig, prompt_dir: Path = _PROMPT_DIR) -> None:
        self._client = client
        self._config = config
        self._template_path = prompt_dir / f"{config.prompt_version}.txt"

    def reason(self, evidence: Evidence, risk: RiskAssessment) -> tuple[ReasoningResult | None, float]:
        """Returns (result, latency_ms). result is None on a prompt-building error, timeout,
        parse failure, or any client-level error — reasoning is best-effort and a Finding
        must survive without it, no matter which stage fails.
        """
        start = time.monotonic()
        try:
            prompt = build_prompt(evidence, risk, self._template_path)
            raw = self._client.complete_json(
                prompt,
                schema=JSON_SCHEMA,
                model=self._config.model,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
                timeout_s=self._config.timeout_s,
                strict=self._config.strict_json,
            )
            result = parse_reasoning_response(raw)
        except Exception as e:  # noqa: BLE001 - reasoning is best-effort; deliberately catch-all
            latency_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "reason.failed latency_ms=%.2f error_type=%s error=%s", latency_ms, type(e).__name__, e
            )
            return None, latency_ms

        latency_ms = (time.monotonic() - start) * 1000
        logger.info("reason.success latency_ms=%.2f", latency_ms)
        return result, latency_ms
