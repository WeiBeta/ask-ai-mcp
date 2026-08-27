"""OpenCode Go Qwen3.8 Max backend for bounded multimodal source extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from ask_ai_mcp import __version__
from ask_ai_mcp.credentials import OpenCodeCredentialStore
from ask_ai_mcp.models import (
    ModelProvider,
    SourceBackendStatus,
    SourceExtractionProfile,
    UsageEvent,
)
from ask_ai_mcp.opencode_account import OpenCodeAccount, load_opencode_account
from ask_ai_mcp.opencode_pricing import (
    OPENCODE_GO_PRICING_VERSION,
    OpenCodeGoModel,
    OpenCodeGoProtocol,
    calculate_opencode_go_cost,
)
from ask_ai_mcp.qwen import QwenClientError
from ask_ai_mcp.qwen_source import LocalQwenSourceBackend
from ask_ai_mcp.usage import UsageStore

OPENCODE_GO_MESSAGES_URL = "https://opencode.ai/zen/go/v1/messages"
OPENCODE_GO_MODELS_URL = "https://opencode.ai/zen/go/v1/models"


@dataclass(frozen=True, slots=True)
class _RemoteQwenConfig:
    model_id: str = OpenCodeGoModel.QWEN_3_8_MAX.value


class OpenCodeQwenMessagesClient:
    """Translate the existing bounded source request to Anthropic Messages."""

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
        credentials = OpenCodeCredentialStore(selected.uid)
        self.account = selected.uid
        self.account_alias = selected.alias
        self.subscription_id = selected.subscription_id
        self.api_key_provider = api_key_provider or credentials.get_api_key
        self.usage_store = usage_store or UsageStore()
        self.transport = transport
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self.config = _RemoteQwenConfig()

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key_provider(),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "User-Agent": f"ask-ai-mcp/{__version__}",
        }

    def health(self) -> None:
        self.models()

    def models(self) -> dict[str, Any]:
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.get(OPENCODE_GO_MODELS_URL, headers=self._headers())
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, TypeError, ValueError) as error:
            raise QwenClientError("OpenCode Go models endpoint failed") from error
        if not isinstance(data, dict):
            raise QwenClientError("OpenCode Go models endpoint returned invalid JSON")
        return data

    def model_is_available(self, response: dict[str, Any]) -> bool:
        entries = response.get("data", [])
        return any(
            isinstance(item, dict) and item.get("id") == self.config.model_id for item in entries
        )

    def chat(self, body: dict[str, Any], *, vision: bool) -> dict[str, Any]:
        del vision
        reason = self.usage_store.opencode_limit_reason(
            self.account, self.config.model_id, self.subscription_id
        )
        if reason:
            raise QwenClientError(reason)
        request = self._messages_body(body)
        started = perf_counter()
        status = "success"
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(
                    OPENCODE_GO_MESSAGES_URL,
                    headers=self._headers(),
                    json=request,
                )
                response.raise_for_status()
                data = response.json()
            if not isinstance(data, dict):
                raise ValueError("non-object response")
            normalized = self._normalize(data)
        except httpx.HTTPStatusError as error:
            status = "http_error"
            self._record({}, started, status)
            raise QwenClientError(
                f"OpenCode Go returned HTTP {error.response.status_code}"
            ) from None
        except (httpx.HTTPError, TypeError, ValueError):
            status = "invalid_response"
            self._record({}, started, status)
            raise QwenClientError("OpenCode Go Qwen request failed") from None
        self._record(data, started, status)
        return normalized

    @staticmethod
    def _messages_body(body: dict[str, Any]) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        system_parts: list[str] = []
        for message in body.get("messages", []):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if role == "system" and isinstance(content, str):
                system_parts.append(content)
                continue
            converted: list[dict[str, Any]] = []
            parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if (
                        not isinstance(url, str)
                        or not url.startswith("data:")
                        or ";base64," not in url
                    ):
                        raise ValueError("only base64 data images are allowed")
                    header, encoded = url.split(",", 1)
                    converted.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": header[5:].split(";", 1)[0],
                                "data": encoded,
                            },
                        }
                    )
                elif part.get("type") == "text" and isinstance(part.get("text"), str):
                    converted.append({"type": "text", "text": part["text"]})
            messages.append({"role": role, "content": converted})
        request = {
            "model": body["model"],
            "messages": messages,
            "max_tokens": body["max_tokens"],
            "temperature": body.get("temperature", 0.1),
        }
        if system_parts:
            request["system"] = "\n".join(system_parts)
        return request

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        text = "".join(
            str(item.get("text", ""))
            for item in data.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        )
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        return {
            "choices": [
                {
                    "finish_reason": (
                        "length" if data.get("stop_reason") == "max_tokens" else "stop"
                    ),
                    "message": {"content": text},
                }
            ],
            "usage": {
                "prompt_cache_hit_tokens": cache_read,
                "prompt_cache_miss_tokens": input_tokens,
                "completion_tokens": int(usage.get("output_tokens", 0) or 0),
            },
        }

    def _record(self, data: dict[str, Any], started: float, status: str) -> None:
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
        priced_at = datetime.now(UTC)
        cost = calculate_opencode_go_cost(
            OpenCodeGoModel.QWEN_3_8_MAX,
            input_tokens=input_tokens + cache_read + cache_write,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            priced_at=priced_at,
        )
        self.usage_store.record(
            UsageEvent(
                client_name="source_backend",
                task_kind="source_extract",
                model=self.config.model_id,
                provider=ModelProvider.OPENCODE,
                provider_model_id=self.config.model_id,
                provider_runtime=OpenCodeGoProtocol.ANTHROPIC_MESSAGES.value,
                provider_account=self.account,
                provider_subscription_id=self.subscription_id,
                thinking_enabled=False,
                priced_at=priced_at,
                pricing_schedule_version=OPENCODE_GO_PRICING_VERSION,
                prompt_cache_hit_tokens=cache_read,
                prompt_cache_miss_tokens=input_tokens,
                completion_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                estimated_cost_usd=cost,
                latency_ms=max(0, round((perf_counter() - started) * 1_000)),
                status=status,
            )
        )

    def release_after_task(self) -> str:
        return "online"


class OpenCodeQwenSourceBackend(LocalQwenSourceBackend):
    """Reuse staging/provenance logic while replacing only the model transport."""

    def __init__(
        self,
        client: OpenCodeQwenMessagesClient | None = None,
        *,
        preprocessor=None,
    ) -> None:
        super().__init__(client=client or OpenCodeQwenMessagesClient(), preprocessor=preprocessor)

    def status(self) -> SourceBackendStatus:
        try:
            models = self.client.models()
            ready = self.client.model_is_available(models)
        except QwenClientError:
            ready = False
        return SourceBackendStatus(
            provider=ModelProvider.OPENCODE,
            configured=True,
            ready=ready,
            model_id=self.client.config.model_id,
            runtime=OpenCodeGoProtocol.ANTHROPIC_MESSAGES.value,
            supported_profiles=[
                SourceExtractionProfile.DOCUMENT_EVIDENCE,
                SourceExtractionProfile.VISUAL_STRUCTURE,
            ],
            detail=(
                "OpenCode Go Qwen3.8 Max is ready for bounded multimodal extraction."
                if ready
                else "OpenCode Go is configured but Qwen3.8 Max is unavailable."
            ),
        )
