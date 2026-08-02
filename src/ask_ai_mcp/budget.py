"""Persistent per-conversation DeepSeek budget grants."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from ask_ai_mcp.models import (
    BudgetSessionStatus,
    BudgetState,
    DeepSeekModel,
    ModelBudgetStatus,
)
from ask_ai_mcp.usage import UsageStore, default_usage_db_path

BUDGET_INCREMENT_CNY = 5.0
INITIAL_FLASH_BUDGET_CNY = 5.0
INITIAL_PRO_BUDGET_CNY = 0.0


class BudgetError(RuntimeError):
    """Raised when a budget session is missing, mismatched, or closed."""


class BudgetAuthorizationError(BudgetError):
    """Raised before a lifecycle whose model budget is not active."""

    def __init__(self, status: BudgetSessionStatus, model: DeepSeekModel) -> None:
        model_status = status.flash if model is DeepSeekModel.FLASH else status.pro
        super().__init__(
            f"{model_status.state.value}: budget_session_id={status.budget_session_id}; "
            f"model={model.value}; granted_cny={model_status.granted_cny:.8f}; "
            f"spent_cny={model_status.spent_cny:.8f}; next_increment_cny=5.00"
        )
        self.status = status
        self.model = model


class BudgetStore:
    """SQLite-backed model budgets shared by both desktop MCP processes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_usage_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        UsageStore(self.path)
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
                CREATE TABLE IF NOT EXISTS budget_sessions (
                    budget_session_id TEXT PRIMARY KEY,
                    client_name TEXT NOT NULL,
                    label TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT,
                    flash_granted_cny REAL NOT NULL,
                    pro_granted_cny REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_extensions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    budget_session_id TEXT NOT NULL,
                    client_name TEXT NOT NULL,
                    model TEXT NOT NULL,
                    amount_cny REAL NOT NULL,
                    granted_at TEXT NOT NULL,
                    FOREIGN KEY (budget_session_id)
                        REFERENCES budget_sessions(budget_session_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_budget_sessions_client "
                "ON budget_sessions(client_name)"
            )

    def open_session(self, *, client_name: str, label: str | None = None) -> BudgetSessionStatus:
        identifier = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO budget_sessions (
                    budget_session_id, client_name, label, created_at, updated_at,
                    closed_at, flash_granted_cny, pro_granted_cny
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    identifier,
                    client_name,
                    label,
                    now,
                    now,
                    INITIAL_FLASH_BUDGET_CNY,
                    INITIAL_PRO_BUDGET_CNY,
                ),
            )
        return self.status(identifier, client_name=client_name)

    def status(self, budget_session_id: str, *, client_name: str) -> BudgetSessionStatus:
        self._validate_identifier(budget_session_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM budget_sessions WHERE budget_session_id = ?",
                (budget_session_id,),
            ).fetchone()
            if row is None:
                raise BudgetError("budget session does not exist")
            if str(row["client_name"]) != client_name:
                raise BudgetError("budget session belongs to a different desktop client")

            usage_rows = connection.execute(
                """
                SELECT model, COALESCE(SUM(estimated_cost_cny), 0) AS spent,
                       COUNT(*) AS api_calls, MAX(timestamp) AS latest
                FROM api_usage
                WHERE budget_session_id = ?
                GROUP BY model
                """,
                (budget_session_id,),
            ).fetchall()
            lifecycle_row = connection.execute(
                """
                SELECT COUNT(*) AS lifecycle_count, MAX(completed_at) AS latest
                FROM lifecycle_audit
                WHERE budget_session_id = ?
                """,
                (budget_session_id,),
            ).fetchone()

        usage = {str(item["model"]): item for item in usage_rows}
        flash_spent = float(usage.get(DeepSeekModel.FLASH.value, {"spent": 0})["spent"])
        pro_spent = float(usage.get(DeepSeekModel.PRO.value, {"spent": 0})["spent"])
        closed_at = datetime.fromisoformat(str(row["closed_at"])) if row["closed_at"] else None
        updated_at = datetime.fromisoformat(str(row["updated_at"]))
        latest_values = [str(item["latest"]) for item in usage_rows if item["latest"] is not None]
        if lifecycle_row is not None and lifecycle_row["latest"] is not None:
            latest_values.append(str(lifecycle_row["latest"]))
        if latest_values:
            updated_at = max(updated_at, *(datetime.fromisoformat(item) for item in latest_values))

        flash = self._model_status(
            model=DeepSeekModel.FLASH,
            granted=float(row["flash_granted_cny"]),
            spent=flash_spent,
            closed=closed_at is not None,
        )
        pro = self._model_status(
            model=DeepSeekModel.PRO,
            granted=float(row["pro_granted_cny"]),
            spent=pro_spent,
            closed=closed_at is not None,
        )
        return BudgetSessionStatus(
            budget_session_id=budget_session_id,
            client_name=client_name,
            label=row["label"],
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=updated_at,
            closed_at=closed_at,
            flash=flash,
            pro=pro,
            lifecycle_count=int(lifecycle_row["lifecycle_count"] if lifecycle_row else 0),
            api_call_count=sum(int(item["api_calls"]) for item in usage_rows),
        )

    def add_budget_block(
        self,
        budget_session_id: str,
        *,
        client_name: str,
        model: DeepSeekModel,
    ) -> BudgetSessionStatus:
        current = self.status(budget_session_id, client_name=client_name)
        if current.closed_at is not None:
            raise BudgetError("closed budget sessions cannot be extended")
        model_status = current.flash if model is DeepSeekModel.FLASH else current.pro
        allowed_states = {
            BudgetState.FLASH_EXTENSION_REQUIRED,
            BudgetState.PRO_AUTHORIZATION_REQUIRED,
            BudgetState.PRO_EXTENSION_REQUIRED,
        }
        if model_status.state not in allowed_states:
            raise BudgetError(
                "budget block can be added only when authorization or extension is required"
            )
        column = "flash_granted_cny" if model is DeepSeekModel.FLASH else "pro_granted_cny"
        now = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            connection.execute(
                f"UPDATE budget_sessions SET {column} = {column} + ?, updated_at = ? "
                "WHERE budget_session_id = ? AND client_name = ? AND closed_at IS NULL",
                (BUDGET_INCREMENT_CNY, now, budget_session_id, client_name),
            )
            connection.execute(
                """
                INSERT INTO budget_extensions (
                    budget_session_id, client_name, model, amount_cny, granted_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (budget_session_id, client_name, model.value, BUDGET_INCREMENT_CNY, now),
            )
        return self.status(budget_session_id, client_name=client_name)

    def close_session(self, budget_session_id: str, *, client_name: str) -> BudgetSessionStatus:
        current = self.status(budget_session_id, client_name=client_name)
        if current.closed_at is not None:
            return current
        now = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE budget_sessions
                SET closed_at = ?, updated_at = ?
                WHERE budget_session_id = ? AND client_name = ? AND closed_at IS NULL
                """,
                (now, now, budget_session_id, client_name),
            )
        return self.status(budget_session_id, client_name=client_name)

    def require_lifecycle_budget(
        self,
        budget_session_id: str,
        *,
        client_name: str,
        model: DeepSeekModel,
    ) -> BudgetSessionStatus:
        status = self.status(budget_session_id, client_name=client_name)
        model_status = status.flash if model is DeepSeekModel.FLASH else status.pro
        if model_status.state is not BudgetState.ACTIVE:
            raise BudgetAuthorizationError(status, model)
        return status

    @staticmethod
    def _model_status(
        *, model: DeepSeekModel, granted: float, spent: float, closed: bool
    ) -> ModelBudgetStatus:
        granted = round(max(0.0, granted), 8)
        spent = round(max(0.0, spent), 8)
        if closed:
            state = BudgetState.CLOSED
        elif model is DeepSeekModel.PRO and granted == 0:
            state = BudgetState.PRO_AUTHORIZATION_REQUIRED
        elif spent >= granted:
            state = (
                BudgetState.FLASH_EXTENSION_REQUIRED
                if model is DeepSeekModel.FLASH
                else BudgetState.PRO_EXTENSION_REQUIRED
            )
        else:
            state = BudgetState.ACTIVE
        return ModelBudgetStatus(
            model=model,
            state=state,
            granted_cny=granted,
            spent_cny=spent,
            remaining_cny=round(max(0.0, granted - spent), 8),
            overshoot_cny=round(max(0.0, spent - granted), 8),
        )

    @staticmethod
    def _validate_identifier(value: str) -> None:
        try:
            identifier = UUID(value)
        except ValueError:
            raise BudgetError("invalid budget session ID") from None
        if identifier.version != 4 or str(identifier) != value:
            raise BudgetError("invalid budget session ID")
