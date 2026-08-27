"""Frozen, allow-listed repository inputs for coding candidates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from ask_ai_mcp.code_review_workspace import (
    CodeReviewRepositoryCatalog,
    CodeReviewWorkspaceError,
    _run_git,
)
from ask_ai_mcp.coding_models import CodingSubmitCommand

MAX_FILE_BYTES = 160_000
MAX_TOTAL_BYTES = 700_000
_ALLOWED_SUFFIXES = frozenset(
    {".cs", ".asmdef", ".json", ".xml", ".yaml", ".yml", ".md", ".ps1", ".py"}
)
_EXCLUDED_PARTS = frozenset(
    {".git", ".venv", "library", "temp", "obj", "logs", "build", "node_modules"}
)
_HOST_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/]+(?:Users|Dev|AI)[\\/])")
_SECRET_CONTENT = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*"
    r"[\"'][^\"'\r\n]{12,}[\"']|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class CodingSourceFile:
    path: str
    sha256: str
    content: str
    target: bool
    exists: bool


@dataclass(frozen=True)
class CodingSnapshot:
    repository_id: str
    base_commit: str
    snapshot_sha256: str
    files: tuple[CodingSourceFile, ...]

    @property
    def target_files(self) -> dict[str, CodingSourceFile]:
        return {item.path: item for item in self.files if item.target}


class CodingSnapshotter:
    def __init__(self, catalog: CodeReviewRepositoryCatalog | None = None) -> None:
        self.catalog = catalog or CodeReviewRepositoryCatalog()

    def capture(self, command: CodingSubmitCommand) -> CodingSnapshot:
        root = self.catalog.require(command.repository_id)
        commit = _run_git(root, "rev-parse", "--verify", f"{command.base_ref}^{{commit}}").strip()
        tree = _run_git(root, "ls-tree", "-r", commit)
        tracked: set[str] = set()
        submodules: list[PurePosixPath] = []
        for line in tree.splitlines():
            metadata, separator, relative = line.partition("\t")
            if not separator:
                continue
            if metadata.startswith("160000 "):
                submodules.append(PurePosixPath(relative))
            else:
                tracked.add(relative)
        files: list[CodingSourceFile] = []
        total = 0
        for path, target in [
            *((value, True) for value in command.target_files),
            *((value, False) for value in command.context_files),
        ]:
            self._validate_path(path)
            candidate_path = PurePosixPath(path)
            if any(
                candidate_path == submodule or submodule in candidate_path.parents
                for submodule in submodules
            ):
                raise CodeReviewWorkspaceError("submodule content is excluded")
            exists = path in tracked
            if not exists and not target:
                raise CodeReviewWorkspaceError("context files must exist at the frozen commit")
            content = _run_git(root, "show", f"{commit}:{path}") if exists else ""
            data = content.encode("utf-8")
            if _HOST_PATH.search(content) or _SECRET_CONTENT.search(content):
                raise CodeReviewWorkspaceError(
                    "coding input contains a prohibited host path or secret"
                )
            if len(data) > MAX_FILE_BYTES:
                raise CodeReviewWorkspaceError("coding input file exceeds the per-file byte limit")
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise CodeReviewWorkspaceError("coding snapshot exceeds the total byte limit")
            files.append(
                CodingSourceFile(
                    path=path,
                    sha256=_sha256(data),
                    content=content,
                    target=target,
                    exists=exists,
                )
            )
        canonical = json.dumps(
            {
                "repository_id": command.repository_id,
                "base_commit": commit,
                "files": [item.__dict__ for item in files],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return CodingSnapshot(
            repository_id=command.repository_id,
            base_commit=commit,
            snapshot_sha256=_sha256(canonical),
            files=tuple(files),
        )

    @staticmethod
    def _validate_path(value: str) -> None:
        path = PurePosixPath(value)
        if {part.casefold() for part in path.parts} & _EXCLUDED_PARTS:
            raise CodeReviewWorkspaceError(
                "generated, vendor, or runtime artifact paths are excluded"
            )
        if path.suffix.casefold() not in _ALLOWED_SUFFIXES:
            raise CodeReviewWorkspaceError("coding candidates are limited to approved text formats")
        folded = value.casefold()
        if any(token in folded for token in ("secret", "credential", "private_key", ".env")):
            raise CodeReviewWorkspaceError("secret-bearing paths are excluded")
