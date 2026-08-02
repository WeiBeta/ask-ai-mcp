"""Concurrent, prompt-free SQLite audit storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from platformdirs import user_data_path

from ask_ai_mcp.models import LifecycleAuditEvent, UsageEvent, UsageSummary
from ask_ai_mcp.pricing import load_peak_pricing_effective_at, pricing_context


def default_usage_db_path() -> Path:
    """Return the standard per-user state path on Windows."""
    return user_data_path("AskAIMCP", appauthor=False, ensure_exists=True) / "usage.db"


class UsageStore:
    """Small SQLite repository that is safe for two desktop MCP processes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_usage_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    client_name TEXT NOT NULL,
                    task_kind TEXT NOT NULL,
                    model TEXT NOT NULL,
                    thinking_enabled INTEGER NOT NULL,
                    priced_at TEXT NOT NULL,
                    pricing_band TEXT NOT NULL,
                    pricing_multiplier REAL NOT NULL,
                    pricing_schedule_version TEXT NOT NULL,
                    cache_hit_price_cny_per_million REAL NOT NULL,
                    cache_miss_price_cny_per_million REAL NOT NULL,
                    output_price_cny_per_million REAL NOT NULL,
                    prompt_cache_hit_tokens INTEGER NOT NULL,
                    prompt_cache_miss_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    estimated_cost_cny REAL NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    retries INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    candidate_hash TEXT,
                    budget_session_id TEXT,
                    lifecycle_id TEXT,
                    request_chars INTEGER NOT NULL DEFAULT 0,
                    response_chars INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            existing_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(api_usage)").fetchall()
            }
            migrations = {
                "priced_at": "TEXT",
                "pricing_band": "TEXT NOT NULL DEFAULT 'standard'",
                "pricing_multiplier": "REAL NOT NULL DEFAULT 1.0",
                "pricing_schedule_version": "TEXT NOT NULL DEFAULT 'legacy_base'",
                "cache_hit_price_cny_per_million": "REAL NOT NULL DEFAULT 0.0",
                "cache_miss_price_cny_per_million": "REAL NOT NULL DEFAULT 0.0",
                "output_price_cny_per_million": "REAL NOT NULL DEFAULT 0.0",
                "budget_session_id": "TEXT",
                "lifecycle_id": "TEXT",
                "request_chars": "INTEGER NOT NULL DEFAULT 0",
                "response_chars": "INTEGER NOT NULL DEFAULT 0",
            }
            for column_name, definition in migrations.items():
                if column_name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE api_usage ADD COLUMN {column_name} {definition}"
                    )
            connection.execute("UPDATE api_usage SET priced_at = timestamp WHERE priced_at IS NULL")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_timestamp ON api_usage(timestamp)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_client ON api_usage(client_name)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_pricing_band ON api_usage(pricing_band)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_budget_session "
                "ON api_usage(budget_session_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_lifecycle ON api_usage(lifecycle_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lifecycle_audit (
                    lifecycle_id TEXT PRIMARY KEY,
                    budget_session_id TEXT NOT NULL,
                    client_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    spec_sha256 TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    spec_total_chars INTEGER NOT NULL,
                    purpose_chars INTEGER NOT NULL,
                    input_contract_chars INTEGER NOT NULL,
                    output_contract_chars INTEGER NOT NULL,
                    fixture_notes_chars INTEGER NOT NULL,
                    acceptance_tests_chars INTEGER NOT NULL,
                    candidate_source_chars INTEGER NOT NULL,
                    candidate_test_chars INTEGER NOT NULL,
                    candidate_file_count INTEGER NOT NULL,
                    review_summary_chars INTEGER NOT NULL,
                    patch_chars INTEGER NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    repair_count INTEGER NOT NULL,
                    final_job_id TEXT,
                    final_candidate_sha256 TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_lifecycle_budget_session "
                "ON lifecycle_audit(budget_session_id)"
            )

    def record(self, event: UsageEvent) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO api_usage (
                    timestamp, client_name, task_kind, model, thinking_enabled,
                    priced_at, pricing_band, pricing_multiplier,
                    pricing_schedule_version,
                    cache_hit_price_cny_per_million,
                    cache_miss_price_cny_per_million,
                    output_price_cny_per_million,
                    prompt_cache_hit_tokens, prompt_cache_miss_tokens,
                    completion_tokens, reasoning_tokens, estimated_cost_cny,
                    latency_ms, retries, status, candidate_hash,
                    budget_session_id, lifecycle_id, request_chars, response_chars
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp.astimezone(UTC).isoformat(),
                    event.client_name,
                    event.task_kind,
                    event.model.value,
                    int(event.thinking_enabled),
                    event.priced_at.astimezone(UTC).isoformat(),
                    event.pricing_band.value,
                    event.pricing_multiplier,
                    event.pricing_schedule_version,
                    event.cache_hit_price_cny_per_million,
                    event.cache_miss_price_cny_per_million,
                    event.output_price_cny_per_million,
                    event.prompt_cache_hit_tokens,
                    event.prompt_cache_miss_tokens,
                    event.completion_tokens,
                    event.reasoning_tokens,
                    event.estimated_cost_cny,
                    event.latency_ms,
                    event.retries,
                    event.status,
                    event.candidate_hash,
                    event.budget_session_id,
                    event.lifecycle_id,
                    event.request_chars,
                    event.response_chars,
                ),
            )

    def record_lifecycle(self, event: LifecycleAuditEvent) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO lifecycle_audit (
                    lifecycle_id, budget_session_id, client_name, model, tool_name,
                    spec_sha256, started_at, completed_at, status,
                    spec_total_chars, purpose_chars, input_contract_chars,
                    output_contract_chars, fixture_notes_chars,
                    acceptance_tests_chars, candidate_source_chars,
                    candidate_test_chars, candidate_file_count,
                    review_summary_chars, patch_chars, attempt_count, repair_count,
                    final_job_id, final_candidate_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.lifecycle_id,
                    event.budget_session_id,
                    event.client_name,
                    event.model.value,
                    event.tool_name,
                    event.spec_sha256,
                    event.started_at.astimezone(UTC).isoformat(),
                    event.completed_at.astimezone(UTC).isoformat(),
                    event.status.value,
                    event.spec_total_chars,
                    event.purpose_chars,
                    event.input_contract_chars,
                    event.output_contract_chars,
                    event.fixture_notes_chars,
                    event.acceptance_tests_chars,
                    event.candidate_source_chars,
                    event.candidate_test_chars,
                    event.candidate_file_count,
                    event.review_summary_chars,
                    event.patch_chars,
                    event.attempt_count,
                    event.repair_count,
                    event.final_job_id,
                    event.final_candidate_sha256,
                ),
            )

    def summarize(self, days: int = 15) -> UsageSummary:
        if not 1 <= days <= 366:
            raise ValueError("days must be between 1 and 366")
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

        with self._connection() as connection:
            totals = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_calls,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)
                        AS successful_calls,
                    SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END)
                        AS failed_calls,
                    COALESCE(SUM(prompt_cache_hit_tokens), 0) AS cache_hit,
                    COALESCE(SUM(prompt_cache_miss_tokens), 0) AS cache_miss,
                    COALESCE(SUM(completion_tokens), 0) AS completion,
                    COALESCE(SUM(reasoning_tokens), 0) AS reasoning,
                    COALESCE(SUM(estimated_cost_cny), 0) AS cost
                FROM api_usage
                WHERE timestamp >= ?
                """,
                (cutoff,),
            ).fetchone()
            model_rows = connection.execute(
                """
                SELECT model, COUNT(*) AS count
                FROM api_usage
                WHERE timestamp >= ?
                GROUP BY model
                ORDER BY model
                """,
                (cutoff,),
            ).fetchall()
            client_rows = connection.execute(
                """
                SELECT client_name AS key, COUNT(*) AS count,
                       COALESCE(SUM(estimated_cost_cny), 0) AS cost
                FROM api_usage
                WHERE timestamp >= ?
                GROUP BY client_name
                ORDER BY client_name
                """,
                (cutoff,),
            ).fetchall()
            pricing_band_rows = connection.execute(
                """
                SELECT COALESCE(pricing_band, 'standard') AS key,
                       COUNT(*) AS count,
                       COALESCE(SUM(estimated_cost_cny), 0) AS cost
                FROM api_usage
                WHERE timestamp >= ?
                GROUP BY COALESCE(pricing_band, 'standard')
                ORDER BY key
                """,
                (cutoff,),
            ).fetchall()
            model_cost_rows = connection.execute(
                """
                SELECT model AS key, COALESCE(SUM(estimated_cost_cny), 0) AS cost
                FROM api_usage
                WHERE timestamp >= ?
                GROUP BY model
                ORDER BY model
                """,
                (cutoff,),
            ).fetchall()

        now_utc = datetime.now(UTC)
        effective_at = load_peak_pricing_effective_at()
        beijing_time, current_band, _, schedule_version, peak_enabled = pricing_context(
            now_utc,
            peak_pricing_effective_at=effective_at,
        )

        return UsageSummary(
            days=days,
            total_calls=int(totals["total_calls"] or 0),
            successful_calls=int(totals["successful_calls"] or 0),
            failed_calls=int(totals["failed_calls"] or 0),
            prompt_cache_hit_tokens=int(totals["cache_hit"]),
            prompt_cache_miss_tokens=int(totals["cache_miss"]),
            completion_tokens=int(totals["completion"]),
            reasoning_tokens=int(totals["reasoning"]),
            estimated_cost_cny=round(float(totals["cost"]), 8),
            by_model={str(row["model"]): int(row["count"]) for row in model_rows},
            by_client={str(row["key"]): int(row["count"]) for row in client_rows},
            by_pricing_band={str(row["key"]): int(row["count"]) for row in pricing_band_rows},
            estimated_cost_cny_by_model={
                str(row["key"]): round(float(row["cost"]), 8) for row in model_cost_rows
            },
            estimated_cost_cny_by_client={
                str(row["key"]): round(float(row["cost"]), 8) for row in client_rows
            },
            estimated_cost_cny_by_pricing_band={
                str(row["key"]): round(float(row["cost"]), 8) for row in pricing_band_rows
            },
            current_pricing_band=current_band,
            peak_pricing_enabled=peak_enabled,
            pricing_schedule_version=schedule_version,
            current_beijing_time=beijing_time,
        )
