"""No-retry OpenAI-compatible client for the loopback Qwen runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

QWEN_BASE_URL_ENV = "ASK_AI_MCP_QWEN_BASE_URL"
QWEN_MODEL_ID_ENV = "ASK_AI_MCP_QWEN_MODEL_ID"
QWEN_CONNECT_TIMEOUT_ENV = "ASK_AI_MCP_QWEN_CONNECT_TIMEOUT_SECONDS"
QWEN_TEXT_TIMEOUT_ENV = "ASK_AI_MCP_QWEN_TEXT_TIMEOUT_SECONDS"
QWEN_VISION_TIMEOUT_ENV = "ASK_AI_MCP_QWEN_VISION_TIMEOUT_SECONDS"
QWEN_UNLOAD_AFTER_TASK_ENV = "ASK_AI_MCP_QWEN_UNLOAD_AFTER_TASK"

DEFAULT_QWEN_BASE_URL = "http://127.0.0.1:38827/v1"
DEFAULT_QWEN_MODEL_ID = "qwen3.8-27b-local"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 30.0
DEFAULT_TEXT_TIMEOUT_SECONDS = 600.0
DEFAULT_VISION_TIMEOUT_SECONDS = 5_400.0


class QwenClientError(RuntimeError):
    """Safe local-runtime failure without response bodies or source content."""


class QwenRequestStillProcessing(QwenClientError):
    """A timed-out request remains active and must not be submitted again."""


@dataclass(frozen=True, slots=True)
class QwenRuntimeConfig:
    base_url: str = DEFAULT_QWEN_BASE_URL
    model_id: str = DEFAULT_QWEN_MODEL_ID
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    text_timeout_seconds: float = DEFAULT_TEXT_TIMEOUT_SECONDS
    vision_timeout_seconds: float = DEFAULT_VISION_TIMEOUT_SECONDS
    unload_after_task: bool = False

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise QwenClientError("local Qwen base URL must use loopback HTTP")
        if parsed.path.rstrip("/") != "/v1" or parsed.params or parsed.query or parsed.fragment:
            raise QwenClientError("local Qwen base URL must end with /v1")
        if not self.model_id.strip() or len(self.model_id) > 255:
            raise QwenClientError("local Qwen model ID is invalid")
        if not 10 <= self.connect_timeout_seconds <= 30:
            raise QwenClientError("local Qwen connect timeout must be 10-30 seconds")
        if self.text_timeout_seconds < 600:
            raise QwenClientError("local Qwen text timeout must be at least 600 seconds")
        if self.vision_timeout_seconds < 3_600:
            raise QwenClientError("local Qwen vision timeout must be at least 3600 seconds")

    @property
    def origin(self) -> str:
        parsed = urlparse(self.base_url)
        host = f"[{parsed.hostname}]" if ":" in (parsed.hostname or "") else parsed.hostname
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"

    @classmethod
    def from_environment(cls) -> QwenRuntimeConfig:
        return cls(
            base_url=os.environ.get(QWEN_BASE_URL_ENV, DEFAULT_QWEN_BASE_URL).strip(),
            model_id=os.environ.get(QWEN_MODEL_ID_ENV, DEFAULT_QWEN_MODEL_ID).strip(),
            connect_timeout_seconds=_float_env(
                QWEN_CONNECT_TIMEOUT_ENV,
                DEFAULT_CONNECT_TIMEOUT_SECONDS,
            ),
            text_timeout_seconds=_float_env(
                QWEN_TEXT_TIMEOUT_ENV,
                DEFAULT_TEXT_TIMEOUT_SECONDS,
            ),
            vision_timeout_seconds=_float_env(
                QWEN_VISION_TIMEOUT_ENV,
                DEFAULT_VISION_TIMEOUT_SECONDS,
            ),
            unload_after_task=_bool_env(QWEN_UNLOAD_AFTER_TASK_ENV, False),
        )


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise QwenClientError(f"{name} must be numeric") from error


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().casefold()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise QwenClientError(f"{name} must be a boolean")


def _slot_is_processing(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("is_processing") is True:
            return True
        return any(_slot_is_processing(item) for item in value.values())
    if isinstance(value, list):
        return any(_slot_is_processing(item) for item in value)
    return False


class QwenOpenAIClient:
    """Call one fixed loopback model and never automatically retry POST requests."""

    def __init__(
        self,
        config: QwenRuntimeConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or QwenRuntimeConfig.from_environment()
        self.transport = transport

    def health(self) -> dict[str, Any]:
        data = self._get_json("/health")
        if not isinstance(data, dict):
            raise QwenClientError("local Qwen health endpoint returned a non-object response")
        return data

    def slots(self) -> object:
        return self._get_json("/slots", params={"model": self.config.model_id})

    def models(self) -> dict[str, Any]:
        data = self._get_json("/v1/models")
        if not isinstance(data, dict):
            raise QwenClientError("local Qwen models endpoint returned a non-object response")
        return data

    def model_is_available(self, models: dict[str, Any] | None = None) -> bool:
        payload = models if models is not None else self.models()
        items = payload.get("data")
        return isinstance(items, list) and any(
            isinstance(item, dict) and item.get("id") == self.config.model_id for item in items
        )

    def model_status(self, models: dict[str, Any] | None = None) -> str | None:
        """Return router state without triggering model autoload."""

        payload = models if models is not None else self.models()
        items = payload.get("data")
        if not isinstance(items, list):
            return None
        for item in items:
            if not isinstance(item, dict) or item.get("id") != self.config.model_id:
                continue
            status = item.get("status")
            if isinstance(status, dict) and isinstance(status.get("value"), str):
                return status["value"]
        return None

    def chat(self, body: dict[str, Any], *, vision: bool) -> dict[str, Any]:
        timeout_seconds = (
            self.config.vision_timeout_seconds if vision else self.config.text_timeout_seconds
        )
        timeout = httpx.Timeout(
            connect=self.config.connect_timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=self.config.connect_timeout_seconds,
        )
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": "Bearer local",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            processing = self._processing_after_timeout()
            if processing:
                raise QwenRequestStillProcessing(
                    "local Qwen request timed out but a slot is still processing; do not resubmit"
                ) from None
            raise QwenClientError(
                "local Qwen request timed out; no slot reports processing"
            ) from None
        except httpx.HTTPStatusError as error:
            raise QwenClientError(
                f"local Qwen API returned HTTP {error.response.status_code}"
            ) from None
        except (httpx.HTTPError, TypeError, ValueError):
            raise QwenClientError("local Qwen API request failed") from None
        if not isinstance(data, dict):
            raise QwenClientError("local Qwen API returned a non-object response")
        return data

    def release_after_task(self) -> str:
        """Unload an idle router-managed model while leaving the HTTP router online."""

        if not self.config.unload_after_task:
            return "disabled"
        if self.model_status() == "unloaded":
            return "unloaded"
        if _slot_is_processing(self.slots()):
            return "busy"
        timeout = httpx.Timeout(
            connect=self.config.connect_timeout_seconds,
            read=120,
            write=120,
            pool=self.config.connect_timeout_seconds,
        )
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(
                    f"{self.config.origin}/models/unload",
                    headers={
                        "Authorization": "Bearer local",
                        "Content-Type": "application/json",
                    },
                    json={"model": self.config.model_id},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as error:
            raise QwenClientError(
                f"local Qwen unload endpoint returned HTTP {error.response.status_code}"
            ) from None
        except (httpx.HTTPError, TypeError, ValueError):
            raise QwenClientError("local Qwen model unload failed") from None
        if not isinstance(data, dict) or data.get("success") is not True:
            raise QwenClientError("local Qwen model unload was not acknowledged")
        return "unloaded"

    def _processing_after_timeout(self) -> bool:
        try:
            return _slot_is_processing(self.slots())
        except QwenClientError:
            return False

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> object:
        timeout = httpx.Timeout(
            connect=self.config.connect_timeout_seconds,
            read=self.config.connect_timeout_seconds,
            write=self.config.connect_timeout_seconds,
            pool=self.config.connect_timeout_seconds,
        )
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.get(f"{self.config.origin}{path}", params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as error:
            raise QwenClientError(
                f"local Qwen health endpoint returned HTTP {error.response.status_code}"
            ) from None
        except (httpx.HTTPError, TypeError, ValueError):
            raise QwenClientError("local Qwen health endpoint is unavailable") from None
        return data
