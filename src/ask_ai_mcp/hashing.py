"""Stable hashes for specifications and untrusted candidate payloads."""

from __future__ import annotations

import hashlib

from ask_ai_mcp.models import ToolBuildSpec, ToolCandidatePayload


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tool_spec_sha256(spec: ToolBuildSpec) -> str:
    return sha256_text(spec.model_dump_json(exclude_none=True))


def candidate_payload_sha256(payload: ToolCandidatePayload) -> str:
    return sha256_text(payload.model_dump_json(exclude_none=True))
