"""Offline contract tests for the narrow DeepSeek V4 client."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ask_ai_mcp.deepseek import (
    DEEPSEEK_CHAT_COMPLETIONS_URL,
    DeepSeekClient,
    DeepSeekClientError,
    ModelEscalationRequired,
)
from ask_ai_mcp.models import (
    CandidateRepairFeedback,
    DeepSeekModel,
    PricingBand,
    ToolBuildSpec,
    ToolCategory,
)
from ask_ai_mcp.usage import UsageStore


def make_spec(model: DeepSeekModel = DeepSeekModel.FLASH) -> ToolBuildSpec:
    return ToolBuildSpec(
        name="extract_docx_tables",
        category=ToolCategory.DOCUMENT_PARSER,
        purpose="Extract table cells while preserving document source coordinates.",
        input_contract="Read-only copied DOCX fixtures inside the isolated job input.",
        output_contract="JSON tables with paragraph, table, row, and cell provenance.",
        acceptance_tests=["Original fixture hashes remain unchanged."],
        allowed_packages=["python-docx"],
        model=model,
    )


def api_response(*, path: str = "tool.py", finish_reason: str = "stop") -> dict[str, object]:
    candidate = {
        "summary": "A bounded DOCX table extractor and its tests.",
        "files": [
            {
                "path": path,
                "content": (
                    "def extract_tables(path):\n    return []\n\n"
                    "def run(request, input_dir, output_dir):\n    return []\n"
                ),
            },
            {"path": "test_tool.py", "content": "def test_placeholder():\n    assert True\n"},
        ],
        "risks": ["Merged-cell semantics require fixture coverage."],
    }
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": json.dumps(candidate),
                    "reasoning_content": "private reasoning must never be returned or logged",
                },
            }
        ],
        "usage": {
            "prompt_tokens": 300,
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 200,
            "completion_tokens": 80,
            "completion_tokens_details": {"reasoning_tokens": 50},
        },
    }


def test_flash_candidate_uses_thinking_json_and_records_prompt_free_usage(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DEEPSEEK_CHAT_COMPLETIONS_URL
        assert request.headers["Authorization"].startswith("Bearer sk-")
        body = json.loads(request.content)
        assert body["model"] == "deepseek-v4-flash"
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"
        assert body["response_format"] == {"type": "json_object"}
        assert "Return JSON only" in body["messages"][1]["content"]
        assert "test_*.py" in body["messages"][0]["content"]
        assert "tempfile.TemporaryDirectory" in body["messages"][0]["content"]
        return httpx.Response(200, json=api_response())

    store = UsageStore(tmp_path / "usage.db")
    client = DeepSeekClient(
        api_key_provider=lambda: "sk-" + "x" * 40,
        usage_store=store,
        transport=httpx.MockTransport(handler),
    )
    result = client.build_candidate(make_spec(), client_name="codex")

    assert result.model is DeepSeekModel.FLASH
    assert result.thinking_enabled is True
    assert result.payload.files[0].path == "tool.py"
    assert "private reasoning" not in result.model_dump_json()
    summary = store.summarize()
    assert summary.total_calls == 1
    assert summary.successful_calls == 1
    assert summary.prompt_cache_hit_tokens == 100
    assert summary.prompt_cache_miss_tokens == 200
    assert summary.completion_tokens == 80
    assert summary.reasoning_tokens == 50
    assert summary.by_client == {"codex": 1}


def test_pro_requires_explicit_host_escalation(tmp_path: Path) -> None:
    provider_called = False

    def api_key_provider() -> str:
        nonlocal provider_called
        provider_called = True
        return "sk-" + "x" * 40

    client = DeepSeekClient(
        api_key_provider=api_key_provider,
        usage_store=UsageStore(tmp_path / "usage.db"),
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    with pytest.raises(ModelEscalationRequired, match="explicit"):
        client.build_candidate(make_spec(DeepSeekModel.PRO), client_name="claude")
    assert provider_called is False


def test_pro_is_sent_only_after_explicit_escalation(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == "deepseek-v4-pro"
        return httpx.Response(200, json=api_response())

    client = DeepSeekClient(
        api_key_provider=lambda: "sk-" + "x" * 40,
        usage_store=UsageStore(tmp_path / "usage.db"),
        transport=httpx.MockTransport(handler),
    )
    result = client.build_candidate(
        make_spec(DeepSeekModel.PRO), client_name="codex", allow_pro=True
    )
    assert result.model is DeepSeekModel.PRO


def test_unsafe_candidate_path_is_rejected_and_counted(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=api_response(path="../escape.py"))
    )
    store = UsageStore(tmp_path / "usage.db")
    client = DeepSeekClient(
        api_key_provider=lambda: "sk-" + "x" * 40,
        usage_store=store,
        transport=transport,
    )

    with pytest.raises(DeepSeekClientError, match="invalid candidate"):
        client.build_candidate(make_spec(), client_name="codex")
    summary = store.summarize()
    assert summary.total_calls == 1
    assert summary.failed_calls == 1


def test_http_error_does_not_expose_response_body(tmp_path: Path) -> None:
    secret_body = "upstream-debug-secret-that-must-not-escape"
    transport = httpx.MockTransport(lambda _request: httpx.Response(401, text=secret_body))
    client = DeepSeekClient(
        api_key_provider=lambda: "sk-" + "x" * 40,
        usage_store=UsageStore(tmp_path / "usage.db"),
        transport=transport,
    )

    with pytest.raises(DeepSeekClientError) as captured:
        client.build_candidate(make_spec(), client_name="codex")
    assert "HTTP 401" in str(captured.value)
    assert secret_body not in str(captured.value)


def test_repair_uses_bounded_feedback_and_records_repair_round(tmp_path: Path) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        user_message = body["messages"][1]["content"]
        assert "Previous candidate JSON" in user_message
        assert "execution_exit_1" in user_message
        assert "source documents" in user_message
        assert "complete replacement candidate" in user_message
        assert "test_*.py" in body["messages"][0]["content"]
        return httpx.Response(200, json=api_response())

    database = tmp_path / "usage.db"
    client = DeepSeekClient(
        api_key_provider=lambda: "sk-" + "x" * 40,
        usage_store=UsageStore(database),
        transport=httpx.MockTransport(handler),
    )
    previous = client._parse_candidate(api_response())
    from ask_ai_mcp.hashing import candidate_payload_sha256
    from ask_ai_mcp.models import ToolCandidateResult

    result = client.repair_candidate(
        make_spec(),
        ToolCandidateResult(
            candidate_sha256=candidate_payload_sha256(previous),
            model=DeepSeekModel.FLASH,
            thinking_enabled=True,
            payload=previous,
        ),
        CandidateRepairFeedback(
            repair_round=1,
            reason_codes=["execution_exit_1"],
            diagnostic_excerpt="one synthetic assertion failed",
        ),
        client_name="codex",
    )

    assert result.candidate_sha256 == candidate_payload_sha256(result.payload)
    assert len(requests) == 1
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT task_kind, retries FROM api_usage").fetchone()
    assert row == ("tool_repair", 1)


def test_client_records_peak_price_metadata_from_request_start(tmp_path: Path) -> None:
    request_time = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    client = DeepSeekClient(
        api_key_provider=lambda: "sk-" + "x" * 40,
        usage_store=UsageStore(tmp_path / "usage.db"),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=api_response())),
        clock=lambda: request_time,
        peak_pricing_effective_at=request_time - timedelta(days=1),
    )

    client.build_candidate(make_spec(), client_name="claude_desktop")
    summary = client.usage_store.summarize()

    assert summary.by_pricing_band == {PricingBand.PEAK.value: 1}
    assert summary.estimated_cost_cny == 0.000724
