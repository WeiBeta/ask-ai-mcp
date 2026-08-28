"""Provider-neutral request and response adapters for fixed OpenCode Go routes."""

from __future__ import annotations

import json
from typing import Any

from ask_ai_mcp.opencode_pricing import (
    OPENCODE_GO_PRICES,
    OpenCodeGoModel,
    OpenCodeGoProtocol,
)

OPENCODE_GO_CHAT_URL = "https://opencode.ai/zen/go/v1/chat/completions"
OPENCODE_GO_RESPONSES_URL = "https://opencode.ai/zen/go/v1/responses"


class OpenCodeProviderResponseError(RuntimeError):
    """A content-free terminal provider status returned over successful HTTP."""

    def __init__(self, status: str) -> None:
        super().__init__("OpenCode Go returned a terminal provider failure status")
        self.status = status[:64]


def strict_response_schema(value: object) -> object:
    """Normalize Pydantic JSON Schema to the Responses strict subset."""

    if isinstance(value, list):
        return [strict_response_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {
        key: strict_response_schema(item) for key, item in value.items() if key != "default"
    }
    properties = normalized.get("properties")
    if normalized.get("type") == "object" and isinstance(properties, dict):
        normalized["additionalProperties"] = False
        normalized["required"] = list(properties)
    return normalized


def provider_protocol(model: OpenCodeGoModel) -> OpenCodeGoProtocol:
    return OPENCODE_GO_PRICES[model].protocol


def endpoint_for(model: OpenCodeGoModel) -> str:
    protocol = provider_protocol(model)
    if protocol is OpenCodeGoProtocol.RESPONSES:
        return OPENCODE_GO_RESPONSES_URL
    if protocol is OpenCodeGoProtocol.CHAT_COMPLETIONS:
        return OPENCODE_GO_CHAT_URL
    raise ValueError(f"unsupported text-generation protocol: {protocol.value}")


def request_body_for(
    model: OpenCodeGoModel,
    chat_body: dict[str, Any],
    *,
    schema: dict[str, Any],
    schema_name: str,
) -> dict[str, Any]:
    if provider_protocol(model) is OpenCodeGoProtocol.CHAT_COMPLETIONS:
        return chat_body
    body = {
        "model": model.value,
        "input": chat_body["messages"],
        "reasoning": {"effort": chat_body["reasoning_effort"]},
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": strict_response_schema(schema),
            }
        },
        "max_output_tokens": chat_body["max_tokens"],
        "store": False,
    }
    if model is OpenCodeGoModel.GROK_4_6:
        # Start an SSE response promptly so a slow reasoning pass does not sit
        # behind an upstream non-streaming response-header timeout.
        body["stream"] = True
    return body


def decode_provider_response(model: OpenCodeGoModel, response_body: bytes) -> dict[str, Any]:
    """Decode one JSON response or one complete Responses SSE transcript."""

    stripped = response_body.lstrip()
    if not stripped.startswith((b"data:", b"event:")):
        value = json.loads(response_body)
        if not isinstance(value, dict):
            raise ValueError("OpenCode Go returned a non-object response")
        return value
    if provider_protocol(model) is not OpenCodeGoProtocol.RESPONSES:
        raise ValueError("unexpected SSE response for a non-Responses route")

    final_response: dict[str, Any] | None = None
    event_name: str | None = None
    data_lines: list[str] = []

    def consume_event() -> None:
        nonlocal final_response, event_name, data_lines
        if not data_lines:
            event_name = None
            return
        data = "\n".join(data_lines)
        data_lines = []
        if data == "[DONE]":
            event_name = None
            return
        event = json.loads(data)
        if not isinstance(event, dict):
            raise ValueError("OpenCode Go returned a non-object SSE event")
        selected_type = event_name or event.get("type")
        if selected_type in {
            "response.completed",
            "response.failed",
            "response.incomplete",
        }:
            response = event.get("response")
            if not isinstance(response, dict):
                raise ValueError("Responses SSE terminal event is missing its response object")
            final_response = response
        event_name = None

    text = response_body.decode("utf-8")
    for line in text.splitlines():
        if not line:
            consume_event()
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line.startswith(":"):
            raise ValueError("OpenCode Go returned malformed SSE framing")
    consume_event()
    if final_response is None:
        raise ValueError("Responses SSE stream ended without a terminal response")
    return final_response


def normalize_provider_response(model: OpenCodeGoModel, data: dict[str, Any]) -> dict[str, Any]:
    if provider_protocol(model) is OpenCodeGoProtocol.CHAT_COMPLETIONS:
        return data
    status = data.get("status")
    if status not in (None, "completed", "incomplete"):
        raise OpenCodeProviderResponseError(str(status))
    text = data.get("output_text")
    if not isinstance(text, str):
        text = ""
        for item in data.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    value = content.get("text")
                    if isinstance(value, str):
                        text += value
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    input_details = usage.get("input_tokens_details", {})
    output_details = usage.get("output_tokens_details", {})
    cached = input_details.get("cached_tokens", 0) if isinstance(input_details, dict) else 0
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    return {
        "choices": [
            {
                "finish_reason": ("stop" if status in (None, "completed") else "length"),
                "message": {"content": text},
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "prompt_cache_hit_tokens": int(cached or 0),
            "prompt_cache_miss_tokens": max(0, input_tokens - int(cached or 0)),
            "prompt_tokens_details": {"cached_tokens": int(cached or 0)},
            "completion_tokens": int(usage.get("output_tokens", 0) or 0),
            "completion_tokens_details": {
                "reasoning_tokens": (
                    int(output_details.get("reasoning_tokens", 0) or 0)
                    if isinstance(output_details, dict)
                    else 0
                )
            },
        },
    }
