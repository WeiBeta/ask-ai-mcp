"""Tests for prompt-free MCP protocol observability."""

from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent, Tool

from ask_ai_mcp.protocol_audit import ProtocolAuditEvent, ProtocolAuditMiddleware
from ask_ai_mcp.usage import UsageStore


class MemorySink:
    def __init__(self) -> None:
        self.events: list[ProtocolAuditEvent] = []

    def record_protocol_event(self, event: ProtocolAuditEvent) -> None:
        self.events.append(event)


class FailingSink:
    def record_protocol_event(self, event: ProtocolAuditEvent) -> None:
        raise sqlite3.OperationalError("synthetic write failure containing secret-marker")


class FakeFastMCPContext:
    request_context = object()
    session_id = "session-1"
    request_id = "request-1"


def middleware_context(message: Any) -> MiddlewareContext[Any]:
    return MiddlewareContext(
        message=message,
        method="test",
        fastmcp_context=FakeFastMCPContext(),
    )


def sample_event() -> ProtocolAuditEvent:
    return ProtocolAuditEvent(
        timestamp=datetime.now(UTC),
        process_instance_id="process-1",
        session_id="session-1",
        request_id="request-1",
        configured_client_name="codex_desktop",
        reported_client_name="codex",
        client_version="1.2.3",
        protocol_version="2025-06-18",
        method="tools/list",
        tool_name=None,
        duration_ms=7,
        status="success",
        tool_count=16,
        tool_surface_bytes=1234,
        tool_surface_sha256="a" * 64,
    )


def test_usage_store_persists_protocol_event_without_payloads(tmp_path: Path) -> None:
    database = tmp_path / "usage.db"
    store = UsageStore(database)
    store.record_protocol_event(sample_event())

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM mcp_protocol_events").fetchone()
        columns = {
            str(item[1])
            for item in connection.execute("PRAGMA table_info(mcp_protocol_events)").fetchall()
        }

    assert row is not None
    assert row["method"] == "tools/list"
    assert row["tool_count"] == 16
    assert row["tool_surface_bytes"] == 1234
    assert row["tool_surface_sha256"] == "a" * 64
    assert "arguments" not in columns
    assert "result" not in columns
    assert "prompt" not in columns


def test_initialize_records_only_standard_client_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASK_AI_MCP_CLIENT_NAME", "codex_desktop")
    sink = MemorySink()
    middleware = ProtocolAuditMiddleware(lambda: sink, enabled=True)
    message = SimpleNamespace(
        params=SimpleNamespace(
            protocolVersion="2025-06-18",
            clientInfo=SimpleNamespace(name="codex", version="9.9"),
            capabilities={"secret-marker": "must-not-be-recorded"},
        )
    )

    async def call_next(context: MiddlewareContext[Any]) -> str:
        return "initialized"

    result = asyncio.run(middleware.on_initialize(middleware_context(message), call_next))

    assert result == "initialized"
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.configured_client_name == "codex_desktop"
    assert event.reported_client_name == "codex"
    assert event.client_version == "9.9"
    assert event.protocol_version == "2025-06-18"
    assert "secret-marker" not in repr(event)


def test_list_tools_records_stable_surface_size_and_hash() -> None:
    sink = MemorySink()
    middleware = ProtocolAuditMiddleware(lambda: sink, enabled=True)
    tools = [
        Tool(
            name="diagnostic_status",
            description="Return fixed diagnostic metadata.",
            inputSchema={"type": "object", "properties": {}},
        )
    ]

    async def call_next(context: MiddlewareContext[Any]) -> list[Tool]:
        return tools

    first = asyncio.run(middleware.on_list_tools(middleware_context(object()), call_next))
    second = asyncio.run(middleware.on_list_tools(middleware_context(object()), call_next))

    assert first == tools
    assert second == tools
    assert sink.events[0].tool_count == 1
    assert sink.events[0].tool_surface_bytes is not None
    assert sink.events[0].tool_surface_sha256 == sink.events[1].tool_surface_sha256

    changed_tools = [
        Tool(
            name="diagnostic_status",
            description="A changed description must change the surface hash.",
            inputSchema={"type": "object", "properties": {}},
        )
    ]

    async def changed_call_next(context: MiddlewareContext[Any]) -> list[Tool]:
        return changed_tools

    asyncio.run(middleware.on_list_tools(middleware_context(object()), changed_call_next))
    assert sink.events[2].tool_surface_sha256 != sink.events[0].tool_surface_sha256


def test_call_tool_records_sizes_but_not_argument_or_result_content() -> None:
    sink = MemorySink()
    middleware = ProtocolAuditMiddleware(lambda: sink, enabled=True)
    message = SimpleNamespace(
        name="diagnostic_status",
        arguments={"token": "secret-marker"},
    )
    tool_result = ToolResult(
        content=[TextContent(type="text", text="private-result-marker")],
    )

    async def call_next(context: MiddlewareContext[Any]) -> ToolResult:
        return tool_result

    result = asyncio.run(middleware.on_call_tool(middleware_context(message), call_next))

    assert result is tool_result
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.tool_name == "diagnostic_status"
    assert event.arguments_bytes is not None
    assert event.result_bytes is not None
    assert "secret-marker" not in repr(event)
    assert "private-result-marker" not in repr(event)


def test_tool_failure_records_category_without_exception_message() -> None:
    sink = MemorySink()
    middleware = ProtocolAuditMiddleware(lambda: sink, enabled=True)
    message = SimpleNamespace(name="diagnostic_status", arguments={})

    async def call_next(context: MiddlewareContext[Any]) -> ToolResult:
        raise TimeoutError("secret-timeout-detail")

    with pytest.raises(TimeoutError, match="secret-timeout-detail"):
        asyncio.run(middleware.on_call_tool(middleware_context(message), call_next))

    assert sink.events[0].status == "error"
    assert sink.events[0].error_kind == "timeout"
    assert "secret-timeout-detail" not in repr(sink.events[0])


def test_audit_write_failure_is_fail_open() -> None:
    middleware = ProtocolAuditMiddleware(lambda: FailingSink(), enabled=True)
    message = SimpleNamespace(name="diagnostic_status", arguments={})
    tool_result = ToolResult(content=[TextContent(type="text", text="ok")])

    async def call_next(context: MiddlewareContext[Any]) -> ToolResult:
        return tool_result

    result = asyncio.run(middleware.on_call_tool(middleware_context(message), call_next))

    assert result is tool_result


def test_disabled_audit_does_not_construct_store() -> None:
    constructed = False

    def store_factory() -> MemorySink:
        nonlocal constructed
        constructed = True
        return MemorySink()

    middleware = ProtocolAuditMiddleware(store_factory, enabled=False)
    message = SimpleNamespace(name="diagnostic_status", arguments={})
    tool_result = ToolResult(content=[TextContent(type="text", text="ok")])

    async def call_next(context: MiddlewareContext[Any]) -> ToolResult:
        return tool_result

    result = asyncio.run(middleware.on_call_tool(middleware_context(message), call_next))

    assert result is tool_result
    assert constructed is False


def test_fastmcp_client_records_initialize_list_and_call(tmp_path: Path) -> None:
    database = tmp_path / "usage.db"
    store = UsageStore(database)
    test_server = FastMCP("Protocol audit test")
    test_server.add_middleware(ProtocolAuditMiddleware(lambda: store, enabled=True))

    @test_server.tool
    def diagnostic_status() -> dict[str, bool]:
        return {"ready": True}

    async def exercise_server() -> None:
        async with Client(test_server) as client:
            await client.list_tools()
            await client.call_tool("diagnostic_status")

    asyncio.run(exercise_server())

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT method, tool_name, status, tool_count, tool_surface_bytes,
                   arguments_bytes, result_bytes, reported_client_name
            FROM mcp_protocol_events
            ORDER BY id
            """
        ).fetchall()

    methods = [str(row["method"]) for row in rows]
    assert methods == ["initialize", "tools/list", "tools/call"]
    assert all(row["reported_client_name"] == "mcp" for row in rows)
    assert rows[1]["tool_count"] == 1
    assert rows[1]["tool_surface_bytes"] > 0
    assert rows[2]["tool_name"] == "diagnostic_status"
    assert rows[2]["arguments_bytes"] > 0
    assert rows[2]["result_bytes"] > 0
    assert all(row["status"] == "success" for row in rows)


def test_two_stores_can_write_protocol_events_concurrently(tmp_path: Path) -> None:
    database = tmp_path / "usage.db"
    stores = [UsageStore(database), UsageStore(database)]

    def record(index: int) -> None:
        stores[index % 2].record_protocol_event(
            replace_event_request_id(sample_event(), f"request-{index}")
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(record, range(20)))

    with sqlite3.connect(database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM mcp_protocol_events").fetchone()[0]

    assert count == 20


def replace_event_request_id(event: ProtocolAuditEvent, request_id: str) -> ProtocolAuditEvent:
    return ProtocolAuditEvent(
        timestamp=event.timestamp,
        process_instance_id=event.process_instance_id,
        session_id=event.session_id,
        request_id=request_id,
        configured_client_name=event.configured_client_name,
        reported_client_name=event.reported_client_name,
        client_version=event.client_version,
        protocol_version=event.protocol_version,
        method=event.method,
        tool_name=event.tool_name,
        duration_ms=event.duration_ms,
        status=event.status,
        error_kind=event.error_kind,
        tool_count=event.tool_count,
        tool_surface_bytes=event.tool_surface_bytes,
        tool_surface_sha256=event.tool_surface_sha256,
        arguments_bytes=event.arguments_bytes,
        result_bytes=event.result_bytes,
    )
