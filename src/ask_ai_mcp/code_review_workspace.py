"""Allow-listed repository snapshotting for bounded code review jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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
    def __init__(self, repositories: dict[str, Path] | None = None) -> None:
        configured = repositories if repositories is not None else self._load_environment()
        self.repositories = {
            identifier: self._validate_root(identifier, path)
            for identifier, path in configured.items()
        }

    @staticmethod
    def _load_environment() -> dict[str, Path]:
        raw = os.environ.get(REPOSITORIES_ENV, "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise CodeReviewWorkspaceError(f"{REPOSITORIES_ENV} must be a JSON object") from error
        if not isinstance(value, dict) or len(value) > 64:
            raise CodeReviewWorkspaceError(f"{REPOSITORIES_ENV} must map repository IDs to paths")
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

    def from_patch(
        self, repository_id: str, patch_file: str, expected_sha256: str
    ) -> CodeReviewSnapshot:
        root = self.catalog.require(repository_id)
        candidate = Path(patch_file)
        if not candidate.is_absolute() or not candidate.is_file():
            raise CodeReviewWorkspaceError("patch input must be an absolute regular file")
        resolved = candidate.resolve(strict=True)
        allowed = next((item for item in self.patch_roots if _is_within(resolved, item)), None)
        if allowed is None:
            raise CodeReviewWorkspaceError("patch input is outside configured patch roots")
        _assert_no_reparse_escape(resolved, allowed)
        data = resolved.read_bytes()
        if len(data) > MAX_DIFF_BYTES:
            raise CodeReviewWorkspaceError("patch input exceeds the bounded diff size")
        actual = _sha256(data)
        if actual != expected_sha256:
            raise CodeReviewWorkspaceError("patch SHA-256 does not match")
        try:
            raw_diff = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise CodeReviewWorkspaceError("patch input must be UTF-8") from error
        return self._build(
            repository_id,
            root,
            raw_diff,
            source_identity={"patch_sha256": actual},
            context_ref=None,
        )

    def _build(
        self,
        repository_id: str,
        root: Path,
        raw_diff: str,
        *,
        source_identity: dict[str, str],
        context_ref: str | None,
    ) -> CodeReviewSnapshot:
        sections = self._sections(raw_diff)
        submodule_roots = self._submodule_roots(root, context_ref)
        accepted: list[tuple[str, str]] = []
        omitted: list[str] = []
        for relative, section in sections:
            reason = self._exclusion_reason(relative, section, root, submodule_roots)
            if reason:
                omitted.append(f"{relative}: {reason}")
            else:
                redacted = _redact_host_paths(section)
                if redacted != section:
                    omitted.append(f"{relative}: host absolute paths redacted")
                accepted.append((relative, redacted))
        if not accepted:
            raise CodeReviewWorkspaceError("no reviewable text diff remains after exclusions")
        if len(accepted) > MAX_CHANGED_FILES:
            raise CodeReviewWorkspaceError("changed file count exceeds the review limit")
        diff_text = "".join(section for _, section in accepted)
        diff_bytes = diff_text.encode("utf-8")
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
        )

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
        if candidate.exists():
            resolved = candidate.resolve(strict=True)
            if not _is_within(resolved, root):
                raise CodeReviewWorkspaceError("changed file escaped repository root")
            _assert_no_reparse_escape(candidate, root)
            if candidate.is_dir() or candidate.stat().st_size > 128_000:
                return "large or non-file context excluded"
        return None

    @staticmethod
    def _submodule_roots(root: Path, context_ref: str | None) -> tuple[PurePosixPath, ...]:
        roots: list[PurePosixPath] = []
        listing = (
            _run_git(root, "ls-tree", "-r", context_ref)
            if context_ref is not None
            else _run_git(root, "ls-files", "--stage")
        )
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
        result: list[dict[str, object]] = []
        used = 0
        for relative, section in accepted:
            candidate = root.joinpath(*PurePosixPath(relative).parts)
            try:
                if context_ref is not None:
                    text = _run_git(root, "show", f"{context_ref}:{relative}")
                else:
                    if not candidate.is_file():
                        raise OSError("current file unavailable")
                    text = candidate.read_text(encoding="utf-8", errors="strict")
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
