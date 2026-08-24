"""Tests for reason/schema.py."""

from __future__ import annotations

import pytest

from reason.schema import (
    JSON_SCHEMA,
    ReasoningParseError,
    ReasoningResult,
    parse_reasoning_response,
)

_VALID = {
    "what": "A linear reflector consistent with a buried utility line.",
    "where": "Approximately 0.5m deep, position unavailable.",
    "why": "High detector confidence and consistent hyperbola width.",
    "how": "Moderate confidence; depth is estimated, not calibrated.",
    "recommended_action": "Confirm with a second survey pass at this line.",
}


def test_parses_valid_response() -> None:
    result = parse_reasoning_response(_VALID)
    assert isinstance(result, ReasoningResult)
    assert result.what == _VALID["what"]
    assert result.recommended_action == _VALID["recommended_action"]


def test_non_dict_raises() -> None:
    with pytest.raises(ReasoningParseError, match="JSON object"):
        parse_reasoning_response(["not", "a", "dict"])


def test_missing_field_raises() -> None:
    incomplete = dict(_VALID)
    del incomplete["why"]
    with pytest.raises(ReasoningParseError, match="why"):
        parse_reasoning_response(incomplete)


def test_non_string_field_raises() -> None:
    bad = dict(_VALID)
    bad["what"] = 42
    with pytest.raises(ReasoningParseError, match="what"):
        parse_reasoning_response(bad)


def test_empty_string_field_raises() -> None:
    bad = dict(_VALID)
    bad["how"] = "   "
    with pytest.raises(ReasoningParseError, match="how"):
        parse_reasoning_response(bad)


def test_json_schema_requires_all_five_fields_strictly() -> None:
    assert set(JSON_SCHEMA["required"]) == {"what", "where", "why", "how", "recommended_action"}
    assert JSON_SCHEMA["additionalProperties"] is False
    assert set(JSON_SCHEMA["properties"]) == set(JSON_SCHEMA["required"])
