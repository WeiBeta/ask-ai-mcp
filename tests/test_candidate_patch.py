"""Safety tests for hash-bound static candidate edits."""

import pytest

from ask_ai_mcp.candidate_patch import CandidatePatchError, apply_candidate_patch
from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.models import (
    CandidateFile,
    CandidatePatchPayload,
    CandidateTextEdit,
    ToolCandidatePayload,
)


def candidate(content: str = "VALUE = 1\n") -> ToolCandidatePayload:
    return ToolCandidatePayload(
        summary="Synthetic candidate",
        files=[
            CandidateFile(path="tool.py", content=content),
            CandidateFile(path="test_tool.py", content="import unittest\n"),
        ],
    )


def patch(previous: ToolCandidatePayload, **updates: object) -> CandidatePatchPayload:
    values = {
        "base_candidate_sha256": candidate_payload_sha256(previous),
        "summary": "Replace one exact unsafe construct.",
        "edits": [
            CandidateTextEdit(file_path="tool.py", old_text="VALUE = 1", new_text="VALUE = 2")
        ],
    }
    values.update(updates)
    return CandidatePatchPayload.model_validate(values)


def test_patch_summary_is_optional_protocol_metadata() -> None:
    previous = candidate()

    parsed = CandidatePatchPayload.model_validate(
        {
            "base_candidate_sha256": candidate_payload_sha256(previous),
            "edits": [
                {
                    "file_path": "tool.py",
                    "old_text": "VALUE = 1",
                    "new_text": "VALUE = 2",
                }
            ],
        }
    )

    assert parsed.summary == "Static policy repair."


def test_patch_applies_one_unique_edit_without_changing_file_set() -> None:
    previous = candidate()

    result = apply_candidate_patch(previous, patch(previous))

    assert result.files[0].content == "VALUE = 2"
    assert [file.path for file in result.files] == ["tool.py", "test_tool.py"]
    assert result.summary == previous.summary


def test_patch_rejects_wrong_hash_unknown_file_and_ambiguous_text() -> None:
    previous = candidate("VALUE = 1\nVALUE = 1\n")

    with pytest.raises(CandidatePatchError, match="different candidate"):
        apply_candidate_patch(
            previous,
            patch(previous, base_candidate_sha256="0" * 64),
        )
    with pytest.raises(CandidatePatchError, match="unknown file"):
        apply_candidate_patch(
            previous,
            patch(
                previous,
                edits=[
                    CandidateTextEdit(
                        file_path="missing.py",
                        old_text="VALUE = 1",
                        new_text="VALUE = 2",
                    )
                ],
            ),
        )
    with pytest.raises(CandidatePatchError, match="exactly once"):
        apply_candidate_patch(previous, patch(previous))
