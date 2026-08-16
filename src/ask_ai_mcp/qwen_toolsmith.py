"""Local Qwen implementation of the bounded toolsmith candidate protocol."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from ask_ai_mcp.deepseek import (
    BUILD_INSTRUCTION,
    REGENERATION_INSTRUCTION,
    REPAIR_INSTRUCTION,
    STATIC_REPAIR_INSTRUCTION,
    SYSTEM_PROMPT,
    DeepSeekClient,
    ModelEscalationRequired,
    prompt_template_sha256,
)
from ask_ai_mcp.hashing import sha256_text
from ask_ai_mcp.models import (
    CandidatePatchDraftPayload,
    CandidateRepairFeedback,
    DeepSeekModel,
    ModelProvider,
    RepairKind,
    ToolBuildSpec,
    ToolCandidatePayload,
    ToolCandidateResult,
    UsageEvent,
)
from ask_ai_mcp.policy import require_tool_spec_allowed
from ask_ai_mcp.qwen import QwenOpenAIClient
from ask_ai_mcp.usage import UsageStore

QWEN_TOOLSMITH_PROMPT_VERSION = "local-qwen-toolsmith-json-v3"
QWEN_TOOLSMITH_MAX_OUTPUT_TOKENS = 32_768


class LocalQwenToolsmithClient(DeepSeekClient):
    """Use local Qwen behind the same mechanically validated lifecycle."""

    provider = ModelProvider.LOCAL_QWEN
    provider_runtime = "openai_chat_completions_local"
    api_label = "local Qwen API"
    response_label = "local Qwen"

    def __init__(
        self,
        *,
        qwen_client: QwenOpenAIClient | None = None,
        usage_store: UsageStore | None = None,
    ) -> None:
        self.qwen = qwen_client or QwenOpenAIClient()
        super().__init__(
            api_key_provider=lambda: "local",
            usage_store=usage_store,
            timeout_seconds=self.qwen.config.text_timeout_seconds,
        )

    @staticmethod
    def replay_prompt_metadata() -> tuple[str, str]:
        settings = json.dumps(
            {
                "provider": "local_qwen",
                "runtime": "openai_chat_completions_local",
                "temperature": 0.1,
                "reasoning_effort": "none",
                "max_output_tokens": QWEN_TOOLSMITH_MAX_OUTPUT_TOKENS,
                "response_format": "prompt_constrained_json",
                "controller_prompt_sha256": prompt_template_sha256(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return QWEN_TOOLSMITH_PROMPT_VERSION, sha256_text(settings)

    def _provider_model_id(
        self, spec: ToolBuildSpec, request_body: dict[str, Any] | None = None
    ) -> str:
        return self.qwen.config.model_id

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
        body = self._request_body(
            spec,
            thinking_enabled=False,
            max_output_tokens=QWEN_TOOLSMITH_MAX_OUTPUT_TOKENS,
        )
        return self._complete_candidate(
            spec=spec,
            client_name=client_name,
            request_body=body,
            task_kind="tool_build",
            retries=0,
            thinking_enabled=False,
            budget_session_id=budget_session_id,
            lifecycle_id=lifecycle_id,
        )

    def _request_body(
        self,
        spec: ToolBuildSpec,
        *,
        thinking_enabled: bool,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        return self._local_body(
            user_content=BUILD_INSTRUCTION + spec.model_dump_json(exclude_none=True),
            thinking_enabled=thinking_enabled,
            max_output_tokens=max_output_tokens,
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
        require_tool_spec_allowed(spec)
        if spec.model is DeepSeekModel.PRO and not allow_pro:
            raise ModelEscalationRequired("V4 Pro requires explicit Opus/Sol escalation")
        body = self._local_body(
            user_content=(REGENERATION_INSTRUCTION + spec.model_dump_json(exclude_none=True)),
            thinking_enabled=False,
            max_output_tokens=QWEN_TOOLSMITH_MAX_OUTPUT_TOKENS,
        )
        return self._complete_candidate(
            spec=spec,
            client_name=client_name,
            request_body=body,
            task_kind="tool_regeneration",
            retries=1,
            thinking_enabled=False,
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
        max_output_tokens: int = QWEN_TOOLSMITH_MAX_OUTPUT_TOKENS,
        budget_session_id: str | None = None,
        lifecycle_id: str | None = None,
    ) -> ToolCandidateResult:
        return super().repair_candidate(
            spec,
            previous,
            feedback,
            client_name=client_name,
            allow_pro=allow_pro,
            thinking_enabled=False,
            max_output_tokens=min(max_output_tokens, QWEN_TOOLSMITH_MAX_OUTPUT_TOKENS),
            budget_session_id=budget_session_id,
            lifecycle_id=lifecycle_id,
        )

    def _repair_request_body(
        self,
        spec: ToolBuildSpec,
        previous: ToolCandidateResult,
        feedback: CandidateRepairFeedback,
        *,
        thinking_enabled: bool,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        specification = spec.model_dump_json(exclude_none=True)
        candidate = previous.payload.model_dump_json(exclude_none=True)
        diagnostics = feedback.model_dump_json(exclude_none=True)
        static_patch = feedback.repair_kind is RepairKind.STATIC_POLICY
        return self._local_body(
            user_content=(
                (STATIC_REPAIR_INSTRUCTION if static_patch else REPAIR_INSTRUCTION)
                + f"Specification JSON:\n{specification}\n"
                f"Previous candidate JSON:\n{candidate}\n"
                f"Bounded failure diagnostics JSON:\n{diagnostics}"
            ),
            thinking_enabled=thinking_enabled,
            max_output_tokens=max_output_tokens,
            response_model=CandidatePatchDraftPayload if static_patch else ToolCandidatePayload,
        )

    def _local_body(
        self,
        *,
        user_content: str,
        thinking_enabled: bool,
        max_output_tokens: int,
        response_model: type[CandidatePatchDraftPayload | ToolCandidatePayload] = (
            ToolCandidatePayload
        ),
    ) -> dict[str, Any]:
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        return {
            "model": self.qwen.config.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": f"{SYSTEM_PROMPT}\nRequired JSON schema:\n{schema}",
                },
                {"role": "user", "content": user_content},
            ],
            "reasoning_effort": "high" if thinking_enabled else "none",
            "max_tokens": max_output_tokens,
            "temperature": 0.1,
            "stream": False,
        }

    def _request(self, request_body: dict[str, Any]) -> dict[str, Any]:
        return self.qwen.chat(request_body, vision=False)

    def release_resources(self) -> str:
        return self.qwen.release_after_task()

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
        self.usage_store.record(
            UsageEvent(
                client_name=client_name,
                task_kind=task_kind,
                model=spec.model,
                provider=self.provider,
                provider_model_id=self.qwen.config.model_id,
                provider_runtime=self.provider_runtime,
                thinking_enabled=thinking_enabled,
                priced_at=priced_at.astimezone(UTC),
                pricing_schedule_version="local_qwen_zero_cost_v1",
                prompt_cache_hit_tokens=cache_hit,
                prompt_cache_miss_tokens=cache_miss,
                completion_tokens=completion,
                reasoning_tokens=reasoning,
                estimated_cost_cny=0,
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
