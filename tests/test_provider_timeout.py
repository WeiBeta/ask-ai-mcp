from __future__ import annotations

import httpx

from ask_ai_mcp.opencode import OpenCodeGoClient
from ask_ai_mcp.opencode_source import OpenCodeQwenMessagesClient
from ask_ai_mcp.provider_timeout import (
    REMOTE_ASYNC_GENERATION_TIMEOUT,
    REMOTE_HEALTH_TIMEOUT,
    REMOTE_SYNC_GENERATION_TIMEOUT,
    prompt_free_transport_audit,
    timeout_phase,
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
    assert audit["elapsed_ms"] == 7_200_123
    assert audit["usage_observed"] is False
    assert audit["upstream_progress_confirmed"] is False
    assert "PRIVATE_PROVIDER_OUTPUT" not in str(audit)


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
