"""Expected JSON output schema for the reasoning layer: what/where/why/how + recommended_action.

Strict parsing with a clear failure path — a malformed response never
propagates past this module as a raw exception from json.loads or a KeyError;
reason/engine.py catches ReasoningParseError (among other things) and the
Finding survives without reasoning rather than crashing the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "v1_finding"

_FIELDS = ("what", "where", "why", "how", "recommended_action")

# Groq's strict json_schema structured-output mode (currently only
# supported on the openai/gpt-oss-* models) requires every property in
# `required` and additionalProperties: false — see config.yaml's
# reasoning.strict_json and CLAUDE.md's notes on why gpt-oss was chosen.
JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {field: {"type": "string"} for field in _FIELDS},
    "required": list(_FIELDS),
    "additionalProperties": False,
}


class ReasoningParseError(Exception):
    """Raised when a reasoning response doesn't parse into the expected schema."""


@dataclass(frozen=True)
class ReasoningResult:
    what: str
    where: str
    why: str
    how: str
    recommended_action: str


def parse_reasoning_response(raw: Any) -> ReasoningResult:
    if not isinstance(raw, dict):
        raise ReasoningParseError(f"expected a JSON object, got {type(raw).__name__}")

    missing = [field for field in _FIELDS if field not in raw]
    if missing:
        raise ReasoningParseError(f"response missing required fields: {missing}")

    values: dict[str, str] = {}
    for field in _FIELDS:
        value = raw[field]
        if not isinstance(value, str) or not value.strip():
            raise ReasoningParseError(f"field {field!r} must be a non-empty string, got {value!r}")
        values[field] = value

    return ReasoningResult(**values)
