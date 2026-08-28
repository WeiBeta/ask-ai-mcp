from __future__ import annotations

import httpx
import pytest

from ask_ai_mcp.opencode import OpenCodeGoClient
from ask_ai_mcp.opencode_source import OpenCodeQwenMessagesClient
from ask_ai_mcp.provider_timeout import (
    MAX_PROVIDER_READ_TIMEOUT_SECONDS,
    REMOTE_ASYNC_GENERATION_TIMEOUT,
    REMOTE_HEALTH_TIMEOUT,
    REMOTE_SYNC_GENERATION_TIMEOUT,
    prompt_free_transport_audit,
    timeout_phase,
    timeout_policy_with_read_seconds,
    transport_failure_kind,
    transport_failure_kind_from_audit,
)


def test_remote_inference_timeout_policies_are_long_but_bounded() -> None:
    assert REMOTE_SYNC_GENERATION_TIMEOUT.status_metadata() == {
        "policy_name": "remote_sync_generation_v1",
        "connect_seconds": 30.0,
        "read_seconds": 3_600.0,
        "write_seconds": 600.0,
        "pool_seconds": 30.0,
    }
    assert REMOTE_ASYNC_GENERATION_TIMEOUT.status_metadata() == {
        "policy_name": "remote_async_generation_v1",
        "connect_seconds": 30.0,
        "read_seconds": 7_200.0,
        "write_seconds": 600.0,
        "pool_seconds": 30.0,
    }
    timeout = REMOTE_ASYNC_GENERATION_TIMEOUT.as_httpx()
    assert timeout.connect == 30.0
    assert timeout.read == 7_200.0
    assert timeout.write == 600.0
    assert timeout.pool == 30.0
    assert REMOTE_HEALTH_TIMEOUT.read_seconds == 30.0


def test_timeout_override_rejects_values_outside_status_contract() -> None:
    assert (
        timeout_policy_with_read_seconds(
            REMOTE_ASYNC_GENERATION_TIMEOUT,
            MAX_PROVIDER_READ_TIMEOUT_SECONDS,
        ).read_seconds
        == MAX_PROVIDER_READ_TIMEOUT_SECONDS
    )
    with pytest.raises(ValueError, match="no greater than 14400"):
        timeout_policy_with_read_seconds(
            REMOTE_ASYNC_GENERATION_TIMEOUT,
            MAX_PROVIDER_READ_TIMEOUT_SECONDS + 1,
        )


def test_timeout_diagnostics_are_phase_specific_and_prompt_free() -> None:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    error = httpx.ReadTimeout("PRIVATE_PROVIDER_OUTPUT", request=request)

    audit = prompt_free_transport_audit(
        error,
        policy=REMOTE_ASYNC_GENERATION_TIMEOUT,
        elapsed_ms=7_200_123,
    )

    assert timeout_phase(error) == "read"
    assert audit["timeout_phase"] == "read"
    assert audit["transport_failure_kind"] is None
    assert audit["elapsed_ms"] == 7_200_123
    assert audit["usage_observed"] is False
    assert audit["upstream_progress_confirmed"] is False
    assert "PRIVATE_PROVIDER_OUTPUT" not in str(audit)


def test_remote_protocol_failure_is_content_free_and_legacy_backfillable() -> None:
    request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    error = httpx.RemoteProtocolError("PRIVATE_DISCONNECT_DETAIL", request=request)

    audit = prompt_free_transport_audit(
        error,
        policy=REMOTE_ASYNC_GENERATION_TIMEOUT,
        elapsed_ms=334_282,
    )

    assert transport_failure_kind(error) == "REMOTE_PROTOCOL"
    assert audit["transport_failure_kind"] == "REMOTE_PROTOCOL"
    assert transport_failure_kind_from_audit(audit) == "REMOTE_PROTOCOL"
    assert (
        transport_failure_kind_from_audit({"exception_type": "RemoteProtocolError"})
        == "REMOTE_PROTOCOL"
    )
    assert "PRIVATE_DISCONNECT_DETAIL" not in str(audit)


def test_core_and_remote_perception_use_the_intended_policy() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(500))
    core = OpenCodeGoClient(
        api_key_provider=lambda: "opaque-test-key",
        transport=transport,
    )
    perception = OpenCodeQwenMessagesClient(
        api_key_provider=lambda: "opaque-test-key",
        transport=transport,
    )

    assert core.timeout.read == 3_600
    assert perception.timeout.read == 7_200
