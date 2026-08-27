"""Offline protocol and accounting tests for fixed OpenCode Go adapters."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ask_ai_mcp.deepseek import DeepSeekClient
from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.models import (
    CandidateRepairFeedback,
    DeepSeekModel,
    ModelProvider,
    ToolBuildSpec,
    ToolCandidateResult,
    ToolCategory,
    UsageEvent,
)
from ask_ai_mcp.opencode import (
    OPENCODE_GO_CHAT_URL,
    OPENCODE_GO_RESPONSES_URL,
    OpenCodeGoClient,
)
from ask_ai_mcp.opencode_pricing import OpenCodeGoModel, calculate_opencode_go_cost
from ask_ai_mcp.opencode_source import (
    OPENCODE_GO_MESSAGES_URL,
    OpenCodeQwenMessagesClient,
)
from ask_ai_mcp.usage import UsageStore


def spec() -> ToolBuildSpec:
    return ToolBuildSpec(
        name="extract_tables",
        category=ToolCategory.DOCUMENT_PARSER,
        purpose="Extract bounded synthetic tables with provenance.",
        input_contract="Read-only staged files.",
        output_contract="JSON evidence.",
        acceptance_tests=["Synthetic coordinates remain stable."],
    )


def candidate() -> dict[str, object]:
    return {
        "summary": "Bounded extractor.",
        "files": [
            {
                "path": "tool.py",
                "content": "def run(request, input_dir, output_dir):\n    return {}\n",
            },
            {
                "path": "test_tool.py",
                "content": (
                    "import unittest\n\nclass ToolTest(unittest.TestCase):\n"
                    "    def test_run(self):\n        self.assertTrue(True)\n"
                ),
            },
        ],
        "risks": [],
    }


def test_build_uses_fixed_chat_endpoint_and_records_virtual_usd(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENCODE_GO_CHAT_URL
        assert request.headers["Authorization"] == "Bearer opaque-opencode-key-12345"
        body = json.loads(request.content)
        assert body["model"] == OpenCodeGoModel.DSV4_FLASH.value
        assert "_json_schema" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(candidate())}}
                ],
                "usage": {
                    "prompt_tokens": 1_000,
                    "prompt_tokens_details": {"cached_tokens": 100},
                    "completion_tokens": 200,
                },
            },
        )

    store = UsageStore(tmp_path / "usage.db")
    result = OpenCodeGoClient(
        api_key_provider=lambda: "opaque-opencode-key-12345",
        usage_store=store,
        transport=httpx.MockTransport(handler),
    ).build_candidate(spec(), client_name="codex")

    assert result.provider is ModelProvider.OPENCODE
    assert result.provider_model_id == OpenCodeGoModel.DSV4_FLASH.value
    assert result.provider_runtime == "chat_completions"
    summary = store.summarize()
    assert summary.by_provider == {"opencode": 1}
    assert summary.prompt_cache_hit_tokens == 100
    assert summary.prompt_cache_miss_tokens == 900
    assert summary.estimated_cost_usd > 0
    assert summary.opencode_go_accounts[0].account == "injected-test"


def test_semantic_repair_uses_luna_responses_and_normalizes_output(tmp_path: Path) -> None:
    payload = DeepSeekClient._parse_candidate(
        {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(candidate())}}]}
    )
    previous = ToolCandidateResult(
        candidate_sha256=candidate_payload_sha256(payload),
        model=DeepSeekModel.FLASH,
        thinking_enabled=True,
        payload=payload,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENCODE_GO_RESPONSES_URL
        body = json.loads(request.content)
        assert body["model"] == OpenCodeGoModel.GPT_5_6_LUNA.value
        assert "input" in body and "messages" not in body
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(candidate())}],
                    }
                ],
                "usage": {
                    "input_tokens": 500,
                    "input_tokens_details": {"cached_tokens": 100},
                    "output_tokens": 80,
                    "output_tokens_details": {"reasoning_tokens": 20},
                },
            },
        )

    result = OpenCodeGoClient(
        api_key_provider=lambda: "opaque-opencode-key-12345",
        usage_store=UsageStore(tmp_path / "usage.db"),
        transport=httpx.MockTransport(handler),
    ).repair_candidate(
        spec(),
        previous,
        CandidateRepairFeedback(repair_round=1, reason_codes=["semantic_failure"]),
        client_name="codex",
    )

    assert result.provider_model_id == OpenCodeGoModel.GPT_5_6_LUNA.value
    assert result.provider_runtime == "responses"


def test_qwen_multimodal_request_uses_messages_protocol_and_cache_accounting(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == OPENCODE_GO_MESSAGES_URL
        assert request.headers["x-api-key"] == "opaque-opencode-key-12345"
        body = json.loads(request.content)
        image = body["messages"][0]["content"][0]
        assert image == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "YWJj"},
        }
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": '{"records": []}'}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 1_000,
                    "output_tokens": 100,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 50,
                },
            },
        )

    store = UsageStore(tmp_path / "usage.db")
    response = OpenCodeQwenMessagesClient(
        api_key_provider=lambda: "opaque-opencode-key-12345",
        usage_store=store,
        transport=httpx.MockTransport(handler),
    ).chat(
        {
            "model": OpenCodeGoModel.QWEN_3_8_MAX.value,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}},
                        {"type": "text", "text": "Extract evidence."},
                    ],
                }
            ],
            "max_tokens": 1_024,
        },
        vision=True,
    )

    assert response["choices"][0]["message"]["content"] == '{"records": []}'
    summary = store.summarize()
    assert summary.estimated_cost_usd == calculate_opencode_go_cost(
        OpenCodeGoModel.QWEN_3_8_MAX,
        input_tokens=1_250,
        output_tokens=100,
        cache_read_tokens=200,
        cache_write_tokens=50,
    )
    assert summary.estimated_cost_usd_by_provider_model == {
        "qwen3.8-max": summary.estimated_cost_usd
    }


def test_local_ledger_blocks_an_exhausted_account_without_cross_account_rotation(
    tmp_path: Path,
) -> None:
    store = UsageStore(tmp_path / "usage.db")
    store.record(
        UsageEvent(
            client_name="codex",
            task_kind="tool_build",
            model=DeepSeekModel.FLASH,
            provider=ModelProvider.OPENCODE,
            provider_model_id=OpenCodeGoModel.DSV4_FLASH.value,
            provider_runtime="chat_completions",
            provider_account="primary",
            thinking_enabled=True,
            status="success",
            estimated_cost_usd=12.0,
        )
    )

    assert "5-hour" in (
        store.opencode_limit_reason("primary", OpenCodeGoModel.DSV4_FLASH.value) or ""
    )
    assert store.opencode_limit_reason("secondary", OpenCodeGoModel.DSV4_FLASH.value) is None
