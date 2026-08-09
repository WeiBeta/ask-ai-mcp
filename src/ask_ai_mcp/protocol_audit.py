"""Prompt-free MCP protocol observability middleware."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult

_AUDIT_ENVIRONMENT_VARIABLE = "ASK_AI_MCP_PROTOCOL_AUDIT"
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_PROCESS_INSTANCE_ID = str(uuid4())
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProtocolAuditEvent:
    """One prompt-free MCP protocol event persisted by the local audit store."""

    timestamp: datetime
    process_instance_id: str
    session_id: str | None
    request_id: str | None
    configured_client_name: str | None
    reported_client_name: str | None
    client_version: str | None
    protocol_version: str | None
    method: str
    tool_name: str | None
    duration_ms: int
    status: str
    error_kind: str | None = None
    tool_count: int | None = None
    tool_surface_bytes: int | None = None
    tool_surface_sha256: str | None = None
    arguments_bytes: int | None = None
    result_bytes: int | None = None


class ProtocolAuditSink(Protocol):
    """Structural sink accepted by the middleware without coupling to SQLite."""

    def record_protocol_event(self, event: ProtocolAuditEvent) -> None: ...


def protocol_audit_enabled() -> bool:
    """Return whether prompt-free protocol auditing is enabled for this process."""

    value = os.environ.get(_AUDIT_ENVIRONMENT_VARIABLE, "1")
    return value.strip().lower() not in _FALSE_VALUES


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _payload_size(value: Any) -> int | None:
    try:
        return len(_canonical_json_bytes(_jsonable(value)))
    except (TypeError, ValueError):
        return None


def _tool_surface(tools: Sequence[Tool]) -> tuple[int, str]:
    wire_tools = []
    for tool in tools:
        wire_tool = tool.to_mcp_tool() if hasattr(tool, "to_mcp_tool") else tool
        wire_tools.append(_jsonable(wire_tool))
    payload = _canonical_json_bytes(wire_tools)
    return len(payload), hashlib.sha256(payload).hexdigest()


def _error_kind(error: BaseException) -> str:
    name = type(error).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "cancel" in name:
        return "cancelled"
    if isinstance(error, (TypeError, ValueError)):
        return "validation"
    if isinstance(error, (ConnectionError, OSError)):
        return "backend_unavailable"
    return "internal"


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _message_params(message: Any) -> Any:
    return getattr(message, "params", message)


class ProtocolAuditMiddleware(Middleware):
    """Record structural initialize, tools/list, and tools/call metadata.

    Prompt text, argument values, tool definitions, results, secrets, and exception
    messages are never persisted. Audit failures are fail-open and never alter the
    underlying MCP response.
    """

    def __init__(
        self,
        store_factory: Callable[[], ProtocolAuditSink],
        *,
        enabled: bool | None = None,
    ) -> None:
        self._store_factory = store_factory
        self._enabled = protocol_audit_enabled() if enabled is None else enabled
        self._client_metadata: dict[str, tuple[str | None, str | None]] = {}
        self._reported_write_failure = False

    @staticmethod
    def _context_ids(context: MiddlewareContext[Any]) -> tuple[str | None, str | None]:
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is None:
            return None, None
        session_id: str | None = None
        request_id: str | None = None
        with suppress(RuntimeError):
            session_id = fastmcp_context.session_id
        if fastmcp_context.request_context is not None:
            request_id = fastmcp_context.request_id
        return session_id, request_id

    def _client_fields(self, session_id: str | None) -> tuple[str | None, str | None]:
        if session_id is None:
            return None, None
        return self._client_metadata.get(session_id, (None, None))

    def _record(self, event: ProtocolAuditEvent) -> None:
        if not self._enabled:
            return
        try:
            self._store_factory().record_protocol_event(event)
        except Exception:
            if not self._reported_write_failure:
                _LOGGER.warning(
                    "MCP protocol audit write failed; request processing remains enabled",
                    exc_info=False,
                )
                self._reported_write_failure = True

    def _event(
        self,
        *,
        context: MiddlewareContext[Any],
        timestamp: datetime,
        method: str,
        duration_ms: int,
        status: str,
        reported_client_name: str | None = None,
        client_version: str | None = None,
        protocol_version: str | None = None,
        tool_name: str | None = None,
        error_kind: str | None = None,
        tool_count: int | None = None,
        tool_surface_bytes: int | None = None,
        tool_surface_sha256: str | None = None,
        arguments_bytes: int | None = None,
        result_bytes: int | None = None,
    ) -> ProtocolAuditEvent:
        session_id, request_id = self._context_ids(context)
        known_name, known_version = self._client_fields(session_id)
        return ProtocolAuditEvent(
            timestamp=timestamp,
            process_instance_id=_PROCESS_INSTANCE_ID,
            session_id=session_id,
            request_id=request_id,
            configured_client_name=os.environ.get("ASK_AI_MCP_CLIENT_NAME") or None,
            reported_client_name=reported_client_name or known_name,
            client_version=client_version or known_version,
            protocol_version=protocol_version,
            method=method,
            tool_name=tool_name,
            duration_ms=duration_ms,
            status=status,
            error_kind=error_kind,
            tool_count=tool_count,
            tool_surface_bytes=tool_surface_bytes,
            tool_surface_sha256=tool_surface_sha256,
            arguments_bytes=arguments_bytes,
            result_bytes=result_bytes,
        )

    async def on_initialize(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        timestamp = datetime.now(UTC)
        started_at = time.perf_counter()
        params = _message_params(context.message)
        client_info = getattr(params, "clientInfo", None)
        reported_name = getattr(client_info, "name", None)
        client_version = getattr(client_info, "version", None)
        protocol_version = getattr(params, "protocolVersion", None)
        session_id, _ = self._context_ids(context)
        if session_id is not None:
            self._client_metadata[session_id] = (reported_name, client_version)
        try:
            result = await call_next(context)
        except BaseException as error:
            self._record(
                self._event(
                    context=context,
                    timestamp=timestamp,
                    method="initialize",
                    duration_ms=_duration_ms(started_at),
                    status="error",
                    error_kind=_error_kind(error),
                    reported_client_name=reported_name,
                    client_version=client_version,
                    protocol_version=(
                        str(protocol_version) if protocol_version is not None else None
                    ),
                )
            )
            raise
        self._record(
            self._event(
                context=context,
                timestamp=timestamp,
                method="initialize",
                duration_ms=_duration_ms(started_at),
                status="success",
                reported_client_name=reported_name,
                client_version=client_version,
                protocol_version=str(protocol_version) if protocol_version is not None else None,
            )
        )
        return result

    async def on_list_tools(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Sequence[Tool]],
    ) -> Sequence[Tool]:
        timestamp = datetime.now(UTC)
        started_at = time.perf_counter()
        try:
            result = await call_next(context)
        except BaseException as error:
            self._record(
                self._event(
                    context=context,
                    timestamp=timestamp,
                    method="tools/list",
                    duration_ms=_duration_ms(started_at),
                    status="error",
                    error_kind=_error_kind(error),
                )
            )
            raise
        try:
            surface_bytes, surface_sha256 = _tool_surface(result)
        except (AttributeError, TypeError, ValueError):
            surface_bytes, surface_sha256 = None, None
        self._record(
            self._event(
                context=context,
                timestamp=timestamp,
                method="tools/list",
                duration_ms=_duration_ms(started_at),
                status="success",
                tool_count=len(result),
                tool_surface_bytes=surface_bytes,
                tool_surface_sha256=surface_sha256,
            )
        )
        return result

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        timestamp = datetime.now(UTC)
        started_at = time.perf_counter()
        params = _message_params(context.message)
        tool_name = getattr(params, "name", None)
        arguments_bytes = _payload_size(getattr(params, "arguments", None))
        try:
            result = await call_next(context)
        except BaseException as error:
            self._record(
                self._event(
                    context=context,
                    timestamp=timestamp,
                    method="tools/call",
                    duration_ms=_duration_ms(started_at),
                    status="error",
                    error_kind=_error_kind(error),
                    tool_name=tool_name,
                    arguments_bytes=arguments_bytes,
                )
            )
            raise
        self._record(
            self._event(
                context=context,
                timestamp=timestamp,
                method="tools/call",
                duration_ms=_duration_ms(started_at),
                status="error_result" if result.is_error else "success",
                tool_name=tool_name,
                arguments_bytes=arguments_bytes,
                result_bytes=_payload_size(result),
            )
        )
        return result
