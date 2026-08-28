"""Shared bounded timeout policy and prompt-free transport diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx

MAX_PROVIDER_READ_TIMEOUT_SECONDS = 14_400.0
_TRANSPORT_FAILURE_BY_EXCEPTION = {
    "RemoteProtocolError": "REMOTE_PROTOCOL",
    "LocalProtocolError": "LOCAL_PROTOCOL",
    "ProxyError": "PROXY",
    "ConnectError": "CONNECT",
    "ReadError": "READ_IO",
    "WriteError": "WRITE_IO",
    "CloseError": "CLOSE_IO",
    "NetworkError": "OTHER",
}


@dataclass(frozen=True, slots=True)
class ProviderTimeoutPolicy:
    """Keep slow inference bounded without confusing it with connect failures."""

    name: str
    connect_seconds: float
    read_seconds: float
    write_seconds: float
    pool_seconds: float

    def as_httpx(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_seconds,
            read=self.read_seconds,
            write=self.write_seconds,
            pool=self.pool_seconds,
        )

    def audit_metadata(self) -> dict[str, str | float]:
        return asdict(self)

    def status_metadata(self) -> dict[str, str | float]:
        metadata = self.audit_metadata()
        metadata["policy_name"] = metadata.pop("name")
        return metadata


# Core is currently a synchronous MCP call. Specialist jobs return an ID before
# inference and can therefore tolerate a longer bounded read without pinning the
# caller's tool request.
REMOTE_SYNC_GENERATION_TIMEOUT = ProviderTimeoutPolicy(
    name="remote_sync_generation_v1",
    connect_seconds=30.0,
    read_seconds=3_600.0,
    write_seconds=600.0,
    pool_seconds=30.0,
)
REMOTE_ASYNC_GENERATION_TIMEOUT = ProviderTimeoutPolicy(
    name="remote_async_generation_v1",
    connect_seconds=30.0,
    read_seconds=7_200.0,
    write_seconds=600.0,
    pool_seconds=30.0,
)
REMOTE_HEALTH_TIMEOUT = ProviderTimeoutPolicy(
    name="remote_health_v1",
    connect_seconds=30.0,
    read_seconds=30.0,
    write_seconds=30.0,
    pool_seconds=30.0,
)


def timeout_policy_with_read_seconds(
    base: ProviderTimeoutPolicy, read_seconds: float
) -> ProviderTimeoutPolicy:
    """Retain narrow constructor overrides used by offline transports/tests."""

    if not 0 < read_seconds <= MAX_PROVIDER_READ_TIMEOUT_SECONDS:
        raise ValueError("provider read timeout must be positive and no greater than 14400 seconds")
    return ProviderTimeoutPolicy(
        name=f"{base.name}_override",
        connect_seconds=base.connect_seconds,
        read_seconds=read_seconds,
        write_seconds=base.write_seconds,
        pool_seconds=base.pool_seconds,
    )


def timeout_phase(error: BaseException) -> str | None:
    if isinstance(error, httpx.ConnectTimeout):
        return "connect"
    if isinstance(error, httpx.ReadTimeout):
        return "read"
    if isinstance(error, httpx.WriteTimeout):
        return "write"
    if isinstance(error, httpx.PoolTimeout):
        return "pool"
    if isinstance(error, httpx.TimeoutException):
        return "unknown"
    return None


def transport_failure_kind(error: BaseException) -> str | None:
    """Classify content-free transport failures without logging exception messages."""

    if isinstance(error, (httpx.TimeoutException, httpx.HTTPStatusError)):
        return None
    for error_type in type(error).__mro__:
        selected = _TRANSPORT_FAILURE_BY_EXCEPTION.get(error_type.__name__)
        if selected is not None:
            return selected
    return "OTHER" if isinstance(error, httpx.RequestError) else None


def transport_failure_kind_from_audit(value: object) -> str | None:
    """Read the new field or safely backfill older prompt-free audit artifacts."""

    if not isinstance(value, dict):
        return None
    selected = value.get("transport_failure_kind")
    if isinstance(selected, str) and selected in set(_TRANSPORT_FAILURE_BY_EXCEPTION.values()):
        return selected
    exception_type = value.get("exception_type")
    return (
        _TRANSPORT_FAILURE_BY_EXCEPTION.get(exception_type)
        if isinstance(exception_type, str)
        else None
    )


def prompt_free_transport_audit(
    error: httpx.HTTPStatusError | httpx.RequestError,
    *,
    policy: ProviderTimeoutPolicy,
    elapsed_ms: int,
) -> dict[str, Any]:
    """Return only content-independent failure metadata."""

    status_code = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
    return {
        "status_code": status_code,
        "exception_type": type(error).__name__,
        "timeout_phase": timeout_phase(error),
        "transport_failure_kind": transport_failure_kind(error),
        "elapsed_ms": max(0, elapsed_ms),
        "timeout_policy": policy.audit_metadata(),
        "usage_observed": False,
        "upstream_progress_confirmed": False,
    }
