"""SQLite review ledger with prompt-free metrics and blind adjudication records."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from platformdirs import user_data_path

from ask_ai_mcp.code_review_models import (
    CodeReviewAdjudicationCommand,
    CodeReviewFinding,
    CodeReviewMetricSlice,
    CodeReviewMonthlyReport,
    CodeReviewOutcomeCommand,
    CodeReviewPayload,
)

PROMPT_VERSION = "code-review-prompt-v1"
CONTRACT_VERSION = "code-review-findings-v1"
MAX_OUTPUT_TOKENS = 8_000
TEMPERATURE = 0.0


def default_code_review_root() -> Path:
    return user_data_path("AskAIMCP", appauthor=False, ensure_exists=True) / "code-review"


class CodeReviewStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_code_review_root()
        self.jobs_root = self.root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "review.db"
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS code_review_runs (
                    run_id TEXT PRIMARY KEY,
                    review_group_id TEXT NOT NULL,
                    blind_label TEXT NOT NULL UNIQUE,
                    repository_id TEXT NOT NULL,
                    repo_snapshot_hash TEXT NOT NULL,
                    diff_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    reasoning_effort TEXT NOT NULL,
                    max_output_tokens INTEGER NOT NULL,
                    temperature REAL NOT NULL,
                    account_uid TEXT,
                    account_alias TEXT NOT NULL,
                    subscription_id TEXT NOT NULL,
                    catalog_version TEXT NOT NULL,
                    catalog_effective_at TEXT NOT NULL,
                    catalog_source_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    reasoning_tokens INTEGER,
                    cache_read_tokens INTEGER,
                    cache_write_tokens INTEGER,
                    input_rate REAL,
                    output_rate REAL,
                    cache_read_rate REAL,
                    cache_write_rate REAL,
                    estimated_cost_usd REAL,
                    provider_reported_cost_usd REAL,
                    usage_source TEXT NOT NULL,
                    pricing_band TEXT NOT NULL,
                    api_usage_id INTEGER,
                    latency_ms INTEGER,
                    file_count INTEGER NOT NULL,
                    changed_line_count INTEGER NOT NULL,
                    language TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    diff_size_bucket TEXT NOT NULL,
                    risk_level TEXT,
                    artifact_relative_path TEXT NOT NULL,
                    structured_output_hash TEXT,
                    failure_kind TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_code_review_runs_group
                    ON code_review_runs(review_group_id);
                CREATE INDEX IF NOT EXISTS idx_code_review_runs_created
                    ON code_review_runs(created_at);
                CREATE INDEX IF NOT EXISTS idx_code_review_runs_account
                    ON code_review_runs(account_alias, subscription_id, created_at);

                CREATE TABLE IF NOT EXISTS code_review_findings (
                    run_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    finding_fingerprint TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    file TEXT NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    structured_output_hash TEXT NOT NULL,
                    PRIMARY KEY(run_id, finding_id),
                    FOREIGN KEY(run_id) REFERENCES code_review_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_code_review_findings_fingerprint
                    ON code_review_findings(finding_fingerprint);

                CREATE TABLE IF NOT EXISTS code_review_adjudications (
                    run_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    severity_agreement INTEGER,
                    accepted INTEGER,
                    fixed INTEGER,
                    test_confirmed INTEGER,
                    adjudication_ms INTEGER NOT NULL,
                    adjudicated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, finding_id),
                    FOREIGN KEY(run_id, finding_id)
                        REFERENCES code_review_findings(run_id, finding_id)
                );

                CREATE TABLE IF NOT EXISTS code_review_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    finding_id TEXT,
                    adopted INTEGER,
                    escaped_defect INTEGER,
                    regression_result TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES code_review_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_code_review_outcomes_run
                    ON code_review_outcomes(run_id);
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(code_review_runs)").fetchall()
            }
            if "account_uid" not in columns:
                connection.execute("ALTER TABLE code_review_runs ADD COLUMN account_uid TEXT")
                connection.execute(
                    "UPDATE code_review_runs SET account_uid = account_alias "
                    "WHERE account_uid IS NULL"
                )
            if "reasoning_tokens" not in columns:
                connection.execute(
                    "ALTER TABLE code_review_runs ADD COLUMN reasoning_tokens INTEGER"
                )
            if "reasoning_effort" not in columns:
                connection.execute(
                    "ALTER TABLE code_review_runs ADD COLUMN "
                    "reasoning_effort TEXT NOT NULL DEFAULT 'max'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_code_review_runs_account_uid "
                "ON code_review_runs(account_uid, subscription_id, created_at)"
            )

    def create_run(self, values: dict[str, object]) -> None:
        invariant_fields = (
            "diff_hash",
            "prompt_version",
            "contract_version",
            "temperature",
        )
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM code_review_runs WHERE review_group_id = ? LIMIT 1",
                (values["review_group_id"],),
            ).fetchone()
            if existing is not None and any(
                existing[field] != values[field] for field in invariant_fields
            ):
                raise RuntimeError(
                    "review_group_id is already bound to a different diff or sampling contract"
                )
            connection.execute(
                """
                INSERT INTO code_review_runs (
                    run_id, review_group_id, blind_label, repository_id,
                    repo_snapshot_hash, diff_hash, model, provider, protocol,
                    prompt_version, contract_version, reasoning_effort,
                    max_output_tokens, temperature,
                    account_uid, account_alias, subscription_id, catalog_version,
                    catalog_effective_at, catalog_source_url, created_at, status,
                    usage_source, pricing_band, file_count, changed_line_count,
                    language, task_type, diff_size_bucket, artifact_relative_path
                ) VALUES (
                    :run_id, :review_group_id, :blind_label, :repository_id,
                    :repo_snapshot_hash, :diff_hash, :model, :provider, :protocol,
                    :prompt_version, :contract_version, :reasoning_effort,
                    :max_output_tokens, :temperature,
                    :account_uid, :account_alias, :subscription_id, :catalog_version,
                    :catalog_effective_at, :catalog_source_url, :created_at, :status,
                    :usage_source, :pricing_band, :file_count, :changed_line_count,
                    :language, :task_type, :diff_size_bucket, :artifact_relative_path
                )
                """,
                values,
            )

    def mark_running(self, run_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE code_review_runs SET status = 'running', started_at = ? WHERE run_id = ?",
                (datetime.now(UTC).isoformat(), run_id),
            )

    def complete(
        self,
        run_id: str,
        *,
        payload: CodeReviewPayload,
        structured_output_hash: str,
        usage: dict[str, object],
        api_usage_id: int,
    ) -> None:
        severity_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        risk = max(
            (finding.severity.value for finding in payload.findings),
            key=lambda value: severity_order[value],
            default="low",
        )
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE code_review_runs SET
                    completed_at = :completed_at, status = 'succeeded',
                    input_tokens = :input_tokens, output_tokens = :output_tokens,
                    reasoning_tokens = :reasoning_tokens,
                    cache_read_tokens = :cache_read_tokens,
                    cache_write_tokens = :cache_write_tokens,
                    input_rate = :input_rate, output_rate = :output_rate,
                    cache_read_rate = :cache_read_rate,
                    cache_write_rate = :cache_write_rate,
                    estimated_cost_usd = :estimated_cost_usd,
                    provider_reported_cost_usd = :provider_reported_cost_usd,
                    usage_source = :usage_source, pricing_band = :pricing_band,
                    api_usage_id = :api_usage_id, latency_ms = :latency_ms,
                    risk_level = :risk_level,
                    structured_output_hash = :structured_output_hash
                WHERE run_id = :run_id
                """,
                {
                    **usage,
                    "completed_at": datetime.now(UTC).isoformat(),
                    "api_usage_id": api_usage_id,
                    "risk_level": risk,
                    "structured_output_hash": structured_output_hash,
                    "run_id": run_id,
                },
            )
            for finding in payload.findings:
                fingerprint = self._finding_fingerprint(finding)
                connection.execute(
                    """
                    INSERT INTO code_review_findings (
                        run_id, finding_id, finding_fingerprint, category, severity,
                        confidence, file, line_start, line_end, structured_output_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        finding.finding_id,
                        fingerprint,
                        finding.category.value,
                        finding.severity.value,
                        finding.confidence,
                        finding.file,
                        finding.line_start,
                        finding.line_end,
                        structured_output_hash,
                    ),
                )

    def fail(self, run_id: str, failure_kind: str, *, latency_ms: int) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE code_review_runs SET completed_at = ?, status = 'failed',
                    failure_kind = ?, latency_ms = ? WHERE run_id = ?
                """,
                (datetime.now(UTC).isoformat(), failure_kind[:120], latency_ms, run_id),
            )

    def get_run(self, run_id: str) -> sqlite3.Row:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM code_review_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("code review job was not found")
        return row

    def adjudicate(self, run_id: str, command: CodeReviewAdjudicationCommand) -> None:
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM code_review_findings WHERE run_id = ? AND finding_id = ?",
                (run_id, command.finding_id),
            ).fetchone()
            if exists is None:
                raise RuntimeError("finding was not found for blind adjudication")
            connection.execute(
                """
                INSERT INTO code_review_adjudications (
                    run_id, finding_id, decision, severity_agreement, accepted,
                    fixed, test_confirmed, adjudication_ms, adjudicated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, finding_id) DO UPDATE SET
                    decision = excluded.decision,
                    severity_agreement = excluded.severity_agreement,
                    accepted = excluded.accepted,
                    fixed = excluded.fixed,
                    test_confirmed = excluded.test_confirmed,
                    adjudication_ms = excluded.adjudication_ms,
                    adjudicated_at = excluded.adjudicated_at
                """,
                (
                    run_id,
                    command.finding_id,
                    command.decision.value,
                    self._bool(command.severity_agreement),
                    self._bool(command.accepted),
                    self._bool(command.fixed),
                    self._bool(command.test_confirmed),
                    command.adjudication_ms,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def record_outcome(self, run_id: str, command: CodeReviewOutcomeCommand) -> None:
        with self._connection() as connection:
            if command.finding_id is not None:
                exists = connection.execute(
                    "SELECT 1 FROM code_review_findings WHERE run_id = ? AND finding_id = ?",
                    (run_id, command.finding_id),
                ).fetchone()
                if exists is None:
                    raise RuntimeError("outcome finding was not found")
            connection.execute(
                """
                INSERT INTO code_review_outcomes (
                    run_id, finding_id, adopted, escaped_defect,
                    regression_result, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    command.finding_id,
                    self._bool(command.adopted),
                    self._bool(command.escaped_defect),
                    command.regression_result.value,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def monthly_report(self, month: str | None = None) -> CodeReviewMonthlyReport:
        selected = month or datetime.now(UTC).strftime("%Y-%m")
        try:
            start = datetime.strptime(selected, "%Y-%m").replace(tzinfo=UTC)
        except ValueError as error:
            raise ValueError("month must be YYYY-MM") from error
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        dimensions = ("language", "task_type", "diff_size_bucket", "risk_level")
        slices: list[CodeReviewMetricSlice] = []
        with self._connection() as connection:
            runs = connection.execute(
                """
                SELECT * FROM code_review_runs
                WHERE created_at >= ? AND created_at < ? AND status = 'succeeded'
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            for dimension in dimensions:
                values = sorted({str(row[dimension] or "unknown") for row in runs})
                for value in values:
                    selected_runs = [
                        row for row in runs if str(row[dimension] or "unknown") == value
                    ]
                    slices.append(self._metric_slice(connection, dimension, value, selected_runs))
        return CodeReviewMonthlyReport(month=selected, slices=slices)

    @staticmethod
    def _metric_slice(
        connection: sqlite3.Connection,
        dimension: str,
        value: str,
        runs: list[sqlite3.Row],
    ) -> CodeReviewMetricSlice:
        ids = [str(row["run_id"]) for row in runs]
        if not ids:
            return CodeReviewMetricSlice(
                dimension=dimension,
                value=value,
                runs=0,
                adjudicated_findings=0,
                unique_true_findings=0,
                false_positive_burden=0.0,
            )
        placeholders = ",".join("?" for _ in ids)
        adjudications = connection.execute(
            f"""
            SELECT a.*, f.finding_fingerprint
            FROM code_review_adjudications a
            JOIN code_review_findings f
              ON f.run_id = a.run_id AND f.finding_id = a.finding_id
            WHERE a.run_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        outcomes = connection.execute(
            f"SELECT * FROM code_review_outcomes WHERE run_id IN ({placeholders})",
            ids,
        ).fetchall()
        tp = [row for row in adjudications if row["decision"] == "true_positive"]
        fp = [row for row in adjudications if row["decision"] == "false_positive"]
        duplicates = [row for row in adjudications if row["decision"] == "duplicate"]
        precision_denominator = len(tp) + len(fp)
        escaped = sum(1 for row in outcomes if row["escaped_defect"] == 1)
        unique_tp = len({str(row["finding_fingerprint"]) for row in tp})
        accepted = sum(1 for row in tp if row["accepted"] == 1)
        total_cost = sum(float(row["estimated_cost_usd"] or 0) for row in runs)
        total_latency = sum(int(row["latency_ms"] or 0) for row in runs)
        changed_lines = sum(int(row["changed_line_count"]) for row in runs)
        calibrated = [row for row in adjudications if row["severity_agreement"] is not None]
        confirmed = [row for row in tp if row["test_confirmed"] is not None]
        return CodeReviewMetricSlice(
            dimension=dimension,
            value=value,
            runs=len(runs),
            adjudicated_findings=len(adjudications),
            precision=(len(tp) / precision_denominator if precision_denominator else None),
            unique_true_findings=unique_tp,
            recall_proxy=(unique_tp / (unique_tp + escaped) if unique_tp + escaped else None),
            escaped_defects=escaped,
            false_positive_burden=len(fp) / len(runs),
            duplicate_rate=(len(duplicates) / len(adjudications) if adjudications else None),
            severity_calibration=(
                sum(1 for row in calibrated if row["severity_agreement"] == 1) / len(calibrated)
                if calibrated
                else None
            ),
            test_confirmed_rate=(
                sum(1 for row in confirmed if row["test_confirmed"] == 1) / len(confirmed)
                if confirmed
                else None
            ),
            true_findings_per_kloc=(unique_tp / (changed_lines / 1_000) if changed_lines else None),
            cost_per_accepted_finding_usd=(total_cost / accepted if accepted else None),
            latency_per_accepted_finding_ms=(total_latency / accepted if accepted else None),
            average_latency_ms=(total_latency / len(runs) if runs else None),
        )

    @staticmethod
    def _finding_fingerprint(finding: CodeReviewFinding) -> str:
        normalized = "|".join(
            (
                finding.category.value,
                finding.file.casefold(),
                str(finding.line_start),
                finding.evidence_sha256,
            )
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _bool(value: bool | None) -> int | None:
        return None if value is None else int(value)
