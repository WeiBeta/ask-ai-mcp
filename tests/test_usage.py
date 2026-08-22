"""Tests for concurrent-safe, content-free usage accounting."""

from datetime import UTC, datetime
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
    timestamp = datetime.now(UTC).isoformat()
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
                1, ?, 'codex_desktop', 'tool_build',
                'deepseek-v4-flash', 1, 0, 100, 20, 10, 0.00014, 1000, 0,
                'success', NULL
            )
            """,
            (timestamp,),
        )

    summary = UsageStore(database).summarize(days=15)

    assert summary.total_calls == 1
    assert summary.by_client == {"codex_desktop": 1}
    assert summary.by_pricing_band == {"standard": 1}
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(api_usage)")}
        migrated = connection.execute(
            "SELECT provider_subscription_id, provider_reported_cost_usd, cost_source, "
            "pricing_schedule_version FROM api_usage WHERE id = 1"
        ).fetchone()
    assert {
        "provider_subscription_id",
        "provider_reported_cost_usd",
        "cost_source",
    }.issubset(columns)
    assert migrated == (None, None, "local_estimate", "legacy_base")


def test_v040_lifecycle_metrics_are_migrated_without_fake_byte_values(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "legacy-lifecycle.db"
    now = datetime.now(UTC).isoformat()
    lifecycle_id = "11111111-1111-4111-8111-111111111111"
    job_id = "22222222-2222-4222-8222-222222222222"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE lifecycle_audit (
                lifecycle_id TEXT PRIMARY KEY, budget_session_id TEXT NOT NULL,
                client_name TEXT NOT NULL, model TEXT NOT NULL, tool_name TEXT NOT NULL,
                spec_sha256 TEXT NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL, status TEXT NOT NULL,
                spec_total_chars INTEGER NOT NULL, purpose_chars INTEGER NOT NULL,
                input_contract_chars INTEGER NOT NULL, output_contract_chars INTEGER NOT NULL,
                fixture_notes_chars INTEGER NOT NULL, acceptance_tests_chars INTEGER NOT NULL,
                candidate_source_chars INTEGER NOT NULL, candidate_test_chars INTEGER NOT NULL,
                candidate_file_count INTEGER NOT NULL, review_summary_chars INTEGER NOT NULL,
                patch_chars INTEGER NOT NULL, attempt_count INTEGER NOT NULL,
                repair_count INTEGER NOT NULL, final_job_id TEXT,
                final_candidate_sha256 TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO lifecycle_audit VALUES (
                ?, ?, 'codex_desktop', 'deepseek-v4-flash', 'legacy_tool', ?, ?, ?,
                'review_pending', 500, 100, 120, 130, 0, 50, 900, 300, 2,
                250, 1500, 1, 0, ?, ?
            )
            """,
            (
                lifecycle_id,
                "33333333-3333-4333-8333-333333333333",
                "a" * 64,
                now,
                now,
                job_id,
                "b" * 64,
            ),
        )

    store = UsageStore(database)
    economics = store.summarize(days=15).recent_lifecycle_economics[0]

    assert economics.lifecycle_id == lifecycle_id
    assert economics.spec_total_chars == 500
    assert economics.spec_total_bytes == 0
    assert economics.structural_bytes_available is False
    assert economics.spec_to_candidate_total_bytes_ratio is None
    assert store.client_for_job(job_id) == "codex_desktop"
