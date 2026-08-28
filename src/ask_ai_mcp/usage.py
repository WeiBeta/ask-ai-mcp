"""Concurrent, prompt-free SQLite audit storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from platformdirs import user_data_path

from ask_ai_mcp.models import (
    LifecycleAuditEvent,
    LifecycleEconomics,
    OpenCodeGoAccountUsage,
    OpenCodeGoLimitWindow,
    OpenCodeGoModelAllowance,
    UsageEvent,
    UsageSummary,
)
from ask_ai_mcp.opencode_pricing import (
    OPENCODE_GO_LIMITS_USD,
    OPENCODE_GO_PRICES,
    OPENCODE_GO_PRICING_EFFECTIVE_AT,
    OPENCODE_GO_PRICING_SOURCE_URL,
    OPENCODE_GO_PRICING_VERSION,
)
from ask_ai_mcp.pricing import load_peak_pricing_effective_at, pricing_context
from ask_ai_mcp.protocol_audit import ProtocolAuditEvent


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
                    provider TEXT NOT NULL DEFAULT 'deepseek',
                    provider_model_id TEXT,
                    provider_runtime TEXT,
                    provider_account TEXT,
                    provider_subscription_id TEXT,
                    thinking_enabled INTEGER NOT NULL,
                    reasoning_effort TEXT,
                    max_output_tokens INTEGER,
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
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
                    provider_reported_cost_usd REAL,
                    cost_source TEXT NOT NULL DEFAULT 'local_estimate',
                    latency_ms INTEGER NOT NULL,
                    retries INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    candidate_hash TEXT,
                    budget_session_id TEXT,
                    lifecycle_id TEXT,
                    request_chars INTEGER NOT NULL DEFAULT 0,
                    response_chars INTEGER NOT NULL DEFAULT 0,
                    attribution_uid TEXT,
                    usage_observation_scope TEXT NOT NULL DEFAULT 'provider_response'
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
                "provider": "TEXT NOT NULL DEFAULT 'deepseek'",
                "provider_model_id": "TEXT",
                "provider_runtime": "TEXT",
                "provider_account": "TEXT",
                "provider_subscription_id": "TEXT",
                "reasoning_effort": "TEXT",
                "max_output_tokens": "INTEGER",
                "cache_read_tokens": "INTEGER NOT NULL DEFAULT 0",
                "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
                "estimated_cost_usd": "REAL NOT NULL DEFAULT 0.0",
                "provider_reported_cost_usd": "REAL",
                "cost_source": "TEXT NOT NULL DEFAULT 'local_estimate'",
                "attribution_uid": "TEXT",
                "usage_observation_scope": "TEXT NOT NULL DEFAULT 'provider_response'",
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
                "CREATE INDEX IF NOT EXISTS idx_api_usage_provider ON api_usage(provider)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_usage_attribution_uid "
                "ON api_usage(attribution_uid) WHERE attribution_uid IS NOT NULL"
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
                    spec_total_bytes INTEGER NOT NULL DEFAULT 0,
                    purpose_bytes INTEGER NOT NULL DEFAULT 0,
                    input_contract_bytes INTEGER NOT NULL DEFAULT 0,
                    output_contract_bytes INTEGER NOT NULL DEFAULT 0,
                    fixture_notes_bytes INTEGER NOT NULL DEFAULT 0,
                    acceptance_tests_bytes INTEGER NOT NULL DEFAULT 0,
                    candidate_source_chars INTEGER NOT NULL,
                    candidate_test_chars INTEGER NOT NULL,
                    candidate_source_bytes INTEGER NOT NULL DEFAULT 0,
                    candidate_test_bytes INTEGER NOT NULL DEFAULT 0,
                    candidate_file_count INTEGER NOT NULL,
                    review_summary_chars INTEGER NOT NULL,
                    patch_chars INTEGER NOT NULL,
                    review_summary_bytes INTEGER NOT NULL DEFAULT 0,
                    patch_bytes INTEGER NOT NULL DEFAULT 0,
                    attempt_count INTEGER NOT NULL,
                    repair_count INTEGER NOT NULL,
                    final_job_id TEXT,
                    final_candidate_sha256 TEXT
                )
                """
            )
            lifecycle_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(lifecycle_audit)").fetchall()
            }
            lifecycle_migrations = {
                "spec_total_bytes": "INTEGER NOT NULL DEFAULT 0",
                "purpose_bytes": "INTEGER NOT NULL DEFAULT 0",
                "input_contract_bytes": "INTEGER NOT NULL DEFAULT 0",
                "output_contract_bytes": "INTEGER NOT NULL DEFAULT 0",
                "fixture_notes_bytes": "INTEGER NOT NULL DEFAULT 0",
                "acceptance_tests_bytes": "INTEGER NOT NULL DEFAULT 0",
                "candidate_source_bytes": "INTEGER NOT NULL DEFAULT 0",
                "candidate_test_bytes": "INTEGER NOT NULL DEFAULT 0",
                "review_summary_bytes": "INTEGER NOT NULL DEFAULT 0",
                "patch_bytes": "INTEGER NOT NULL DEFAULT 0",
            }
            for column_name, definition in lifecycle_migrations.items():
                if column_name not in lifecycle_columns:
                    connection.execute(
                        f"ALTER TABLE lifecycle_audit ADD COLUMN {column_name} {definition}"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_lifecycle_budget_session "
                "ON lifecycle_audit(budget_session_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_protocol_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    process_instance_id TEXT NOT NULL,
                    session_id TEXT,
                    request_id TEXT,
                    configured_client_name TEXT,
                    reported_client_name TEXT,
                    client_version TEXT,
                    protocol_version TEXT,
                    method TEXT NOT NULL,
                    tool_name TEXT,
                    duration_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error_kind TEXT,
                    tool_count INTEGER,
                    tool_surface_bytes INTEGER,
                    tool_surface_sha256 TEXT,
                    arguments_bytes INTEGER,
                    result_bytes INTEGER
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_mcp_protocol_timestamp "
                "ON mcp_protocol_events(timestamp)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_mcp_protocol_session "
                "ON mcp_protocol_events(session_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_mcp_protocol_method ON mcp_protocol_events(method)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_mcp_protocol_tool ON mcp_protocol_events(tool_name)"
            )

    def record(self, event: UsageEvent) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO api_usage (
                    timestamp, client_name, task_kind, model, provider,
                    provider_model_id, provider_runtime, provider_account,
                    provider_subscription_id, thinking_enabled,
                    reasoning_effort, max_output_tokens,
                    priced_at, pricing_band, pricing_multiplier,
                    pricing_schedule_version,
                    cache_hit_price_cny_per_million,
                    cache_miss_price_cny_per_million,
                    output_price_cny_per_million,
                    prompt_cache_hit_tokens, prompt_cache_miss_tokens,
                    completion_tokens, reasoning_tokens, estimated_cost_cny,
                    cache_read_tokens, cache_write_tokens, estimated_cost_usd,
                    provider_reported_cost_usd, cost_source,
                    latency_ms, retries, status, candidate_hash,
                    budget_session_id, lifecycle_id, request_chars, response_chars,
                    attribution_uid, usage_observation_scope
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    event.timestamp.astimezone(UTC).isoformat(),
                    event.client_name,
                    event.task_kind,
                    event.model,
                    event.provider.value,
                    event.provider_model_id,
                    event.provider_runtime,
                    event.provider_account,
                    event.provider_subscription_id,
                    int(event.thinking_enabled),
                    event.reasoning_effort,
                    event.max_output_tokens,
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
                    event.cache_read_tokens,
                    event.cache_write_tokens,
                    event.estimated_cost_usd,
                    event.provider_reported_cost_usd,
                    event.cost_source.value,
                    event.latency_ms,
                    event.retries,
                    event.status,
                    event.candidate_hash,
                    event.budget_session_id,
                    event.lifecycle_id,
                    event.request_chars,
                    event.response_chars,
                    event.attribution_uid,
                    event.usage_observation_scope,
                ),
            )
            return int(cursor.lastrowid)

    def usage_id_for_attribution(self, attribution_uid: str) -> int | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM api_usage WHERE attribution_uid = ?",
                (attribution_uid,),
            ).fetchone()
        return int(row[0]) if row is not None else None

    def record_lifecycle(self, event: LifecycleAuditEvent) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO lifecycle_audit (
                    lifecycle_id, budget_session_id, client_name, model, tool_name,
                    spec_sha256, started_at, completed_at, status,
                    spec_total_chars, purpose_chars, input_contract_chars,
                    output_contract_chars, fixture_notes_chars,
                    acceptance_tests_chars, spec_total_bytes, purpose_bytes,
                    input_contract_bytes, output_contract_bytes, fixture_notes_bytes,
                    acceptance_tests_bytes, candidate_source_chars,
                    candidate_test_chars, candidate_source_bytes,
                    candidate_test_bytes, candidate_file_count,
                    review_summary_chars, patch_chars, review_summary_bytes,
                    patch_bytes, attempt_count, repair_count,
                    final_job_id, final_candidate_sha256
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
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
                    event.spec_total_bytes,
                    event.purpose_bytes,
                    event.input_contract_bytes,
                    event.output_contract_bytes,
                    event.fixture_notes_bytes,
                    event.acceptance_tests_bytes,
                    event.candidate_source_chars,
                    event.candidate_test_chars,
                    event.candidate_source_bytes,
                    event.candidate_test_bytes,
                    event.candidate_file_count,
                    event.review_summary_chars,
                    event.patch_chars,
                    event.review_summary_bytes,
                    event.patch_bytes,
                    event.attempt_count,
                    event.repair_count,
                    event.final_job_id,
                    event.final_candidate_sha256,
                ),
            )

    def record_protocol_event(self, event: ProtocolAuditEvent) -> None:
        """Persist structural MCP metadata without prompt or payload content."""

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO mcp_protocol_events (
                    timestamp, process_instance_id, session_id, request_id,
                    configured_client_name, reported_client_name, client_version,
                    protocol_version, method, tool_name, duration_ms, status,
                    error_kind, tool_count, tool_surface_bytes,
                    tool_surface_sha256, arguments_bytes, result_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp.astimezone(UTC).isoformat(),
                    event.process_instance_id,
                    event.session_id,
                    event.request_id,
                    event.configured_client_name,
                    event.reported_client_name,
                    event.client_version,
                    event.protocol_version,
                    event.method,
                    event.tool_name,
                    event.duration_ms,
                    event.status,
                    event.error_kind,
                    event.tool_count,
                    event.tool_surface_bytes,
                    event.tool_surface_sha256,
                    event.arguments_bytes,
                    event.result_bytes,
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
                    COALESCE(SUM(estimated_cost_cny), 0) AS cost,
                    COALESCE(SUM(estimated_cost_usd), 0) AS cost_usd
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
            provider_rows = connection.execute(
                """
                SELECT provider AS key, COUNT(*) AS count
                FROM api_usage WHERE timestamp >= ? GROUP BY provider ORDER BY provider
                """,
                (cutoff,),
            ).fetchall()
            provider_model_cost_rows = connection.execute(
                """
                SELECT COALESCE(provider_model_id, model) AS key,
                       COALESCE(SUM(estimated_cost_usd), 0) AS cost
                FROM api_usage WHERE timestamp >= ?
                GROUP BY COALESCE(provider_model_id, model) ORDER BY key
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
            lifecycle_rows = connection.execute(
                """
                SELECT lifecycle_audit.*,
                       COUNT(api_usage.id) AS api_call_count,
                       COALESCE(SUM(api_usage.prompt_cache_hit_tokens), 0) AS cache_hit,
                       COALESCE(SUM(api_usage.prompt_cache_miss_tokens), 0) AS cache_miss,
                       COALESCE(SUM(api_usage.completion_tokens), 0) AS completion,
                       COALESCE(SUM(api_usage.reasoning_tokens), 0) AS reasoning,
                       COALESCE(SUM(api_usage.estimated_cost_cny), 0) AS cost
                FROM lifecycle_audit
                LEFT JOIN api_usage
                  ON api_usage.lifecycle_id = lifecycle_audit.lifecycle_id
                WHERE lifecycle_audit.completed_at >= ?
                GROUP BY lifecycle_audit.lifecycle_id
                ORDER BY lifecycle_audit.completed_at DESC
                LIMIT 20
                """,
                (cutoff,),
            ).fetchall()
            lifecycle_count = connection.execute(
                "SELECT COUNT(*) FROM lifecycle_audit WHERE completed_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            opencode_accounts = self._opencode_accounts(connection)

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
            estimated_cost_usd=round(float(totals["cost_usd"]), 8),
            by_model={str(row["model"]): int(row["count"]) for row in model_rows},
            by_provider={str(row["key"]): int(row["count"]) for row in provider_rows},
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
            estimated_cost_usd_by_provider_model={
                str(row["key"]): round(float(row["cost"]), 8) for row in provider_model_cost_rows
            },
            opencode_go_accounts=opencode_accounts,
            current_pricing_band=current_band,
            peak_pricing_enabled=peak_enabled,
            pricing_schedule_version=schedule_version,
            current_beijing_time=beijing_time,
            lifecycle_count=int(lifecycle_count),
            recent_lifecycle_economics=[self._lifecycle_economics(row) for row in lifecycle_rows],
        )

    @staticmethod
    def _opencode_accounts(connection: sqlite3.Connection) -> list[OpenCodeGoAccountUsage]:
        now = datetime.now(UTC)
        accounts = connection.execute(
            """
            SELECT DISTINCT provider_account,
                   COALESCE(provider_subscription_id, 'legacy:' || provider_account)
                       AS subscription_id
            FROM api_usage
            WHERE provider = 'opencode' AND provider_account IS NOT NULL
            ORDER BY provider_account, subscription_id
            """
        ).fetchall()
        result: list[OpenCodeGoAccountUsage] = []
        for account_row in accounts:
            account = str(account_row["provider_account"])
            subscription_id = str(account_row["subscription_id"])
            utc_day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            day_spend_row = connection.execute(
                """
                SELECT COALESCE(SUM(COALESCE(provider_reported_cost_usd,
                    estimated_cost_usd)), 0) AS ledger
                FROM api_usage
                WHERE provider = 'opencode' AND provider_account = ?
                  AND COALESCE(provider_subscription_id,
                      'legacy:' || provider_account) = ?
                  AND timestamp >= ?
                """,
                (account, subscription_id, utc_day_start.isoformat()),
            ).fetchone()
            current_utc_day_spent = round(float(day_spend_row["ledger"]), 8)
            windows: list[OpenCodeGoLimitWindow] = []
            for name, delta in (
                ("rolling_5h", timedelta(hours=5)),
                ("rolling_7d", timedelta(days=7)),
                ("rolling_30d", timedelta(days=30)),
            ):
                spend_row = connection.execute(
                    """
                        SELECT COALESCE(SUM(estimated_cost_usd), 0) AS estimated,
                               COALESCE(SUM(provider_reported_cost_usd), 0) AS reported,
                               COALESCE(SUM(COALESCE(provider_reported_cost_usd,
                                   estimated_cost_usd)), 0) AS ledger,
                               COUNT(*) AS calls,
                               COUNT(provider_reported_cost_usd) AS reported_calls
                        FROM api_usage
                        WHERE provider = 'opencode' AND provider_account = ?
                          AND COALESCE(provider_subscription_id,
                              'legacy:' || provider_account) = ?
                          AND timestamp >= ?
                        """,
                    (account, subscription_id, (now - delta).isoformat()),
                ).fetchone()
                spent = float(spend_row["ledger"])
                limit = OPENCODE_GO_LIMITS_USD[name]
                windows.append(
                    OpenCodeGoLimitWindow(
                        window=name,
                        spent_usd=round(spent, 8),
                        estimated_spent_usd=round(float(spend_row["estimated"]), 8),
                        provider_reported_spent_usd=round(float(spend_row["reported"]), 8),
                        provider_reported_call_count=int(spend_row["reported_calls"]),
                        total_call_count=int(spend_row["calls"]),
                        limit_usd=limit,
                        remaining_usd=round(max(0.0, limit - spent), 8),
                    )
                )
            rows = connection.execute(
                """
                SELECT provider_model_id,
                       COALESCE(SUM(estimated_cost_usd), 0) AS estimated,
                       COALESCE(SUM(provider_reported_cost_usd), 0) AS reported,
                       COALESCE(SUM(COALESCE(provider_reported_cost_usd,
                           estimated_cost_usd)), 0) AS ledger
                FROM api_usage
                WHERE provider = 'opencode' AND provider_account = ? AND timestamp >= ?
                  AND COALESCE(provider_subscription_id,
                      'legacy:' || provider_account) = ?
                GROUP BY provider_model_id ORDER BY provider_model_id
                """,
                (account, (now - timedelta(days=30)).isoformat(), subscription_id),
            ).fetchall()
            by_model = {
                str(row["provider_model_id"]): round(float(row["ledger"]), 8)
                for row in rows
                if row["provider_model_id"] in {model.value for model in OPENCODE_GO_PRICES}
            }
            shared_remaining = next(
                window.remaining_usd for window in windows if window.window == "rolling_30d"
            )
            result.append(
                OpenCodeGoAccountUsage(
                    account=account,
                    subscription_id=subscription_id,
                    current_utc_day_spent_usd=current_utc_day_spent,
                    daily_pace_target_usd=2.0,
                    above_daily_pace=current_utc_day_spent > 2.0,
                    daily_pace_is_hard_limit=False,
                    windows=windows,
                    rolling_30d_by_model_usd=by_model,
                    model_allowances=[
                        OpenCodeGoModelAllowance(
                            model_id=model.value,
                            spent_usd=by_model.get(model.value, 0.0),
                            estimated_spent_usd=round(
                                next(
                                    (
                                        float(row["estimated"])
                                        for row in rows
                                        if row["provider_model_id"] == model.value
                                    ),
                                    0.0,
                                ),
                                8,
                            ),
                            provider_reported_spent_usd=round(
                                next(
                                    (
                                        float(row["reported"])
                                        for row in rows
                                        if row["provider_model_id"] == model.value
                                    ),
                                    0.0,
                                ),
                                8,
                            ),
                            limit_usd=price.included_limit_usd,
                            remaining_usd=round(
                                max(
                                    0.0,
                                    price.included_limit_usd - by_model.get(model.value, 0.0),
                                ),
                                8,
                            ),
                            effective_remaining_usd=round(
                                min(
                                    shared_remaining,
                                    max(
                                        0.0,
                                        price.included_limit_usd - by_model.get(model.value, 0.0),
                                    ),
                                ),
                                8,
                            ),
                        )
                        for model, price in sorted(
                            OPENCODE_GO_PRICES.items(), key=lambda item: item[0].value
                        )
                    ],
                    catalog_version=OPENCODE_GO_PRICING_VERSION,
                    catalog_effective_at=OPENCODE_GO_PRICING_EFFECTIVE_AT,
                    catalog_source_url=OPENCODE_GO_PRICING_SOURCE_URL,
                )
            )
        return result

    def client_for_job(self, job_id: str) -> str | None:
        """Return the controller that created a final candidate job, if audited."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT client_name FROM lifecycle_audit
                WHERE final_job_id = ?
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        return str(row["client_name"]) if row is not None else None

    def opencode_limit_reason(
        self, account: str, model_id: str, subscription_id: str | None = None
    ) -> str | None:
        """Return a conservative local limit reason, leaving remote 429 authoritative."""

        now = datetime.now(UTC)
        selected_subscription = subscription_id or f"legacy:{account}"
        checks = (
            ("rolling 5-hour", timedelta(hours=5), OPENCODE_GO_LIMITS_USD["rolling_5h"]),
            ("rolling 7-day", timedelta(days=7), OPENCODE_GO_LIMITS_USD["rolling_7d"]),
            ("rolling 30-day", timedelta(days=30), OPENCODE_GO_LIMITS_USD["rolling_30d"]),
        )
        with self._connection() as connection:
            for label, delta, limit in checks:
                spent = float(
                    connection.execute(
                        """
                        SELECT COALESCE(SUM(COALESCE(provider_reported_cost_usd,
                            estimated_cost_usd)), 0) FROM api_usage
                        WHERE provider = 'opencode' AND provider_account = ?
                          AND COALESCE(provider_subscription_id,
                              'legacy:' || provider_account) = ?
                          AND timestamp >= ?
                        """,
                        (account, selected_subscription, (now - delta).isoformat()),
                    ).fetchone()[0]
                )
                if spent >= limit:
                    return f"OpenCode Go {label} local ledger limit reached"
            price = next(
                (price for model, price in OPENCODE_GO_PRICES.items() if model.value == model_id),
                None,
            )
            if price is not None:
                spent = float(
                    connection.execute(
                        """
                        SELECT COALESCE(SUM(COALESCE(provider_reported_cost_usd,
                            estimated_cost_usd)), 0) FROM api_usage
                        WHERE provider = 'opencode' AND provider_account = ?
                          AND COALESCE(provider_subscription_id,
                              'legacy:' || provider_account) = ?
                          AND provider_model_id = ? AND timestamp >= ?
                        """,
                        (
                            account,
                            selected_subscription,
                            model_id,
                            (now - timedelta(days=30)).isoformat(),
                        ),
                    ).fetchone()[0]
                )
                if spent >= price.included_limit_usd:
                    return "OpenCode Go rolling 30-day model allowance reached"
        return None

    @staticmethod
    def _lifecycle_economics(row: sqlite3.Row) -> LifecycleEconomics:
        source_bytes = int(row["candidate_source_bytes"])
        test_bytes = int(row["candidate_test_bytes"])
        spec_bytes = int(row["spec_total_bytes"])
        candidate_total_bytes = source_bytes + test_bytes
        return LifecycleEconomics(
            lifecycle_id=str(row["lifecycle_id"]),
            budget_session_id=str(row["budget_session_id"]),
            client_name=str(row["client_name"]),
            model=str(row["model"]),
            tool_name=str(row["tool_name"]),
            completed_at=datetime.fromisoformat(str(row["completed_at"])),
            status=str(row["status"]),
            api_call_count=int(row["api_call_count"]),
            prompt_cache_hit_tokens=int(row["cache_hit"]),
            prompt_cache_miss_tokens=int(row["cache_miss"]),
            completion_tokens=int(row["completion"]),
            reasoning_tokens=int(row["reasoning"]),
            estimated_cost_cny=round(float(row["cost"]), 8),
            structural_bytes_available=spec_bytes > 0,
            spec_total_chars=int(row["spec_total_chars"]),
            spec_total_bytes=spec_bytes,
            purpose_chars=int(row["purpose_chars"]),
            purpose_bytes=int(row["purpose_bytes"]),
            input_contract_chars=int(row["input_contract_chars"]),
            input_contract_bytes=int(row["input_contract_bytes"]),
            output_contract_chars=int(row["output_contract_chars"]),
            output_contract_bytes=int(row["output_contract_bytes"]),
            fixture_notes_chars=int(row["fixture_notes_chars"]),
            fixture_notes_bytes=int(row["fixture_notes_bytes"]),
            acceptance_tests_chars=int(row["acceptance_tests_chars"]),
            acceptance_tests_bytes=int(row["acceptance_tests_bytes"]),
            candidate_source_chars=int(row["candidate_source_chars"]),
            candidate_source_bytes=source_bytes,
            candidate_test_chars=int(row["candidate_test_chars"]),
            candidate_test_bytes=test_bytes,
            patch_chars=int(row["patch_chars"]),
            patch_bytes=int(row["patch_bytes"]),
            spec_to_candidate_source_bytes_ratio=(
                round(spec_bytes / source_bytes, 6) if spec_bytes and source_bytes else None
            ),
            spec_to_candidate_total_bytes_ratio=(
                round(spec_bytes / candidate_total_bytes, 6)
                if spec_bytes and candidate_total_bytes
                else None
            ),
        )
