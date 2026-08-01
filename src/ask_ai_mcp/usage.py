"""Concurrent, prompt-free SQLite audit storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from platformdirs import user_data_path

from ask_ai_mcp.models import UsageEvent, UsageSummary


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
                "CREATE INDEX IF NOT EXISTS idx_api_usage_timestamp ON api_usage(timestamp)"
            )

    def record(self, event: UsageEvent) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO api_usage (
                    timestamp, client_name, task_kind, model, thinking_enabled,
                    prompt_cache_hit_tokens, prompt_cache_miss_tokens,
                    completion_tokens, reasoning_tokens, estimated_cost_cny,
                    latency_ms, retries, status, candidate_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp.astimezone(UTC).isoformat(),
                    event.client_name,
                    event.task_kind,
                    event.model.value,
                    int(event.thinking_enabled),
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
        )
