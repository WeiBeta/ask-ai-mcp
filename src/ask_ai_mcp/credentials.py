"""DeepSeek API-key storage backed exclusively by Windows Credential Manager."""

from __future__ import annotations

import platform
from typing import Protocol

import keyring
from keyring.errors import KeyringError

SERVICE_NAME = "Ask AI MCP"
ACCOUNT_NAME = "deepseek-api-key"
OPENCODE_ACCOUNT_PREFIX = "opencode-go"


class CredentialBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class CredentialError(RuntimeError):
    """Base error for secure credential operations."""


class CredentialNotFoundError(CredentialError):
    """Raised when the DeepSeek API key has not been configured."""


def validate_api_key(api_key: str) -> str:
    """Validate shape without logging or otherwise exposing the secret."""
    normalized = api_key.strip()
    if not normalized.startswith("sk-") or not 20 <= len(normalized) <= 256:
        raise CredentialError("DeepSeek API key has an unexpected format")
    if any(character.isspace() for character in normalized):
        raise CredentialError("DeepSeek API key must not contain whitespace")
    return normalized


def validate_opencode_api_key(api_key: str) -> str:
    """Validate an opaque OpenCode key without assuming a vendor prefix."""

    normalized = api_key.strip()
    if not 20 <= len(normalized) <= 512:
        raise CredentialError("OpenCode Go API key has an unexpected format")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise CredentialError("OpenCode Go API key must not contain whitespace or controls")
    return normalized


def _load_secure_windows_backend() -> CredentialBackend:
    if platform.system() != "Windows":
        raise CredentialError("Phase 1 credential storage requires Windows")
    backend = keyring.get_keyring()
    backend_module = type(backend).__module__.casefold()
    if not backend_module.startswith("keyring.backends.windows"):
        raise CredentialError("A Windows Credential Manager keyring backend is required")
    return backend


class CredentialStore:
    """Small injectable wrapper around the OS credential backend."""

    def __init__(self, backend: CredentialBackend | None = None) -> None:
        self._backend = backend

    @property
    def backend(self) -> CredentialBackend:
        if self._backend is None:
            self._backend = _load_secure_windows_backend()
        return self._backend

    def is_configured(self) -> bool:
        try:
            return self.backend.get_password(SERVICE_NAME, ACCOUNT_NAME) is not None
        except KeyringError:
            raise CredentialError("Windows Credential Manager read failed") from None

    def get_api_key(self) -> str:
        try:
            api_key = self.backend.get_password(SERVICE_NAME, ACCOUNT_NAME)
        except KeyringError:
            raise CredentialError("Windows Credential Manager read failed") from None
        if api_key is None:
            raise CredentialNotFoundError(
                "DeepSeek API key is not configured; run ask-ai-mcp-credentials set"
            )
        return validate_api_key(api_key)

    def set_api_key(self, api_key: str) -> None:
        try:
            self.backend.set_password(SERVICE_NAME, ACCOUNT_NAME, validate_api_key(api_key))
        except KeyringError:
            raise CredentialError("Windows Credential Manager write failed") from None

    def delete_api_key(self) -> bool:
        if not self.is_configured():
            return False
        try:
            self.backend.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        except KeyringError:
            raise CredentialError("Windows Credential Manager delete failed") from None
        return True


class OpenCodeCredentialStore:
    """Store exactly one credential for one non-secret OpenCode account UID."""

    def __init__(self, account_uid: str, backend: CredentialBackend | None = None) -> None:
        from ask_ai_mcp.opencode_account import validate_account_uid

        try:
            self.account_uid = validate_account_uid(account_uid)
        except ValueError as error:
            raise CredentialError(str(error)) from error
        self.profile = self.account_uid  # bounded compatibility alias
        self.account_name = f"{OPENCODE_ACCOUNT_PREFIX}-{self.account_uid}-api-key"
        self._backend = backend

    @property
    def backend(self) -> CredentialBackend:
        if self._backend is None:
            self._backend = _load_secure_windows_backend()
        return self._backend

    def is_configured(self) -> bool:
        try:
            return self.backend.get_password(SERVICE_NAME, self.account_name) is not None
        except KeyringError:
            raise CredentialError("Windows Credential Manager read failed") from None

    def get_api_key(self) -> str:
        try:
            api_key = self.backend.get_password(SERVICE_NAME, self.account_name)
        except KeyringError:
            raise CredentialError("Windows Credential Manager read failed") from None
        if api_key is None:
            raise CredentialNotFoundError(
                f"OpenCode Go account {self.account_uid} API key is not configured; "
                "run ask-ai-mcp-credentials set --provider opencode-go"
            )
        return validate_opencode_api_key(api_key)

    def set_api_key(self, api_key: str) -> None:
        try:
            self.backend.set_password(
                SERVICE_NAME, self.account_name, validate_opencode_api_key(api_key)
            )
        except KeyringError:
            raise CredentialError("Windows Credential Manager write failed") from None

    def delete_api_key(self) -> bool:
        if not self.is_configured():
            return False
        try:
            self.backend.delete_password(SERVICE_NAME, self.account_name)
        except KeyringError:
            raise CredentialError("Windows Credential Manager delete failed") from None
        return True
