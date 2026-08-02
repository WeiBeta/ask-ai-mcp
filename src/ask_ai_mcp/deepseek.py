"""Narrow DeepSeek V4 client for schema-constrained tool candidates."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from ask_ai_mcp import __version__
from ask_ai_mcp.credentials import CredentialStore
from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.models import (
    CandidateRepairFeedback,
    DeepSeekModel,
    ToolBuildSpec,
    ToolCandidatePayload,
    ToolCandidateResult,
    UsageEvent,
)
from ask_ai_mcp.policy import PolicyViolation, require_tool_spec_allowed
from ask_ai_mcp.pricing import calculate_cost_estimate, load_peak_pricing_effective_at
from ask_ai_mcp.usage import UsageStore

DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
MAX_OUTPUT_TOKENS = 32_768

SYSTEM_PROMPT = """
You are an untrusted auxiliary toolsmith. Return one JSON object only. You may
create bounded preprocessing, extraction, conversion, validation, fixture, and
test code from the supplied specification. Never draft or co-author final
document prose, decide facts, resolve source conflicts, or generate conclusions
for direct delivery. Do not request or use network access, credentials,
environment variables, subprocesses, dynamic package installation, eval, exec,
or paths outside a disposable job workspace. Original source files are
read-only. The controller will validate and review every candidate.

The declared Python entrypoint must define exactly the public callable
`run(request, input_dir, output_dir)`. `request` is a JSON object, `input_dir`
is a pathlib.Path containing staged read-only file copies, and `output_dir` is
a pathlib.Path dedicated to this run. The callable must return a JSON-
serializable value. Do not add a command-line or shell interface.
""".strip()


class DeepSeekClientError(RuntimeError):
    """Safe, response-body-free client error."""


class ModelEscalationRequired(PolicyViolation):
    """Raised when V4 Pro was selected without explicit host approval."""


def _usage_counts(data: dict[str, Any]) -> tuple[int, int, int, int]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0, 0
    details = usage.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens", 0) if isinstance(details, dict) else 0

    def token_count(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    return (
        token_count(usage.get("prompt_cache_hit_tokens")),
        token_count(usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens"))),
        token_count(usage.get("completion_tokens")),
        token_count(reasoning),
    )


class DeepSeekClient:
    """Create untrusted candidates without exposing an arbitrary chat surface."""

    def __init__(
        self,
        *,
        api_key_provider: Callable[[], str] | None = None,
        usage_store: UsageStore | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 120.0,
        clock: Callable[[], datetime] | None = None,
        peak_pricing_effective_at: datetime | None = None,
    ) -> None:
        self.api_key_provider = api_key_provider or CredentialStore().get_api_key
        self.usage_store = usage_store or UsageStore()
        self.transport = transport
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.peak_pricing_effective_at = (
            peak_pricing_effective_at
            if peak_pricing_effective_at is not None
            else load_peak_pricing_effective_at()
        )

    def build_candidate(
        self,
        spec: ToolBuildSpec,
        *,
        client_name: str,
        allow_pro: bool = False,
    ) -> ToolCandidateResult:
        require_tool_spec_allowed(spec)
        if spec.model is DeepSeekModel.PRO and not allow_pro:
            raise ModelEscalationRequired("V4 Pro requires explicit Opus/Sol escalation")

        return self._complete_candidate(
            spec=spec,
            client_name=client_name,
            request_body=self._request_body(spec),
            task_kind="tool_build",
            retries=0,
        )

    def repair_candidate(
        self,
        spec: ToolBuildSpec,
        previous: ToolCandidateResult,
        feedback: CandidateRepairFeedback,
        *,
        client_name: str,
        allow_pro: bool = False,
    ) -> ToolCandidateResult:
        """Repair one candidate from bounded, source-document-free diagnostics."""
        require_tool_spec_allowed(spec)
        if spec.model is DeepSeekModel.PRO and not allow_pro:
            raise ModelEscalationRequired("V4 Pro requires explicit Opus/Sol escalation")
        if previous.model is not spec.model:
            raise DeepSeekClientError("repair model does not match the tool specification")
        if candidate_payload_sha256(previous.payload) != previous.candidate_sha256:
            raise DeepSeekClientError("previous candidate hash does not match its content")

        return self._complete_candidate(
            spec=spec,
            client_name=client_name,
            request_body=self._repair_request_body(spec, previous, feedback),
            task_kind="tool_repair",
            retries=feedback.repair_round,
        )

    def _complete_candidate(
        self,
        *,
        spec: ToolBuildSpec,
        client_name: str,
        request_body: dict[str, Any],
        task_kind: str,
        retries: int,
    ) -> ToolCandidateResult:
        started = perf_counter()
        priced_at = self.clock()
        try:
            data = self._request(request_body)
        except httpx.HTTPStatusError as error:
            self._record_failure(
                spec, client_name, started, priced_at, "http_error", task_kind, retries
            )
            raise DeepSeekClientError(
                f"DeepSeek API returned HTTP {error.response.status_code}"
            ) from None
        except httpx.HTTPError:
            self._record_failure(
                spec, client_name, started, priced_at, "transport_error", task_kind, retries
            )
            raise DeepSeekClientError("DeepSeek API transport failed") from None
        except (TypeError, ValueError):
            self._record_failure(
                spec, client_name, started, priced_at, "invalid_response", task_kind, retries
            )
            raise DeepSeekClientError("DeepSeek returned an invalid API response") from None

        cache_hit, cache_miss, completion, reasoning = _usage_counts(data)
        try:
            payload = self._parse_candidate(data)
        except (KeyError, TypeError, ValueError, ValidationError):
            self._record_usage(
                spec=spec,
                client_name=client_name,
                started=started,
                priced_at=priced_at,
                status="invalid_response",
                cache_hit=cache_hit,
                cache_miss=cache_miss,
                completion=completion,
                reasoning=reasoning,
                task_kind=task_kind,
                retries=retries,
            )
            raise DeepSeekClientError("DeepSeek returned an invalid candidate response") from None

        candidate_hash = candidate_payload_sha256(payload)
        self._record_usage(
            spec=spec,
            client_name=client_name,
            started=started,
            priced_at=priced_at,
            status="success",
            cache_hit=cache_hit,
            cache_miss=cache_miss,
            completion=completion,
            reasoning=reasoning,
            candidate_hash=candidate_hash,
            task_kind=task_kind,
            retries=retries,
        )
        return ToolCandidateResult(
            candidate_sha256=candidate_hash,
            model=spec.model,
            thinking_enabled=True,
            payload=payload,
        )

    def _request_body(self, spec: ToolBuildSpec) -> dict[str, Any]:
        schema = json.dumps(ToolCandidatePayload.model_json_schema(), ensure_ascii=False)
        specification = spec.model_dump_json(exclude_none=True)
        return {
            "model": spec.model.value,
            "messages": [
                {
                    "role": "system",
                    "content": f"{SYSTEM_PROMPT}\nRequired JSON schema:\n{schema}",
                },
                {
                    "role": "user",
                    "content": (
                        "Generate a candidate that satisfies this bounded specification. "
                        f"Return JSON only. Specification JSON:\n{specification}"
                    ),
                },
            ],
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "response_format": {"type": "json_object"},
            "max_tokens": MAX_OUTPUT_TOKENS,
            "stream": False,
        }

    def _repair_request_body(
        self,
        spec: ToolBuildSpec,
        previous: ToolCandidateResult,
        feedback: CandidateRepairFeedback,
    ) -> dict[str, Any]:
        schema = json.dumps(ToolCandidatePayload.model_json_schema(), ensure_ascii=False)
        specification = spec.model_dump_json(exclude_none=True)
        candidate = previous.payload.model_dump_json(exclude_none=True)
        diagnostics = feedback.model_dump_json(exclude_none=True)
        return {
            "model": spec.model.value,
            "messages": [
                {
                    "role": "system",
                    "content": f"{SYSTEM_PROMPT}\nRequired JSON schema:\n{schema}",
                },
                {
                    "role": "user",
                    "content": (
                        "Repair the bounded candidate using only the supplied diagnostics. "
                        "Do not expand its duties, dependencies, or access. Return the complete "
                        "replacement candidate as JSON only. The diagnostics contain no source "
                        "documents.\n"
                        f"Specification JSON:\n{specification}\n"
                        f"Previous candidate JSON:\n{candidate}\n"
                        f"Bounded failure diagnostics JSON:\n{diagnostics}"
                    ),
                },
            ],
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "response_format": {"type": "json_object"},
            "max_tokens": MAX_OUTPUT_TOKENS,
            "stream": False,
        }

    def _request(self, request_body: dict[str, Any]) -> dict[str, Any]:
        api_key = self.api_key_provider()
        with httpx.Client(
            transport=self.transport,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.post(
                DEEPSEEK_CHAT_COMPLETIONS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": f"ask-ai-mcp/{__version__}",
                },
                json=request_body,
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError("DeepSeek returned a non-object response")
        return data

    @staticmethod
    def _parse_candidate(data: dict[str, Any]) -> ToolCandidatePayload:
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("candidate JSON was truncated")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("candidate content is empty")
        return ToolCandidatePayload.model_validate(json.loads(content))

    def _record_failure(
        self,
        spec: ToolBuildSpec,
        client_name: str,
        started: float,
        priced_at: datetime,
        status: Literal["http_error", "transport_error", "invalid_response"],
        task_kind: str,
        retries: int,
    ) -> None:
        self._record_usage(
            spec=spec,
            client_name=client_name,
            started=started,
            priced_at=priced_at,
            status=status,
            cache_hit=0,
            cache_miss=0,
            completion=0,
            reasoning=0,
            task_kind=task_kind,
            retries=retries,
        )

    def _record_usage(
        self,
        *,
        spec: ToolBuildSpec,
        client_name: str,
        started: float,
        priced_at: datetime,
        status: str,
        cache_hit: int,
        cache_miss: int,
        completion: int,
        reasoning: int,
        candidate_hash: str | None = None,
        task_kind: str = "tool_build",
        retries: int = 0,
    ) -> None:
        cost = calculate_cost_estimate(
            spec.model,
            prompt_cache_hit_tokens=cache_hit,
            prompt_cache_miss_tokens=cache_miss,
            completion_tokens=completion,
            priced_at=priced_at,
            peak_pricing_effective_at=self.peak_pricing_effective_at,
        )
        self.usage_store.record(
            UsageEvent(
                client_name=client_name,
                task_kind=task_kind,
                model=spec.model,
                thinking_enabled=True,
                priced_at=cost.priced_at,
                pricing_band=cost.pricing_band,
                pricing_multiplier=cost.pricing_multiplier,
                pricing_schedule_version=cost.pricing_schedule_version,
                cache_hit_price_cny_per_million=cost.prices.cache_hit_input,
                cache_miss_price_cny_per_million=cost.prices.cache_miss_input,
                output_price_cny_per_million=cost.prices.output,
                prompt_cache_hit_tokens=cache_hit,
                prompt_cache_miss_tokens=cache_miss,
                completion_tokens=completion,
                reasoning_tokens=reasoning,
                estimated_cost_cny=cost.cost_cny,
                latency_ms=max(0, round((perf_counter() - started) * 1_000)),
                retries=retries,
                status=status,
                candidate_hash=candidate_hash,
            )
        )
