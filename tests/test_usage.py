"""Tests for concurrent-safe, content-free usage accounting."""

from pathlib import Path

from ask_ai_mcp.models import DeepSeekModel, PricingBand, UsageEvent
from ask_ai_mcp.usage import UsageStore


def test_empty_usage_store(tmp_path: Path) -> None:
    summary = UsageStore(tmp_path / "usage.db").summarize(days=15)
    assert summary.total_calls == 0
    assert summary.estimated_cost_cny == 0
    assert summary.by_model == {}
    assert summary.by_client == {}
    assert summary.by_pricing_band == {}


def test_usage_store_aggregates_without_prompt_content(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.db")
    store.record(
        UsageEvent(
            client_name="codex",
            task_kind="build_helper_tool",
            model=DeepSeekModel.FLASH,
            thinking_enabled=True,
            pricing_band=PricingBand.STANDARD,
            pricing_schedule_version="test-base",
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
            pricing_band=PricingBand.PEAK,
            pricing_multiplier=2.0,
            pricing_schedule_version="test-peak",
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
    assert summary.by_client == {"claude": 1, "codex": 1}
    assert summary.by_pricing_band == {"peak": 1, "standard": 1}
    assert summary.estimated_cost_cny_by_model == {
        "deepseek-v4-flash": 0.001,
        "deepseek-v4-pro": 0.0,
    }
    assert summary.estimated_cost_cny_by_client == {"claude": 0.0, "codex": 0.001}
    assert summary.estimated_cost_cny_by_pricing_band == {
        "peak": 0.0,
        "standard": 0.001,
    }


def test_existing_usage_database_is_migrated_without_losing_history(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE api_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                client_name TEXT NOT NULL,
                task_kind TEXT NOT NULL,
                model TEXT NOT NULL,
                thinking_enabled INTEGER NOT NULL,
                prompt_cache_hit_tokens INTEGER NOT NULL,
                prompt_cache_miss_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                reasoning_tokens INTEGER NOT NULL,
                estimated_cost_cny REAL NOT NULL,
                latency_ms INTEGER NOT NULL,
                retries INTEGER NOT NULL,
                status TEXT NOT NULL,
                candidate_hash TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO api_usage VALUES (
                1, '2026-08-02T00:00:00+00:00', 'codex_desktop', 'tool_build',
                'deepseek-v4-flash', 1, 0, 100, 20, 10, 0.00014, 1000, 0,
                'success', NULL
            )
            """
        )

    summary = UsageStore(database).summarize(days=15)

    assert summary.total_calls == 1
    assert summary.by_client == {"codex_desktop": 1}
    assert summary.by_pricing_band == {"standard": 1}
