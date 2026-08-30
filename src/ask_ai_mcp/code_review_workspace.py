"""Allow-listed repository snapshotting for bounded code review jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

REPOSITORIES_ENV = "ASK_AI_MCP_REVIEW_REPOSITORIES"
PATCH_ROOTS_ENV = "ASK_AI_MCP_REVIEW_PATCH_ROOTS"
MAX_DIFF_BYTES = 1_000_000
MAX_CONTEXT_BYTES = 256_000
MAX_CHANGED_FILES = 100
MAX_CHANGED_LINES = 10_000
_REPARSE_POINT = 0x400
_SECRET_NAME = re.compile(
    r"(?i)(^|/)(\.env(?:\.|$)|.*(?:secret|credential|private[_-]?key|token).*)"
)
_SECRET_CONTENT = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*"
    r"[\"'][^\"'\r\n]{12,}[\"']|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_HOST_ABSOLUTE_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]+[^\s\"'<>|]+)")
_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "coverage",
        "__pycache__",
        "generated",
    }
)
_TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".cs",
        ".go",
        ".rs",
        ".cpp",
        ".cc",
        ".c",
        ".h",
        ".hpp",
        ".ps1",
        ".sh",
        ".sql",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".md",
        ".txt",
        ".ini",
        ".cfg",
        ".xml",
    }
)


class CodeReviewWorkspaceError(RuntimeError):
    """Raised when a requested source snapshot crosses a configured boundary."""


@dataclass(frozen=True)
class CodeReviewSanitizationDiagnostics:
    input_bytes: int
    input_sha256: str
    sanitized_bytes: int
    sanitized_sha256: str
    section_count: int
    accepted_section_count: int
    excluded_section_count: int
    exclusion_reason_counts: dict[str, int]
    host_path_redaction_count: int
    dropped_prefix_bytes: int

    def safe_payload(self) -> dict[str, object]:
        return {
            "failure_code": "PATCH_SANITIZATION_MISMATCH",
            "input_bytes": self.input_bytes,
            "input_sha256": self.input_sha256,
            "sanitized_bytes": self.sanitized_bytes,
            "sanitized_sha256": self.sanitized_sha256,
            "section_count": self.section_count,
            "accepted_section_count": self.accepted_section_count,
            "excluded_section_count": self.excluded_section_count,
            "exclusion_reason_counts": dict(sorted(self.exclusion_reason_counts.items())),
            "host_path_redaction_count": self.host_path_redaction_count,
            "dropped_prefix_bytes": self.dropped_prefix_bytes,
        }


class CodeReviewPatchSanitizationError(CodeReviewWorkspaceError):
    """Content-free staging rejection for a patch that still needs sanitization."""

    def __init__(self, diagnostics: CodeReviewSanitizationDiagnostics) -> None:
        self.diagnostics = diagnostics
        safe = json.dumps(
            diagnostics.safe_payload(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        super().__init__(f"staged patch sanitization mismatch: {safe}")


@dataclass(frozen=True)
class CodeReviewSnapshot:
    repository_id: str
    diff_text: str
    diff_sha256: str
    snapshot_sha256: str
    changed_files: tuple[str, ...]
    changed_line_count: int
    language: str
    context: tuple[dict[str, object], ...]
    omitted_context: tuple[str, ...]
    source_identity: dict[str, str]
    sanitization_diagnostics: CodeReviewSanitizationDiagnostics


@dataclass(frozen=True)
class CodeReviewStagedPatchRecord:
    repository_id: str
    patch_sha256: str
    receipt_sha256: str
    byte_length: int
    snapshot: CodeReviewSnapshot


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _redact_host_paths(value: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        token = _sha256(match.group(0).casefold().encode("utf-8"))[:12]
        return f"<HOST_PATH_{token}>"

    return _HOST_ABSOLUTE_PATH.sub(replacement, value)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    return bool(getattr(stat, "st_file_attributes", 0) & _REPARSE_POINT) or path.is_symlink()


def _assert_no_reparse_escape(path: Path, root: Path) -> None:
    current = path
    while True:
        if _is_reparse(current):
            raise CodeReviewWorkspaceError("reparse points are not accepted in review inputs")
        if current == root:
            break
        if not _is_within(current, root):
            raise CodeReviewWorkspaceError("review input escaped its allow-listed root")
        current = current.parent


def _validate_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise CodeReviewWorkspaceError("diff contains a non-relative path")
    return path.as_posix()


class CodeReviewRepositoryCatalog:
    def __init__(
        self,
        repositories: dict[str, Path] | None = None,
        *,
        environment_variable: str = REPOSITORIES_ENV,
    ) -> None:
        configured = (
            repositories
            if repositories is not None
            else self._load_environment(environment_variable)
        )
        self.repositories = {
            identifier: self._validate_root(identifier, path)
            for identifier, path in configured.items()
        }

    @staticmethod
    def _load_environment(environment_variable: str = REPOSITORIES_ENV) -> dict[str, Path]:
        raw = os.environ.get(environment_variable, "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise CodeReviewWorkspaceError(
                f"{environment_variable} must be a JSON object"
            ) from error
        if not isinstance(value, dict) or len(value) > 64:
            raise CodeReviewWorkspaceError(
                f"{environment_variable} must map repository IDs to paths"
            )
        return {str(key): Path(str(path)) for key, path in value.items()}

    @staticmethod
    def _validate_root(identifier: str, path: Path) -> Path:
        if re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", identifier) is None:
            raise CodeReviewWorkspaceError("repository IDs must be bounded lowercase identifiers")
        if not path.is_absolute() or not path.is_dir():
            raise CodeReviewWorkspaceError(f"repository {identifier} must be an absolute directory")
        absolute = Path(os.path.abspath(path))
        resolved = path.resolve(strict=True)
        if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
            raise CodeReviewWorkspaceError("repository roots cannot be symlinks or junctions")
        home = Path.home().resolve(strict=True)
        if resolved == Path(resolved.anchor) or resolved == home or _is_within(home, resolved):
            raise CodeReviewWorkspaceError(
                "drive roots and broad user-profile roots are prohibited"
            )
        _assert_no_reparse_escape(resolved, resolved)
        if not (resolved / ".git").exists():
            raise CodeReviewWorkspaceError(f"repository {identifier} is not a Git work tree")
        top = _run_git(resolved, "rev-parse", "--show-toplevel").strip()
        if Path(top).resolve(strict=True) != resolved:
            raise CodeReviewWorkspaceError(
                "configured repository must be the exact Git work-tree root"
            )
        return resolved

    def require(self, repository_id: str) -> Path:
        try:
            return self.repositories[repository_id]
        except KeyError as error:
            raise CodeReviewWorkspaceError("repository ID is not allow-listed") from error


def _run_git(root: Path, *arguments: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise CodeReviewWorkspaceError("Git is unavailable")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [git, "-C", str(root), *arguments],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        creationflags=creationflags,
    )
    if completed.returncode != 0:
        raise CodeReviewWorkspaceError("bounded Git snapshot command failed")
    try:
        return completed.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CodeReviewWorkspaceError("Git snapshot output was not UTF-8") from error


class CodeReviewSnapshotter:
    def __init__(
        self,
        catalog: CodeReviewRepositoryCatalog | None = None,
        patch_roots: tuple[Path, ...] | None = None,
    ) -> None:
        self.catalog = catalog or CodeReviewRepositoryCatalog()
        self.patch_roots = patch_roots if patch_roots is not None else self._patch_roots()

    @staticmethod
    def _patch_roots() -> tuple[Path, ...]:
        values = [value.strip() for value in os.environ.get(PATCH_ROOTS_ENV, "").split(";")]
        roots: list[Path] = []
        for value in values:
            if not value:
                continue
            path = Path(value)
            if not path.is_absolute() or not path.is_dir():
                raise CodeReviewWorkspaceError("patch roots must be existing absolute directories")
            absolute = Path(os.path.abspath(path))
            resolved = path.resolve(strict=True)
            if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
                raise CodeReviewWorkspaceError("patch roots cannot be symlinks or junctions")
            if resolved == Path(resolved.anchor) or resolved == Path.home().resolve(strict=True):
                raise CodeReviewWorkspaceError("broad patch roots are prohibited")
            roots.append(resolved)
        return tuple(roots)

    def from_refs(self, repository_id: str, base_ref: str, head_ref: str) -> CodeReviewSnapshot:
        root = self.catalog.require(repository_id)
        base = _run_git(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}").strip()
        head = _run_git(root, "rev-parse", "--verify", f"{head_ref}^{{commit}}").strip()
        raw_diff = _run_git(
            root,
            "diff",
            "--no-ext-diff",
            "--no-color",
            "--no-renames",
            "--unified=3",
            base,
            head,
            "--",
        )
        return self._build(
            repository_id,
            root,
            raw_diff,
            source_identity={"base_commit": base, "head_commit": head},
            context_ref=head,
        )

    def stage_patch(self, repository_id: str, patch_text: str) -> CodeReviewStagedPatchRecord:
        root = self.catalog.require(repository_id)
        patch_root = self._project_patch_root(repository_id)
        data = patch_text.encode("utf-8")
        if len(data) > MAX_DIFF_BYTES:
            raise CodeReviewWorkspaceError("patch input exceeds the bounded diff size")
        patch_sha256 = _sha256(data)
        snapshot = self._build(
            repository_id,
            root,
            patch_text,
            source_identity={"patch_sha256": patch_sha256},
            context_ref=None,
            reject_sanitization_mismatch=True,
        )
        if snapshot.diff_text.encode("utf-8") != data:
            raise CodeReviewPatchSanitizationError(snapshot.sanitization_diagnostics)
        receipt = {
            "schema_version": 1,
            "repository_id": repository_id,
            "patch_sha256": patch_sha256,
            "byte_length": len(data),
            "diff_sha256": snapshot.diff_sha256,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "changed_file_count": len(snapshot.changed_files),
            "changed_line_count": snapshot.changed_line_count,
        }
        receipt_data = (
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        receipt_sha256 = _sha256(receipt_data)
        self._commit_staged_pair(patch_root, patch_sha256, data, receipt_data)
        return CodeReviewStagedPatchRecord(
            repository_id=repository_id,
            patch_sha256=patch_sha256,
            receipt_sha256=receipt_sha256,
            byte_length=len(data),
            snapshot=snapshot,
        )

    def from_staged_patch(
        self, repository_id: str, patch_sha256: str, receipt_sha256: str
    ) -> CodeReviewSnapshot:
        root = self.catalog.require(repository_id)
        patch_root = self._project_patch_root(repository_id)
        patch_path, receipt_path = self._staged_paths(patch_root, patch_sha256)
        if not patch_path.is_file() or not receipt_path.is_file():
            raise CodeReviewWorkspaceError("staged patch and receipt must both exist")
        _assert_no_reparse_escape(patch_path.resolve(strict=True), patch_root)
        _assert_no_reparse_escape(receipt_path.resolve(strict=True), patch_root)
        if patch_path.stat().st_mode & stat.S_IWRITE or receipt_path.stat().st_mode & stat.S_IWRITE:
            raise CodeReviewWorkspaceError("staged patch and receipt must remain read-only")
        data = patch_path.read_bytes()
        receipt_data = receipt_path.read_bytes()
        if _sha256(data) != patch_sha256 or _sha256(receipt_data) != receipt_sha256:
            raise CodeReviewWorkspaceError("staged patch or receipt SHA-256 does not match")
        if len(data) > MAX_DIFF_BYTES:
            raise CodeReviewWorkspaceError("patch input exceeds the bounded diff size")
        try:
            receipt = json.loads(receipt_data.decode("utf-8", errors="strict"))
            patch_text = data.decode("utf-8", errors="strict")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CodeReviewWorkspaceError("staged patch receipt is invalid") from error
        expected = {
            "schema_version": 1,
            "repository_id": repository_id,
            "patch_sha256": patch_sha256,
            "byte_length": len(data),
        }
        if not isinstance(receipt, dict) or any(
            receipt.get(key) != value for key, value in expected.items()
        ):
            raise CodeReviewWorkspaceError("staged patch receipt does not match its patch")
        snapshot = self._build(
            repository_id,
            root,
            patch_text,
            source_identity={"patch_sha256": patch_sha256},
            context_ref=None,
        )
        derived = {
            "diff_sha256": snapshot.diff_sha256,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "changed_file_count": len(snapshot.changed_files),
            "changed_line_count": snapshot.changed_line_count,
        }
        if set(receipt) != set(expected) | set(derived) or any(
            receipt.get(key) != value for key, value in derived.items()
        ):
            raise CodeReviewWorkspaceError("staged patch receipt does not match its snapshot")
        return snapshot

    def _project_patch_root(self, repository_id: str) -> Path:
        matches = [root for root in self.patch_roots if root.name == repository_id]
        if len(matches) != 1:
            raise CodeReviewWorkspaceError(
                "exactly one project-specific patch root must match the repository ID"
            )
        return matches[0]

    @staticmethod
    def _staged_paths(patch_root: Path, patch_sha256: str) -> tuple[Path, Path]:
        if re.fullmatch(r"[a-f0-9]{64}", patch_sha256) is None:
            raise CodeReviewWorkspaceError("patch SHA-256 is invalid")
        return patch_root / f"{patch_sha256}.patch", patch_root / f"{patch_sha256}.receipt.json"

    @classmethod
    def _commit_staged_pair(
        cls, patch_root: Path, patch_sha256: str, data: bytes, receipt_data: bytes
    ) -> None:
        patch_path, receipt_path = cls._staged_paths(patch_root, patch_sha256)
        lock_path = patch_root / f".{patch_sha256}.lock"
        try:
            lock_path.mkdir()
        except FileExistsError as error:
            raise CodeReviewWorkspaceError("staged patch is locked or incomplete") from error
        token = uuid4().hex
        patch_temp = patch_root / f".{token}.patch.tmp"
        receipt_temp = patch_root / f".{token}.receipt.tmp"
        try:
            if patch_path.exists() or receipt_path.exists():
                if not patch_path.is_file() or not receipt_path.is_file():
                    raise CodeReviewWorkspaceError("existing staged patch pair is incomplete")
                if patch_path.read_bytes() != data or receipt_path.read_bytes() != receipt_data:
                    raise CodeReviewWorkspaceError("existing staged patch pair does not match")
                return
            cls._write_synced(patch_temp, data)
            cls._write_synced(receipt_temp, receipt_data)
            os.replace(patch_temp, patch_path)
            os.chmod(patch_path, stat.S_IREAD)
            os.replace(receipt_temp, receipt_path)
            os.chmod(receipt_path, stat.S_IREAD)
        finally:
            for temporary in (patch_temp, receipt_temp):
                if temporary.exists():
                    temporary.unlink()
            lock_path.rmdir()

    @staticmethod
    def _write_synced(path: Path, data: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    def _build(
        self,
        repository_id: str,
        root: Path,
        raw_diff: str,
        *,
        source_identity: dict[str, str],
        context_ref: str | None,
        reject_sanitization_mismatch: bool = False,
    ) -> CodeReviewSnapshot:
        sections = self._sections(raw_diff)
        submodule_roots = self._submodule_roots(root, context_ref)
        accepted: list[tuple[str, str]] = []
        omitted: list[str] = []
        exclusion_reason_counts: dict[str, int] = {}
        host_path_redaction_count = 0
        for relative, section in sections:
            reason = self._exclusion_reason(
                relative,
                section,
                root,
                submodule_roots,
                use_repository_context=context_ref is not None,
            )
            if reason:
                omitted.append(f"{relative}: {reason}")
                code = self._exclusion_reason_code(reason)
                exclusion_reason_counts[code] = exclusion_reason_counts.get(code, 0) + 1
            else:
                host_path_redaction_count += len(_HOST_ABSOLUTE_PATH.findall(section))
                redacted = _redact_host_paths(section)
                if redacted != section:
                    omitted.append(f"{relative}: host absolute paths redacted")
                accepted.append((relative, redacted))
        diff_text = "".join(section for _, section in accepted)
        diff_bytes = diff_text.encode("utf-8")
        raw_diff_bytes = raw_diff.encode("utf-8")
        first_header = re.search(r"(?m)^diff --git ", raw_diff)
        dropped_prefix_bytes = len(
            raw_diff[: first_header.start()].encode("utf-8") if first_header else raw_diff_bytes
        )
        sanitization_diagnostics = CodeReviewSanitizationDiagnostics(
            input_bytes=len(raw_diff_bytes),
            input_sha256=_sha256(raw_diff_bytes),
            sanitized_bytes=len(diff_bytes),
            sanitized_sha256=_sha256(diff_bytes),
            section_count=len(sections),
            accepted_section_count=len(accepted),
            excluded_section_count=len(sections) - len(accepted),
            exclusion_reason_counts=exclusion_reason_counts,
            host_path_redaction_count=host_path_redaction_count,
            dropped_prefix_bytes=dropped_prefix_bytes,
        )
        if not accepted:
            if reject_sanitization_mismatch:
                raise CodeReviewPatchSanitizationError(sanitization_diagnostics)
            raise CodeReviewWorkspaceError("no reviewable text diff remains after exclusions")
        if len(accepted) > MAX_CHANGED_FILES:
            raise CodeReviewWorkspaceError("changed file count exceeds the review limit")
        if len(diff_bytes) > MAX_DIFF_BYTES:
            raise CodeReviewWorkspaceError("sanitized diff exceeds the review limit")
        changed_lines = sum(
            1
            for line in diff_text.splitlines()
            if (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        )
        if changed_lines > MAX_CHANGED_LINES:
            raise CodeReviewWorkspaceError("changed line count exceeds the review limit")
        context = self._context(root, accepted, omitted, context_ref=context_ref)
        manifest = {
            "repository_id": repository_id,
            "diff_sha256": _sha256(diff_bytes),
            "changed_files": [relative for relative, _ in accepted],
            "changed_line_count": changed_lines,
            "context": context,
            "omitted_context": omitted,
            "source_identity": source_identity,
        }
        canonical = json.dumps(
            {"manifest": manifest, "diff": diff_text},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return CodeReviewSnapshot(
            repository_id=repository_id,
            diff_text=diff_text,
            diff_sha256=manifest["diff_sha256"],
            snapshot_sha256=_sha256(canonical),
            changed_files=tuple(relative for relative, _ in accepted),
            changed_line_count=changed_lines,
            language=self._language(tuple(relative for relative, _ in accepted)),
            context=tuple(context),
            omitted_context=tuple(omitted),
            source_identity=source_identity,
            sanitization_diagnostics=sanitization_diagnostics,
        )

    @staticmethod
    def _exclusion_reason_code(reason: str) -> str:
        return {
            "submodule content excluded": "submodule_content",
            "vendor, generated, or build artifact excluded": "generated_or_vendor_content",
            "secret-bearing path or content excluded": "secret_bearing_content",
            "binary diff excluded": "binary_diff",
            "unsupported or binary file type excluded": "unsupported_file_type",
            "large or non-file context excluded": "large_or_non_file_context",
        }.get(reason, "other_exclusion")

    @staticmethod
    def _sections(raw_diff: str) -> list[tuple[str, str]]:
        matches = list(re.finditer(r"(?m)^diff --git a/(\S+) b/(\S+)\r?$", raw_diff))
        if not matches:
            raise CodeReviewWorkspaceError("input is not a unified Git diff")
        sections: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            left = _validate_relative(match.group(1))
            right = _validate_relative(match.group(2))
            if left != right:
                raise CodeReviewWorkspaceError("renames are excluded from bounded review snapshots")
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_diff)
            sections.append((right, raw_diff[match.start() : end]))
        return sections

    @staticmethod
    def _exclusion_reason(
        relative: str,
        section: str,
        root: Path,
        submodule_roots: tuple[PurePosixPath, ...],
        *,
        use_repository_context: bool,
    ) -> str | None:
        path = PurePosixPath(relative)
        if any(path == item or item in path.parents for item in submodule_roots):
            return "submodule content excluded"
        if re.search(r"(?m)^(?:old |new |deleted file |new file )?mode 160000$", section):
            return "submodule content excluded"
        folded_parts = {part.casefold() for part in path.parts}
        if folded_parts & _EXCLUDED_PARTS:
            return "vendor, generated, or build artifact excluded"
        if _SECRET_NAME.search(relative) or _SECRET_CONTENT.search(section):
            return "secret-bearing path or content excluded"
        if "GIT binary patch" in section or "Binary files " in section:
            return "binary diff excluded"
        if path.suffix.casefold() not in _TEXT_SUFFIXES:
            return "unsupported or binary file type excluded"
        candidate = root.joinpath(*path.parts)
        if use_repository_context and candidate.exists():
            resolved = candidate.resolve(strict=True)
            if not _is_within(resolved, root):
                raise CodeReviewWorkspaceError("changed file escaped repository root")
            _assert_no_reparse_escape(candidate, root)
            if candidate.is_dir() or candidate.stat().st_size > 128_000:
                return "large or non-file context excluded"
        return None

    @staticmethod
    def _submodule_roots(root: Path, context_ref: str | None) -> tuple[PurePosixPath, ...]:
        if context_ref is None:
            return ()
        roots: list[PurePosixPath] = []
        listing = _run_git(root, "ls-tree", "-r", context_ref)
        for line in listing.splitlines():
            metadata, separator, relative = line.partition("\t")
            if separator and metadata.startswith("160000 "):
                roots.append(PurePosixPath(_validate_relative(relative)))
        return tuple(roots)

    @staticmethod
    def _context(
        root: Path,
        accepted: list[tuple[str, str]],
        omitted: list[str],
        *,
        context_ref: str | None,
    ) -> list[dict[str, object]]:
        if context_ref is None:
            return []
        result: list[dict[str, object]] = []
        used = 0
        for relative, section in accepted:
            try:
                text = _run_git(root, "show", f"{context_ref}:{relative}")
                lines = text.splitlines()
            except (CodeReviewWorkspaceError, OSError, UnicodeDecodeError):
                omitted.append(f"{relative}: UTF-8 context unavailable")
                continue
            ranges: list[tuple[int, int]] = []
            for match in re.finditer(r"(?m)^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", section):
                start = max(1, int(match.group(1)) - 20)
                count = int(match.group(2) or "1")
                end = min(len(lines), int(match.group(1)) + max(count, 1) + 19)
                ranges.append((start, end))
            fragments: list[dict[str, object]] = []
            for start, end in ranges[:20]:
                text = "\n".join(
                    f"{number}: {lines[number - 1]}" for number in range(start, end + 1)
                )
                redacted = _redact_host_paths(text)
                if redacted != text:
                    omitted.append(f"{relative}: host absolute paths redacted from context")
                text = redacted
                encoded = text.encode("utf-8")
                if used + len(encoded) > MAX_CONTEXT_BYTES:
                    omitted.append(f"{relative}: context truncated at global byte limit")
                    break
                used += len(encoded)
                fragments.append({"line_start": start, "line_end": end, "text": text})
            result.append({"file": relative, "fragments": fragments})
        return result

    @staticmethod
    def _language(files: tuple[str, ...]) -> str:
        counts: dict[str, int] = {}
        mapping = {
            ".py": "python",
            ".pyi": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ps1": "powershell",
            ".cs": "csharp",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".c": "c",
        }
        for value in files:
            language = mapping.get(PurePosixPath(value).suffix.casefold(), "other")
            counts[language] = counts.get(language, 0) + 1
        return max(counts, key=counts.get)
