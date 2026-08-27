"""Bounded OpenCode Go adapter for the existing toolsmith lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from ask_ai_mcp import __version__
from ask_ai_mcp.credentials import OpenCodeCredentialStore
from ask_ai_mcp.deepseek import DeepSeekClient
from ask_ai_mcp.models import DeepSeekModel, ModelProvider, ToolBuildSpec, UsageEvent
from ask_ai_mcp.opencode_account import OpenCodeAccount, load_opencode_account
from ask_ai_mcp.opencode_pricing import (
    OPENCODE_GO_PRICING_VERSION,
    OpenCodeGoModel,
    OpenCodeGoProtocol,
    OpenCodeGoRateBand,
    calculate_opencode_go_cost_breakdown,
)
from ask_ai_mcp.usage import UsageStore

OPENCODE_GO_CHAT_URL = "https://opencode.ai/zen/go/v1/chat/completions"
OPENCODE_GO_RESPONSES_URL = "https://opencode.ai/zen/go/v1/responses"


class OpenCodeGoClient(DeepSeekClient):
    """Route only approved toolsmith stages through fixed OpenCode Go endpoints."""

    provider = ModelProvider.OPENCODE
    provider_runtime = OpenCodeGoProtocol.CHAT_COMPLETIONS.value
    api_label = "OpenCode Go API"
    response_label = "OpenCode Go"

    def __init__(
        self,
        *,
        account: OpenCodeAccount | None = None,
        api_key_provider=None,
        usage_store: UsageStore | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 600.0,
    ) -> None:
        selected = account or (
            OpenCodeAccount(uid="injected-test", alias="injected-test")
            if api_key_provider is not None
            else load_opencode_account(required=True)
        )
        credential_store = OpenCodeCredentialStore(selected.uid)
        self.account = selected.uid
        self.account_alias = selected.alias
        self.subscription_id = selected.subscription_id
        super().__init__(
            api_key_provider=api_key_provider or credential_store.get_api_key,
            usage_store=usage_store,
            transport=transport,
            timeout_seconds=timeout_seconds,
        )

    def _request_body(
        self, spec: ToolBuildSpec, *, thinking_enabled: bool, max_output_tokens: int
    ) -> dict[str, Any]:
        body = super()._request_body(
            spec, thinking_enabled=thinking_enabled, max_output_tokens=max_output_tokens
        )
        body["model"] = self._initial_model(spec).value
        body["_json_schema"] = self._strict_schema(self._candidate_schema())
        return body

    def _repair_request_body(self, spec, previous, feedback, **kwargs) -> dict[str, Any]:
        body = super()._repair_request_body(spec, previous, feedback, **kwargs)
        model = (
            self._initial_model(spec)
            if not kwargs.get("thinking_enabled", True)
            else OpenCodeGoModel.GPT_5_6_LUNA
        )
        body["model"] = model.value
        schema_text = body["messages"][0]["content"].split("Required JSON schema:\n", 1)[-1]
        body["_json_schema"] = self._strict_schema(json.loads(schema_text))
        return body

    @staticmethod
    def _candidate_schema() -> dict[str, Any]:
        from ask_ai_mcp.models import ToolCandidatePayload

        return ToolCandidatePayload.model_json_schema()

    @classmethod
    def _strict_schema(cls, value: object) -> object:
        """Normalize Pydantic JSON Schema to the Responses strict subset."""

        if isinstance(value, list):
            return [cls._strict_schema(item) for item in value]
        if not isinstance(value, dict):
            return value
        normalized = {
            key: cls._strict_schema(item) for key, item in value.items() if key != "default"
        }
        properties = normalized.get("properties")
        if normalized.get("type") == "object" and isinstance(properties, dict):
            normalized["additionalProperties"] = False
            normalized["required"] = list(properties)
        return normalized

    @staticmethod
    def _initial_model(spec: ToolBuildSpec) -> OpenCodeGoModel:
        return (
            OpenCodeGoModel.DSV4_PRO
            if spec.model is DeepSeekModel.PRO
            else OpenCodeGoModel.DSV4_FLASH
        )

    def _provider_model_id(
        self, spec: ToolBuildSpec, request_body: dict[str, Any] | None = None
    ) -> str:
        if request_body and isinstance(request_body.get("model"), str):
            return str(request_body["model"])
        return self._initial_model(spec).value

    def _provider_runtime(self, request_body: dict[str, Any] | None = None) -> str:
        if request_body and request_body.get("model") == OpenCodeGoModel.GPT_5_6_LUNA.value:
            return OpenCodeGoProtocol.RESPONSES.value
        return OpenCodeGoProtocol.CHAT_COMPLETIONS.value

    def _request(self, request_body: dict[str, Any]) -> dict[str, Any]:
        body = dict(request_body)
        schema = body.pop("_json_schema", None)
        model_id = str(body["model"])
        reason = self.usage_store.opencode_limit_reason(
            self.account, model_id, self.subscription_id
        )
        if reason:
            raise ValueError(reason)
        if model_id == OpenCodeGoModel.GPT_5_6_LUNA.value:
            endpoint = OPENCODE_GO_RESPONSES_URL
            body = self._responses_body(body, schema)
        else:
            endpoint = OPENCODE_GO_CHAT_URL
        api_key = self.api_key_provider()
        with httpx.Client(
            transport=self.transport,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": f"ask-ai-mcp/{__version__}",
                },
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError("OpenCode Go returned a non-object response")
        return (
            self._normalize_responses(data)
            if endpoint == OPENCODE_GO_RESPONSES_URL
            else self._normalize_chat_usage(data)
        )

    @staticmethod
    def _normalize_chat_usage(data: dict[str, Any]) -> dict[str, Any]:
        usage = data.get("usage")
        if not isinstance(usage, dict) or "prompt_cache_hit_tokens" in usage:
            return data
        details = usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
        try:
            cached_tokens = max(0, int(cached or 0))
            prompt_tokens = max(0, int(usage.get("prompt_tokens", 0) or 0))
        except (TypeError, ValueError):
            return data
        normalized = dict(data)
        normalized_usage = dict(usage)
        normalized_usage["prompt_cache_hit_tokens"] = cached_tokens
        normalized_usage["prompt_cache_miss_tokens"] = max(0, prompt_tokens - cached_tokens)
        normalized["usage"] = normalized_usage
        return normalized

    @staticmethod
    def _responses_body(body: dict[str, Any], schema: object) -> dict[str, Any]:
        if not isinstance(schema, dict):
            raise ValueError("Responses request is missing its bounded JSON schema")
        effort = "high" if body.get("reasoning_effort") == "high" else "none"
        return {
            "model": body["model"],
            "input": body["messages"],
            "reasoning": {"effort": effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "tool_candidate",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": body["max_tokens"],
            "store": False,
        }

    @staticmethod
    def _normalize_responses(data: dict[str, Any]) -> dict[str, Any]:
        text = data.get("output_text")
        if not isinstance(text, str):
            text = ""
            for item in data.get("output", []):
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        value = content.get("text")
                        if isinstance(value, str):
                            text += value
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        input_details = usage.get("input_tokens_details", {})
        output_details = usage.get("output_tokens_details", {})
        cached = input_details.get("cached_tokens", 0) if isinstance(input_details, dict) else 0
        input_tokens = usage.get("input_tokens", 0)
        normalized_usage = {
            "prompt_cache_hit_tokens": cached,
            "prompt_cache_miss_tokens": max(0, int(input_tokens or 0) - int(cached or 0)),
            "completion_tokens": usage.get("output_tokens", 0),
            "completion_tokens_details": {
                "reasoning_tokens": (
                    output_details.get("reasoning_tokens", 0)
                    if isinstance(output_details, dict)
                    else 0
                )
            },
        }
        truncated = data.get("status") not in (None, "completed")
        return {
            "choices": [
                {
                    "finish_reason": "length" if truncated else "stop",
                    "message": {"content": text},
                }
            ],
            "usage": normalized_usage,
        }

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
        model = self._usage_model(spec, task_kind, thinking_enabled)
        cost = calculate_opencode_go_cost_breakdown(
            model,
            input_tokens=cache_hit + cache_miss,
            output_tokens=completion,
            cache_read_tokens=cache_hit,
            priced_at=priced_at,
        )
        if cost.total_cost_usd is None:
            raise ValueError("OpenCode Go usage included an unsupported priced component")
        self.usage_store.record(
            UsageEvent(
                client_name=client_name,
                task_kind=task_kind,
                model=spec.model,
                provider=self.provider,
                provider_model_id=model.value,
                provider_runtime=(
                    OpenCodeGoProtocol.RESPONSES.value
                    if model is OpenCodeGoModel.GPT_5_6_LUNA
                    else OpenCodeGoProtocol.CHAT_COMPLETIONS.value
                ),
                provider_account=self.account,
                provider_subscription_id=self.subscription_id,
                thinking_enabled=thinking_enabled,
                priced_at=priced_at.astimezone(UTC),
                pricing_band=(
                    "peak"
                    if cost.band is OpenCodeGoRateBand.PEAK
                    else "off_peak"
                    if cost.band is OpenCodeGoRateBand.OFF_PEAK
                    else "standard"
                ),
                pricing_schedule_version=OPENCODE_GO_PRICING_VERSION,
                prompt_cache_hit_tokens=cache_hit,
                prompt_cache_miss_tokens=cache_miss,
                completion_tokens=completion,
                reasoning_tokens=reasoning,
                cache_read_tokens=cache_hit,
                estimated_cost_usd=cost.total_cost_usd,
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

    def _usage_model(
        self, spec: ToolBuildSpec, task_kind: str, thinking_enabled: bool
    ) -> OpenCodeGoModel:
        if task_kind == "tool_repair" and thinking_enabled:
            return OpenCodeGoModel.GPT_5_6_LUNA
        return self._initial_model(spec)
