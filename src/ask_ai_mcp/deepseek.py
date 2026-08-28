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
from ask_ai_mcp.candidate_patch import CandidatePatchError, apply_candidate_patch
from ask_ai_mcp.credentials import CredentialStore
from ask_ai_mcp.hashing import candidate_payload_sha256, sha256_text
from ask_ai_mcp.models import (
    CandidatePatchDraftPayload,
    CandidatePatchPayload,
    CandidateRepairFeedback,
    DeepSeekModel,
    ModelProvider,
    RepairKind,
    ToolBuildSpec,
    ToolCandidatePayload,
    ToolCandidateResult,
    UsageEvent,
)
from ask_ai_mcp.opencode_generation import WIDE_MAX_OUTPUT_TOKENS
from ask_ai_mcp.policy import PolicyViolation, require_tool_spec_allowed
from ask_ai_mcp.pricing import calculate_cost_estimate, load_peak_pricing_effective_at
from ask_ai_mcp.provider_timeout import (
    REMOTE_SYNC_GENERATION_TIMEOUT,
    ProviderTimeoutPolicy,
    timeout_policy_with_read_seconds,
)
from ask_ai_mcp.usage import UsageStore

DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
INITIAL_MAX_OUTPUT_TOKENS = WIDE_MAX_OUTPUT_TOKENS
STATIC_REPAIR_MAX_OUTPUT_TOKENS = 4_096
SEMANTIC_REPAIR_MAX_OUTPUT_TOKENS = WIDE_MAX_OUTPUT_TOKENS
PROMPT_TEMPLATE_VERSION = "toolsmith-json-v2"
BUILD_INSTRUCTION = (
    "Generate a candidate that satisfies this bounded specification. "
    "Return JSON only. Specification JSON:\n"
)
REGENERATION_INSTRUCTION = (
    "Regenerate the complete candidate because the previous API response was empty "
    "or invalid JSON. Return JSON only and satisfy every specification and test-file "
    "requirement. Specification JSON:\n"
)
REPAIR_INSTRUCTION = (
    "Repair the bounded candidate using only the supplied diagnostics. "
    "Do not expand its duties, dependencies, or access. Return the complete "
    "replacement candidate as JSON only. The diagnostics contain no source "
    "documents.\n"
)
STATIC_REPAIR_INSTRUCTION = (
    "Repair only the reported static-policy findings. Return a small hash-bound JSON patch, "
    "not a complete candidate. Each edit must identify an existing Python file and provide "
    "old_text that occurs exactly once plus its replacement new_text. Do not add, remove, or "
    "rename files, and do not change unrelated behavior. Return exactly the top-level "
    "edits field; summary is optional metadata. The controller binds the edit set to the "
    "exact candidate hash. The diagnostics contain no source documents.\n"
)

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

Every initial candidate and complete semantic-test replacement must contain
the declared entrypoint and at least one discoverable stdlib unittest file
named `test_*.py`. Tests must define a `unittest.TestCase` subclass with one or
more methods named `test_*`; plain pytest-style functions are not discoverable
by the runner. Tests may use `tempfile.TemporaryDirectory` and pathlib to make
synthetic input and output directories. Do not use pytest, absolute path
literals, external packages not listed in the specification, or omit tests
when returning a repaired candidate.

A static-policy repair is the sole exception: when the required schema contains
`edits`, return only that bounded edit set. The controller binds it to the exact
previous candidate hash, applies exact unique replacements, and revalidates the
complete candidate from scratch.
""".strip()


def prompt_template_sha256() -> str:
    """Hash controller-owned instructions and the exact structured output schema."""

    schema = json.dumps(
        ToolCandidatePayload.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    templates = "\n".join(
        [
            SYSTEM_PROMPT,
            schema,
            json.dumps(
                CandidatePatchDraftPayload.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            BUILD_INSTRUCTION,
            REGENERATION_INSTRUCTION,
            REPAIR_INSTRUCTION,
            STATIC_REPAIR_INSTRUCTION,
        ]
    )
    return sha256_text(templates)


class DeepSeekClientError(RuntimeError):
    """Safe, response-body-free client error."""


class InvalidCandidateResponse(DeepSeekClientError):
    """Raised for an empty, truncated, or schema-invalid candidate response."""


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

    provider = ModelProvider.DEEPSEEK
    provider_runtime = "chat_completions"
    api_label = "DeepSeek API"
    response_label = "DeepSeek"

    def __init__(
        self,
        *,
        api_key_provider: Callable[[], str] | None = None,
        usage_store: UsageStore | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float | None = None,
        timeout_policy: ProviderTimeoutPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        peak_pricing_effective_at: datetime | None = None,
    ) -> None:
        self.api_key_provider = api_key_provider or CredentialStore().get_api_key
        self.usage_store = usage_store or UsageStore()
        self.transport = transport
        self.timeout_policy = timeout_policy or (
            timeout_policy_with_read_seconds(REMOTE_SYNC_GENERATION_TIMEOUT, timeout_seconds)
            if timeout_seconds is not None
            else REMOTE_SYNC_GENERATION_TIMEOUT
        )
        self.timeout = self.timeout_policy.as_httpx()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.peak_pricing_effective_at = (
            peak_pricing_effective_at
            if peak_pricing_effective_at is not None
            else load_peak_pricing_effective_at()
        )

    @staticmethod
    def replay_prompt_metadata() -> tuple[str, str]:
        """Return reconstructable prompt identity without returning prompt content."""

        return PROMPT_TEMPLATE_VERSION, prompt_template_sha256()

    def build_candidate(
        self,
        spec: ToolBuildSpec,
        *,
        client_name: str,
        allow_pro: bool = False,
        budget_session_id: str | None = None,
        lifecycle_id: str | None = None,
    ) -> ToolCandidateResult:
        require_tool_spec_allowed(spec)
        if spec.model is DeepSeekModel.PRO and not allow_pro:
            raise ModelEscalationRequired("V4 Pro requires explicit Opus/Sol escalation")

        return self._complete_candidate(
            spec=spec,
            client_name=client_name,
            request_body=self._request_body(
                spec,
                thinking_enabled=True,
                max_output_tokens=INITIAL_MAX_OUTPUT_TOKENS,
            ),
            task_kind="tool_build",
            retries=0,
            thinking_enabled=True,
            budget_session_id=budget_session_id,
            lifecycle_id=lifecycle_id,
        )

    def regenerate_candidate(
        self,
        spec: ToolBuildSpec,
        *,
        client_name: str,
        allow_pro: bool = False,
        budget_session_id: str | None = None,
        lifecycle_id: str | None = None,
    ) -> ToolCandidateResult:
        """Retry one invalid/empty structured response without prior candidate content."""
        require_tool_spec_allowed(spec)
        if spec.model is DeepSeekModel.PRO and not allow_pro:
            raise ModelEscalationRequired("V4 Pro requires explicit Opus/Sol escalation")
        body = self._request_body(
            spec,
            thinking_enabled=True,
            max_output_tokens=INITIAL_MAX_OUTPUT_TOKENS,
        )
        body["messages"][1]["content"] = REGENERATION_INSTRUCTION + spec.model_dump_json(
            exclude_none=True
        )
        return self._complete_candidate(
            spec=spec,
            client_name=client_name,
            request_body=body,
            task_kind="tool_regeneration",
            retries=1,
            thinking_enabled=True,
            budget_session_id=budget_session_id,
            lifecycle_id=lifecycle_id,
        )

    def repair_candidate(
        self,
        spec: ToolBuildSpec,
        previous: ToolCandidateResult,
        feedback: CandidateRepairFeedback,
        *,
        client_name: str,
        allow_pro: bool = False,
        thinking_enabled: bool = True,
        max_output_tokens: int = SEMANTIC_REPAIR_MAX_OUTPUT_TOKENS,
        budget_session_id: str | None = None,
        lifecycle_id: str | None = None,
    ) -> ToolCandidateResult:
        """Repair one candidate from bounded, source-document-free diagnostics."""
        require_tool_spec_allowed(spec)
        if spec.model is DeepSeekModel.PRO and not allow_pro:
            raise ModelEscalationRequired("V4 Pro requires explicit Opus/Sol escalation")
        if previous.model is not spec.model:
            raise DeepSeekClientError("repair model does not match the tool specification")
        if candidate_payload_sha256(previous.payload) != previous.candidate_sha256:
            raise DeepSeekClientError("previous candidate hash does not match its content")

        static_patch = feedback.repair_kind is RepairKind.STATIC_POLICY
        return self._complete_candidate(
            spec=spec,
            client_name=client_name,
            request_body=self._repair_request_body(
                spec,
                previous,
                feedback,
                thinking_enabled=thinking_enabled,
                max_output_tokens=max_output_tokens,
            ),
            task_kind="tool_repair",
            retries=feedback.repair_round,
            thinking_enabled=thinking_enabled,
            budget_session_id=budget_session_id,
            lifecycle_id=lifecycle_id,
            patch_base=previous.payload if static_patch else None,
        )

    def _complete_candidate(
        self,
        *,
        spec: ToolBuildSpec,
        client_name: str,
        request_body: dict[str, Any],
        task_kind: str,
        retries: int,
        thinking_enabled: bool,
        budget_session_id: str | None,
        lifecycle_id: str | None,
        patch_base: ToolCandidatePayload | None = None,
    ) -> ToolCandidateResult:
        started = perf_counter()
        priced_at = self.clock()
        request_chars = sum(
            len(str(message.get("content", "")))
            for message in request_body.get("messages", [])
            if isinstance(message, dict)
        )
        try:
            data = self._request(request_body)
        except httpx.HTTPStatusError as error:
            self._record_failure(
                spec,
                client_name,
                started,
                priced_at,
                "http_error",
                task_kind,
                retries,
                thinking_enabled,
                budget_session_id,
                lifecycle_id,
                request_chars,
            )
            raise DeepSeekClientError(
                f"{self.api_label} returned HTTP {error.response.status_code}"
            ) from None
        except httpx.HTTPError:
            self._record_failure(
                spec,
                client_name,
                started,
                priced_at,
                "transport_error",
                task_kind,
                retries,
                thinking_enabled,
                budget_session_id,
                lifecycle_id,
                request_chars,
            )
            raise DeepSeekClientError(f"{self.api_label} transport failed") from None
        except (TypeError, ValueError):
            self._record_failure(
                spec,
                client_name,
                started,
                priced_at,
                "invalid_response",
                task_kind,
                retries,
                thinking_enabled,
                budget_session_id,
                lifecycle_id,
                request_chars,
            )
            raise DeepSeekClientError(
                f"{self.response_label} returned an invalid API response"
            ) from None

        cache_hit, cache_miss, completion, reasoning = _usage_counts(data)
        response_chars = self._response_chars(data)
        try:
            if patch_base is None:
                payload = self._parse_candidate(data)
            else:
                payload = apply_candidate_patch(
                    patch_base,
                    self._parse_patch(data, candidate_payload_sha256(patch_base)),
                )
        except (IndexError, KeyError, TypeError, ValueError, ValidationError) as error:
            failure_status = self._structured_failure_status(error, patch_base is not None)
            failure_detail = self._structured_failure_detail(error, patch_base is not None)
            self._record_usage(
                spec=spec,
                client_name=client_name,
                started=started,
                priced_at=priced_at,
                status=failure_status,
                cache_hit=cache_hit,
                cache_miss=cache_miss,
                completion=completion,
                reasoning=reasoning,
                task_kind=task_kind,
                retries=retries,
                thinking_enabled=thinking_enabled,
                budget_session_id=budget_session_id,
                lifecycle_id=lifecycle_id,
                request_chars=request_chars,
                response_chars=response_chars,
            )
            raise InvalidCandidateResponse(
                f"{self.response_label} returned an invalid candidate response "
                f"({failure_status}{failure_detail})"
            ) from None

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
            thinking_enabled=thinking_enabled,
            budget_session_id=budget_session_id,
            lifecycle_id=lifecycle_id,
            request_chars=request_chars,
            response_chars=response_chars,
        )
        return ToolCandidateResult(
            candidate_sha256=candidate_hash,
            model=spec.model,
            provider=self.provider,
            provider_model_id=self._provider_model_id(spec, request_body),
            provider_runtime=self._provider_runtime(request_body),
            thinking_enabled=thinking_enabled,
            payload=payload,
        )

    def _request_body(
        self,
        spec: ToolBuildSpec,
        *,
        thinking_enabled: bool,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        schema = json.dumps(ToolCandidatePayload.model_json_schema(), ensure_ascii=False)
        specification = spec.model_dump_json(exclude_none=True)
        body = {
            "model": spec.model.value,
            "messages": [
                {
                    "role": "system",
                    "content": f"{SYSTEM_PROMPT}\nRequired JSON schema:\n{schema}",
                },
                {
                    "role": "user",
                    "content": BUILD_INSTRUCTION + specification,
                },
            ],
            "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        if thinking_enabled:
            body["reasoning_effort"] = "high"
        return body

    def _provider_model_id(
        self, spec: ToolBuildSpec, request_body: dict[str, Any] | None = None
    ) -> str:
        return spec.model.value

    def _provider_runtime(self, request_body: dict[str, Any] | None = None) -> str:
        return self.provider_runtime

    def _repair_request_body(
        self,
        spec: ToolBuildSpec,
        previous: ToolCandidateResult,
        feedback: CandidateRepairFeedback,
        *,
        thinking_enabled: bool,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        static_patch = feedback.repair_kind is RepairKind.STATIC_POLICY
        response_model = CandidatePatchDraftPayload if static_patch else ToolCandidatePayload
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        specification = spec.model_dump_json(exclude_none=True)
        candidate = previous.payload.model_dump_json(exclude_none=True)
        diagnostics = feedback.model_dump_json(exclude_none=True)
        body = {
            "model": spec.model.value,
            "messages": [
                {
                    "role": "system",
                    "content": f"{SYSTEM_PROMPT}\nRequired JSON schema:\n{schema}",
                },
                {
                    "role": "user",
                    "content": (
                        (STATIC_REPAIR_INSTRUCTION if static_patch else REPAIR_INSTRUCTION)
                        + f"Specification JSON:\n{specification}\n"
                        f"Previous candidate JSON:\n{candidate}\n"
                        f"Bounded failure diagnostics JSON:\n{diagnostics}"
                    ),
                },
            ],
            "thinking": {"type": "enabled" if thinking_enabled else "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        if thinking_enabled:
            body["reasoning_effort"] = "high"
        return body

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

    @staticmethod
    def _parse_patch(
        data: dict[str, Any],
        base_candidate_sha256: str,
    ) -> CandidatePatchPayload:
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("candidate patch JSON was truncated")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("candidate patch content is empty")
        raw = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError("candidate patch must be a JSON object")
        edits = raw.get("edits")
        if isinstance(edits, list):
            raw["edits"] = [
                edit
                for edit in edits
                if not (
                    isinstance(edit, dict)
                    and isinstance(edit.get("old_text"), str)
                    and edit.get("old_text") == edit.get("new_text")
                )
            ]
        raw.pop("base_candidate_sha256", None)
        draft = CandidatePatchDraftPayload.model_validate(raw)
        return CandidatePatchPayload(
            base_candidate_sha256=base_candidate_sha256,
            summary=draft.summary,
            edits=draft.edits,
        )

    @staticmethod
    def _structured_failure_status(error: Exception, static_patch: bool) -> str:
        if not static_patch:
            return "invalid_response"
        if isinstance(error, CandidatePatchError):
            return "invalid_patch_application"
        if isinstance(error, ValidationError):
            return "invalid_patch_schema"
        if isinstance(error, json.JSONDecodeError):
            return "invalid_patch_json"
        return "invalid_patch_response"

    @staticmethod
    def _structured_failure_detail(error: Exception, static_patch: bool) -> str:
        """Return field/type-only diagnostics without model content or rejected values."""

        if not static_patch or not isinstance(error, ValidationError):
            return ""
        hints: list[str] = []
        for item in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:5]:
            location = ".".join(str(part) for part in item.get("loc", ())) or "root"
            error_type = str(item.get("type", "validation_error"))
            hints.append(f"{location}:{error_type}")
        return f"; schema_hints={','.join(hints)}" if hints else ""

    @staticmethod
    def _response_chars(data: dict[str, Any]) -> int:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return 0
        return len(content) if isinstance(content, str) else 0

    def _record_failure(
        self,
        spec: ToolBuildSpec,
        client_name: str,
        started: float,
        priced_at: datetime,
        status: Literal["http_error", "transport_error", "invalid_response"],
        task_kind: str,
        retries: int,
        thinking_enabled: bool,
        budget_session_id: str | None,
        lifecycle_id: str | None,
        request_chars: int,
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
            thinking_enabled=thinking_enabled,
            budget_session_id=budget_session_id,
            lifecycle_id=lifecycle_id,
            request_chars=request_chars,
            response_chars=0,
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
        thinking_enabled: bool = True,
        budget_session_id: str | None = None,
        lifecycle_id: str | None = None,
        request_chars: int = 0,
        response_chars: int = 0,
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
                provider=self.provider,
                provider_model_id=spec.model.value,
                provider_runtime=self.provider_runtime,
                thinking_enabled=thinking_enabled,
                reasoning_effort="high" if thinking_enabled else None,
                max_output_tokens=(
                    STATIC_REPAIR_MAX_OUTPUT_TOKENS
                    if task_kind == "tool_repair" and not thinking_enabled
                    else SEMANTIC_REPAIR_MAX_OUTPUT_TOKENS
                    if task_kind == "tool_repair"
                    else INITIAL_MAX_OUTPUT_TOKENS
                ),
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
                budget_session_id=budget_session_id,
                lifecycle_id=lifecycle_id,
                request_chars=request_chars,
                response_chars=response_chars,
            )
        )
