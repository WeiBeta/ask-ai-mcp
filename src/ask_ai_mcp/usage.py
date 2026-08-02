"""Concurrent, prompt-free SQLite audit storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from platformdirs import user_data_path

from ask_ai_mcp.models import UsageEvent, UsageSummary
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
                    candidate_hash TEXT
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
                    latency_ms, retries, status, candidate_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
