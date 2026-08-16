"""Apply a hash-bound static repair without executing or trusting model output."""

from __future__ import annotations

from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.models import CandidateFile, CandidatePatchPayload, ToolCandidatePayload


class CandidatePatchError(ValueError):
    """Raised when a model patch cannot be applied exactly and safely."""


def apply_candidate_patch(
    previous: ToolCandidatePayload,
    patch: CandidatePatchPayload,
) -> ToolCandidatePayload:
    """Apply exact unique replacements while preserving the candidate file set."""

    previous_hash = candidate_payload_sha256(previous)
    if patch.base_candidate_sha256 != previous_hash:
        raise CandidatePatchError("static repair patch targets a different candidate")

    contents = {file.path: file.content for file in previous.files}
    for edit in patch.edits:
        if edit.file_path not in contents:
            raise CandidatePatchError("static repair patch targets an unknown file")
        content = contents[edit.file_path]
        if content.count(edit.old_text) != 1:
            raise CandidatePatchError("static repair text must match exactly once")
        updated = content.replace(edit.old_text, edit.new_text, 1)
        if len(updated) > 100_000:
            raise CandidatePatchError("static repair would exceed candidate file bounds")
        contents[edit.file_path] = updated

    files = [CandidateFile(path=file.path, content=contents[file.path]) for file in previous.files]
    return ToolCandidatePayload(summary=previous.summary, files=files, risks=previous.risks)
