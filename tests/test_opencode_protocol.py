"""Offline strict-schema and terminal-status tests for Responses routes."""

import json

import pytest

from ask_ai_mcp.code_review_models import CodeReviewPayload
from ask_ai_mcp.opencode_pricing import OpenCodeGoModel
from ask_ai_mcp.opencode_protocol import (
    OpenCodeProviderResponseError,
    decode_provider_response,
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
    assert body["stream"] is True


def test_responses_sse_returns_only_the_terminal_response() -> None:
    completed = {
        "status": "completed",
        "output_text": "{}",
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }
    body = (
        "event: response.created\n"
        'data: {"type":"response.created","response":{"status":"in_progress"}}\n\n'
        "event: response.output_text.delta\n"
        'data: {"type":"response.output_text.delta","delta":"{}"}\n\n'
        "event: response.completed\n"
        f"data: {json.dumps({'type': 'response.completed', 'response': completed})}\n\n"
        "data: [DONE]\n\n"
    ).encode()

    assert decode_provider_response(OpenCodeGoModel.GROK_4_6, body) == completed


def test_responses_sse_requires_a_terminal_response() -> None:
    body = b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'

    with pytest.raises(ValueError, match="without a terminal response"):
        decode_provider_response(OpenCodeGoModel.GROK_4_6, body)


def test_responses_sse_preserves_terminal_failure_status() -> None:
    body = (
        b"event: response.failed\n"
        b'data: {"type":"response.failed","response":{"status":"failed"}}\n\n'
    )

    decoded = decode_provider_response(OpenCodeGoModel.GROK_4_6, body)
    with pytest.raises(OpenCodeProviderResponseError):
        normalize_provider_response(OpenCodeGoModel.GROK_4_6, decoded)


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
