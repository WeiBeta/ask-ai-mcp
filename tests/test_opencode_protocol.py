"""Offline strict-schema and terminal-status tests for Responses routes."""

import pytest

from ask_ai_mcp.code_review_models import CodeReviewPayload
from ask_ai_mcp.opencode_pricing import OpenCodeGoModel
from ask_ai_mcp.opencode_protocol import (
    OpenCodeProviderResponseError,
    normalize_provider_response,
    request_body_for,
)


def _assert_strict_objects(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _assert_strict_objects(item)
        return
    if not isinstance(value, dict):
        return
    properties = value.get("properties")
    if value.get("type") == "object" and isinstance(properties, dict):
        assert value["additionalProperties"] is False
        assert value["required"] == list(properties)
    assert "default" not in value
    for item in value.values():
        _assert_strict_objects(item)


def test_responses_request_recursively_normalizes_strict_schema() -> None:
    body = request_body_for(
        OpenCodeGoModel.GROK_4_6,
        {
            "model": "grok-4.6",
            "messages": [],
            "reasoning_effort": "max",
            "max_tokens": 131_072,
        },
        schema=CodeReviewPayload.model_json_schema(),
        schema_name="review",
    )
    _assert_strict_objects(body["text"]["format"]["schema"])


@pytest.mark.parametrize("status", ["failed", "cancelled", "in_progress", "queued"])
def test_responses_terminal_or_nonfinal_status_is_not_misreported_as_length(status: str) -> None:
    with pytest.raises(OpenCodeProviderResponseError) as captured:
        normalize_provider_response(OpenCodeGoModel.GROK_4_6, {"status": status})
    assert captured.value.status == status


def test_responses_incomplete_status_remains_output_truncation_signal() -> None:
    normalized = normalize_provider_response(
        OpenCodeGoModel.GROK_4_6,
        {"status": "incomplete", "output_text": "{}", "usage": {}},
    )
    assert normalized["choices"][0]["finish_reason"] == "length"
