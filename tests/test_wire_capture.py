from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from ask_ai_mcp.wire_capture import (
    EncryptedWireCapture,
    WireCaptureError,
    decrypt_wire_file,
)


def test_capture_creates_its_own_audit_directory(tmp_path: Path) -> None:
    job_id = "00000000-0000-4000-8000-000000000001"
    job_root = tmp_path / job_id
    job_root.mkdir()

    capture = EncryptedWireCapture(
        job_root=job_root,
        job_id=job_id,
        key_provider=lambda: b"k" * 32,
        max_bytes=1_024,
    )

    assert (job_root / "audit" / "wire-capture.json").is_file()
    capture.fail(RuntimeError("synthetic stop"))


def test_encrypted_capture_round_trips_by_attribution_uid_without_plaintext_at_rest(
    tmp_path: Path,
) -> None:
    job_id = "11111111-1111-4111-8111-111111111111"
    job_root = tmp_path / job_id
    (job_root / "audit").mkdir(parents=True)
    key = b"k" * 32
    capture = EncryptedWireCapture(
        job_root=job_root,
        job_id=job_id,
        key_provider=lambda: key,
        max_bytes=1_024,
    )

    request = b'{"prompt":"PRIVATE_SOURCE","authorization":"not-recorded-by-caller"}'
    response = b'{"result":"PRIVATE_MODEL_OUTPUT"}'
    capture.capture_request(request)
    capture.response_headers(
        status_code=200,
        http_version="HTTP/1.1",
        header_names=["content-type", "set-cookie"],
    )
    capture.capture_response(response[:10])
    capture.capture_response(response[10:])
    capture.complete_response()

    request_path = job_root / "wire" / "request.wire"
    response_path = job_root / "wire" / "response.wire"
    assert b"PRIVATE_SOURCE" not in request_path.read_bytes()
    assert b"PRIVATE_MODEL_OUTPUT" not in response_path.read_bytes()
    assert decrypt_wire_file(request_path, job_id=job_id, direction="request", key=key) == request
    assert (
        decrypt_wire_file(response_path, job_id=job_id, direction="response", key=key) == response
    )
    audit = json.loads((job_root / "audit" / "wire-capture.json").read_text("utf-8"))
    assert audit["attribution_uid"] == job_id
    assert audit["state"] == "complete"
    assert audit["directions"]["response"]["frames"] == 2
    assert "set-cookie" in audit["response_header_names"]
    assert "PRIVATE_" not in json.dumps(audit)


def test_failed_capture_preserves_decryptable_partial_response(tmp_path: Path) -> None:
    job_id = "22222222-2222-4222-8222-222222222222"
    job_root = tmp_path / job_id
    (job_root / "audit").mkdir(parents=True)
    key = b"p" * 32
    capture = EncryptedWireCapture(
        job_root=job_root,
        job_id=job_id,
        key_provider=lambda: key,
        max_bytes=1_024,
    )
    capture.capture_request(b"request")
    capture.response_headers(status_code=200, http_version="HTTP/1.1", header_names=[])
    capture.capture_response(b'{"partial":')
    capture.fail(RuntimeError("PRIVATE_ERROR_BODY"))

    partial = job_root / "wire" / "response.wire.partial"
    assert (
        decrypt_wire_file(partial, job_id=job_id, direction="response", key=key) == b'{"partial":'
    )
    audit_text = (job_root / "audit" / "wire-capture.json").read_text("utf-8")
    assert '"state": "failed"' in audit_text
    assert '"failure_type": "RuntimeError"' in audit_text
    assert "PRIVATE_ERROR_BODY" not in audit_text


def test_terminal_response_after_read_error_is_sealed_with_content_free_audit(
    tmp_path: Path,
) -> None:
    job_id = "23232323-2323-4232-8232-232323232323"
    job_root = tmp_path / job_id
    (job_root / "audit").mkdir(parents=True)
    key = b"t" * 32
    capture = EncryptedWireCapture(
        job_root=job_root,
        job_id=job_id,
        key_provider=lambda: key,
        max_bytes=1_024,
    )
    response = b"event: response.completed\ndata: {}\n\n"
    capture.capture_request(b"request")
    capture.response_headers(status_code=200, http_version="HTTP/1.1", header_names=[])
    capture.capture_response(response)
    capture.complete_response_after_error(RuntimeError("PRIVATE_TRAILER_ERROR"))

    final = job_root / "wire" / "response.wire"
    assert decrypt_wire_file(final, job_id=job_id, direction="response", key=key) == response
    audit_text = (job_root / "audit" / "wire-capture.json").read_text("utf-8")
    audit = json.loads(audit_text)
    assert audit["state"] == "complete_after_transport_error"
    assert audit["failure_type"] == "RuntimeError"
    assert audit["directions"]["response"]["complete"] is True
    assert "PRIVATE_TRAILER_ERROR" not in audit_text


def test_capture_enforces_one_hundred_mib_absolute_limit(tmp_path: Path) -> None:
    job_root = tmp_path / "job"
    (job_root / "audit").mkdir(parents=True)
    with pytest.raises(WireCaptureError, match="outside the safe bound"):
        EncryptedWireCapture(
            job_root=job_root,
            job_id="33333333-3333-4333-8333-333333333333",
            key_provider=lambda: b"x" * 32,
            max_bytes=100 * 1024 * 1024 + 1,
        )


def test_capture_limit_marks_truncation_without_exceeding_bound(tmp_path: Path) -> None:
    job_id = "44444444-4444-4444-8444-444444444444"
    job_root = tmp_path / job_id
    (job_root / "audit").mkdir(parents=True)
    key = b"z" * 32
    capture = EncryptedWireCapture(
        job_root=job_root,
        job_id=job_id,
        key_provider=lambda: key,
        max_bytes=8,
    )
    capture.capture_request(b"123456")
    capture.capture_response(b"ABCDEFG")
    capture.complete_response()
    audit = json.loads((job_root / "audit" / "wire-capture.json").read_text("utf-8"))
    assert audit["directions"]["response"]["plaintext_bytes"] == 7
    assert audit["directions"]["response"]["stored_bytes"] == 2
    assert audit["directions"]["response"]["truncated"] is True


def test_attribution_uid_is_authenticated_as_aad(tmp_path: Path) -> None:
    job_id = "55555555-5555-4555-8555-555555555555"
    job_root = tmp_path / job_id
    (job_root / "audit").mkdir(parents=True)
    key = b"a" * 32
    capture = EncryptedWireCapture(
        job_root=job_root,
        job_id=job_id,
        key_provider=lambda: key,
        max_bytes=128,
    )
    capture.capture_request(b"payload")
    with pytest.raises(InvalidTag):
        decrypt_wire_file(
            job_root / "wire" / "request.wire",
            job_id="66666666-6666-4666-8666-666666666666",
            direction="request",
            key=key,
        )


def test_retention_evicts_oldest_capture_as_one_uid_bundle(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    key = b"r" * 32
    ids = (
        "77777777-7777-4777-8777-777777777777",
        "88888888-8888-4888-8888-888888888888",
    )
    for job_id in ids:
        job_root = jobs_root / job_id
        (job_root / "audit").mkdir(parents=True)
        capture = EncryptedWireCapture(
            job_root=job_root,
            job_id=job_id,
            key_provider=lambda: key,
            max_bytes=8,
        )
        capture.capture_request(b"12345678")
        capture.complete_response()

    first_root = jobs_root / ids[0]
    second_root = jobs_root / ids[1]
    first_audit = json.loads((first_root / "audit" / "wire-capture.json").read_text("utf-8"))
    second_audit = json.loads((second_root / "audit" / "wire-capture.json").read_text("utf-8"))
    assert first_audit["retained"] is False
    assert not (first_root / "wire").exists()
    assert second_audit["retained"] is True
    assert (second_root / "wire").is_dir()
