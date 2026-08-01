"""Tests for concurrent-safe, content-free usage accounting."""

from pathlib import Path

from ask_ai_mcp.models import DeepSeekModel, UsageEvent
from ask_ai_mcp.usage import UsageStore


def test_empty_usage_store(tmp_path: Path) -> None:
    summary = UsageStore(tmp_path / "usage.db").summarize(days=15)
    assert summary.total_calls == 0
    assert summary.estimated_cost_cny == 0
    assert summary.by_model == {}


def test_usage_store_aggregates_without_prompt_content(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.db")
    store.record(
        UsageEvent(
            client_name="codex",
            task_kind="build_helper_tool",
            model=DeepSeekModel.FLASH,
            thinking_enabled=True,
            prompt_cache_hit_tokens=100,
            prompt_cache_miss_tokens=200,
            completion_tokens=50,
            reasoning_tokens=20,
            estimated_cost_cny=0.001,
            latency_ms=1_500,
            retries=0,
            status="success",
        )
    )
    store.record(
        UsageEvent(
            client_name="claude",
            task_kind="repair_tool_candidate",
            model=DeepSeekModel.PRO,
            thinking_enabled=True,
            status="failed",
        )
    )

    summary = store.summarize(days=15)
    assert summary.total_calls == 2
    assert summary.successful_calls == 1
    assert summary.failed_calls == 1
    assert summary.prompt_cache_hit_tokens == 100
    assert summary.prompt_cache_miss_tokens == 200
    assert summary.completion_tokens == 50
    assert summary.reasoning_tokens == 20
    assert summary.estimated_cost_cny == 0.001
    assert summary.by_model == {
        "deepseek-v4-flash": 1,
        "deepseek-v4-pro": 1,
    }
