"""Non-secret OpenCode Go account identity and one-key-per-account selection."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

ACCOUNT_UID_ENV = "ASK_AI_MCP_OPENCODE_ACCOUNT_UID"
ACCOUNT_ALIAS_ENV = "ASK_AI_MCP_OPENCODE_ACCOUNT_ALIAS"

_UID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,63}$")
_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,63}$")


@dataclass(frozen=True)
class OpenCodeAccount:
    """One paid Go account, one credential, and one isolated usage ledger."""

    uid: str
    alias: str

    @property
    def subscription_id(self) -> str:
        return f"go-account:{self.uid}"


def validate_account_uid(value: str) -> str:
    normalized = value.strip()
    if _UID_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "OpenCode Go account UID must be a stable non-secret identifier containing only "
            "letters, digits, dots, underscores, colons, or hyphens"
        )
    return normalized


def validate_account_alias(value: str) -> str:
    normalized = value.strip()
    if _ALIAS_PATTERN.fullmatch(normalized) is None:
        raise ValueError("OpenCode Go account alias contains unsupported characters")
    return normalized


def load_opencode_account(*, required: bool = True) -> OpenCodeAccount | None:
    """Load the explicitly selected account without inspecting any API key."""

    raw_uid = os.environ.get(ACCOUNT_UID_ENV, "").strip()
    if not raw_uid:
        if required:
            raise RuntimeError(f"{ACCOUNT_UID_ENV} must identify the selected paid Go account")
        return None
    uid = validate_account_uid(raw_uid)
    alias = validate_account_alias(os.environ.get(ACCOUNT_ALIAS_ENV, uid))
    return OpenCodeAccount(uid=uid, alias=alias)
