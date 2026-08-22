"""DeepSeek API-key storage backed exclusively by Windows Credential Manager."""

from __future__ import annotations

import platform
import re
from typing import Protocol

import keyring
from keyring.errors import KeyringError

SERVICE_NAME = "Ask AI MCP"
ACCOUNT_NAME = "deepseek-api-key"
OPENCODE_ACCOUNT_PREFIX = "opencode-go"
OPENCODE_PROFILE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


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
    """Store one explicitly selected OpenCode Go account credential."""

    def __init__(self, profile: str = "primary", backend: CredentialBackend | None = None) -> None:
        normalized = profile.strip().casefold()
        if OPENCODE_PROFILE_PATTERN.fullmatch(normalized) is None:
            raise CredentialError(
                "OpenCode Go account alias must start with a letter and contain only "
                "lowercase letters, digits, underscores, or hyphens"
            )
        self.profile = normalized
        self.account_name = f"{OPENCODE_ACCOUNT_PREFIX}-{normalized}-api-key"
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
                f"OpenCode Go {self.profile} API key is not configured; "
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
