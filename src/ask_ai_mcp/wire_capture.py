"""Opt-in encrypted wire capture for bounded provider diagnostics."""

from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from secrets import token_bytes
from typing import BinaryIO

import keyring
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from keyring.errors import KeyringError

from ask_ai_mcp.credentials import SERVICE_NAME

WIRE_CAPTURE_ENABLED_ENV = "ASK_AI_MCP_WIRE_CAPTURE_ENABLED"
WIRE_CAPTURE_MAX_BYTES_ENV = "ASK_AI_MCP_WIRE_CAPTURE_MAX_BYTES"
WIRE_CAPTURE_KEY_ACCOUNT = "wire-capture-aes256-v1"
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
ABSOLUTE_MAX_BYTES = 100 * 1024 * 1024
_MAGIC = b"ASKAIWIRE1\n"
_FRAME_HEADER = struct.Struct(">I12s")


class WireCaptureError(RuntimeError):
    """Raised before a paid call when encrypted capture cannot be made safe."""


def wire_capture_enabled() -> bool:
    raw = os.environ.get(WIRE_CAPTURE_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def wire_capture_max_bytes() -> int:
    raw = os.environ.get(WIRE_CAPTURE_MAX_BYTES_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        selected = int(raw)
    except ValueError as error:
        raise WireCaptureError("wire capture byte limit must be an integer") from error
    if not 1 <= selected <= ABSOLUTE_MAX_BYTES:
        raise WireCaptureError("wire capture byte limit must be between 1 and 104857600")
    return selected


def load_or_create_wire_key() -> bytes:
    """Load the user-bound capture key from Windows Credential Manager."""

    if platform.system() != "Windows":
        raise WireCaptureError("encrypted wire capture is supported only on Windows")
    backend = keyring.get_keyring()
    if not type(backend).__module__.casefold().startswith("keyring.backends.windows"):
        raise WireCaptureError("encrypted wire capture requires Windows Credential Manager")
    try:
        encoded = backend.get_password(SERVICE_NAME, WIRE_CAPTURE_KEY_ACCOUNT)
        if encoded is None:
            key = token_bytes(32)
            backend.set_password(
                SERVICE_NAME,
                WIRE_CAPTURE_KEY_ACCOUNT,
                base64.urlsafe_b64encode(key).decode("ascii"),
            )
            return key
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (KeyringError, ValueError) as error:
        raise WireCaptureError("Windows Credential Manager wire key operation failed") from error
    if len(key) != 32:
        raise WireCaptureError("stored wire capture key has an invalid shape")
    return key


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, path)


@dataclass(slots=True)
class _DirectionState:
    direction: str
    partial_path: Path
    final_path: Path
    stream: BinaryIO
    frames: int = 0
    plaintext_bytes: int = 0
    stored_bytes: int = 0
    complete: bool = False
    truncated: bool = False


class EncryptedWireCapture:
    """Capture request and streamed response bodies without plaintext-at-rest."""

    def __init__(
        self,
        *,
        job_root: Path,
        job_id: str,
        key_provider: Callable[[], bytes] = load_or_create_wire_key,
        max_bytes: int | None = None,
    ) -> None:
        self.job_id = job_id
        self.attribution_uid = job_id
        self.max_bytes = max_bytes if max_bytes is not None else wire_capture_max_bytes()
        if not 1 <= self.max_bytes <= ABSOLUTE_MAX_BYTES:
            raise WireCaptureError("wire capture byte limit is outside the safe bound")
        key = key_provider()
        if len(key) != 32:
            raise WireCaptureError("wire capture key must contain exactly 32 bytes")
        self._cipher = AESGCM(key)
        self.root = job_root / "wire"
        self.root.mkdir(parents=True, exist_ok=False)
        self.audit_path = job_root / "audit" / "wire-capture.json"
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._states = {
            direction: self._open_direction(direction) for direction in ("request", "response")
        }
        self.status_code: int | None = None
        self.http_version: str | None = None
        self.response_header_names: list[str] = []
        self.failure_type: str | None = None
        self._write_audit("capturing")

    def _open_direction(self, direction: str) -> _DirectionState:
        partial = self.root / f"{direction}.wire.partial"
        final = self.root / f"{direction}.wire"
        stream = partial.open("xb")
        stream.write(_MAGIC)
        stream.flush()
        os.fsync(stream.fileno())
        return _DirectionState(direction, partial, final, stream)

    def capture_request(self, data: bytes) -> None:
        self._capture("request", data)
        self._complete("request")

    def response_headers(
        self, *, status_code: int, http_version: str, header_names: list[str]
    ) -> None:
        self.status_code = status_code
        self.http_version = http_version[:32]
        self.response_header_names = sorted({name.casefold()[:128] for name in header_names})[:100]
        self._write_audit("capturing")

    def capture_response(self, data: bytes) -> None:
        self._capture("response", data)

    def complete_response(self) -> None:
        self._complete("response")
        self._write_audit("complete")
        self._prune_completed_sessions()

    def complete_response_after_error(self, error: BaseException) -> None:
        """Seal a response whose complete terminal event preceded a read error."""

        self.failure_type = type(error).__name__[:128]
        self._complete("response")
        self._write_audit("complete_after_transport_error")
        self._prune_completed_sessions()

    def fail(self, error: BaseException) -> None:
        self.failure_type = type(error).__name__[:128]
        for state in self._states.values():
            if not state.stream.closed:
                state.stream.flush()
                os.fsync(state.stream.fileno())
                state.stream.close()
        self._write_audit("failed")
        self._prune_completed_sessions()

    def _capture(self, direction: str, data: bytes) -> None:
        state = self._states[direction]
        if state.complete:
            raise WireCaptureError("cannot append to a completed wire direction")
        state.plaintext_bytes += len(data)
        remaining = self.max_bytes - sum(item.stored_bytes for item in self._states.values())
        selected = data[: max(0, remaining)]
        if len(selected) != len(data):
            state.truncated = True
        if not selected:
            self._write_audit("capturing")
            return
        nonce = token_bytes(12)
        aad = f"{self.job_id}:{direction}:{state.frames}".encode()
        encrypted = self._cipher.encrypt(nonce, selected, aad)
        state.stream.write(_FRAME_HEADER.pack(len(encrypted), nonce))
        state.stream.write(encrypted)
        state.stream.flush()
        os.fsync(state.stream.fileno())
        state.frames += 1
        state.stored_bytes += len(selected)
        self._write_audit("capturing")

    def _complete(self, direction: str) -> None:
        state = self._states[direction]
        if state.complete:
            return
        state.stream.flush()
        os.fsync(state.stream.fileno())
        state.stream.close()
        os.replace(state.partial_path, state.final_path)
        state.complete = True
        self._write_audit("capturing")

    def _write_audit(self, state: str) -> None:
        _atomic_json(
            self.audit_path,
            {
                "version": 1,
                "attribution_uid": self.attribution_uid,
                "state": state,
                "cipher": "AES-256-GCM-framed",
                "max_plaintext_bytes": self.max_bytes,
                "status_code": self.status_code,
                "http_version": self.http_version,
                "response_header_names": self.response_header_names,
                "failure_type": self.failure_type,
                "retained": True,
                "directions": {
                    name: {
                        "frames": item.frames,
                        "plaintext_bytes": item.plaintext_bytes,
                        "stored_bytes": item.stored_bytes,
                        "complete": item.complete,
                        "truncated": item.truncated,
                    }
                    for name, item in self._states.items()
                },
            },
        )

    def _prune_completed_sessions(self) -> None:
        """Roll encrypted evidence by whole attribution UID, never by mixed frames."""

        candidates: list[tuple[float, Path, Path, int]] = []
        total = 0
        for job_root in self.root.parent.parent.iterdir():
            audit_path = job_root / "audit" / "wire-capture.json"
            wire_root = job_root / "wire"
            if not audit_path.is_file() or not wire_root.is_dir():
                continue
            try:
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(audit, dict) or audit.get("state") == "capturing":
                continue
            directions = audit.get("directions")
            stored = (
                sum(
                    int(item.get("stored_bytes", 0))
                    for item in directions.values()
                    if isinstance(item, dict)
                )
                if isinstance(directions, dict)
                else 0
            )
            total += stored
            candidates.append((audit_path.stat().st_mtime, job_root, audit_path, stored))
        for _modified, job_root, audit_path, stored in sorted(candidates):
            if total <= self.max_bytes or job_root == self.root.parent:
                continue
            wire_root = job_root / "wire"
            tombstone = job_root / f".wire-evicted-{job_root.name}"
            try:
                os.replace(wire_root, tombstone)
                shutil.rmtree(tombstone)
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                if isinstance(audit, dict):
                    audit["retained"] = False
                    _atomic_json(audit_path, audit)
                total -= stored
            except (OSError, ValueError):
                continue


def decrypt_wire_file(path: Path, *, job_id: str, direction: str, key: bytes) -> bytes:
    """Operator-only helper; callers must control any plaintext destination."""

    if direction not in {"request", "response"} or len(key) != 32:
        raise WireCaptureError("invalid wire decryption parameters")
    cipher = AESGCM(key)
    result = bytearray()
    with path.open("rb") as stream:
        if stream.read(len(_MAGIC)) != _MAGIC:
            raise WireCaptureError("wire capture header is invalid")
        sequence = 0
        while header := stream.read(_FRAME_HEADER.size):
            if len(header) != _FRAME_HEADER.size:
                raise WireCaptureError("wire capture frame header is truncated")
            size, nonce = _FRAME_HEADER.unpack(header)
            encrypted = stream.read(size)
            if len(encrypted) != size:
                raise WireCaptureError("wire capture frame is truncated")
            aad = f"{job_id}:{direction}:{sequence}".encode()
            result.extend(cipher.decrypt(nonce, encrypted, aad))
            sequence += 1
    return bytes(result)
