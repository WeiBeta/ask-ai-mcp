"""Defense-in-depth AST checks for untrusted Python candidates.

These checks are not a sandbox. Passing them only permits a candidate to be
staged for a later Windows Sandbox run.
"""

from __future__ import annotations

import ast
import re
import sys

from ask_ai_mcp.hashing import candidate_payload_sha256
from ask_ai_mcp.models import (
    FindingSeverity,
    StaticAnalysisReport,
    StaticFinding,
    ToolBuildSpec,
    ToolCandidatePayload,
)

FORBIDDEN_MODULE_ROOTS = frozenset(
    {
        "aiohttp",
        "builtins",
        "ctypes",
        "ftplib",
        "http",
        "httpx",
        "importlib",
        "keyring",
        "marshal",
        "multiprocessing",
        "os",
        "pickle",
        "requests",
        "shutil",
        "smtplib",
        "socket",
        "ssl",
        "subprocess",
        "sys",
        "tempfile",
        "threading",
        "urllib",
        "winreg",
    }
)

PACKAGE_IMPORT_ROOTS = {
    "openpyxl": frozenset({"openpyxl"}),
    "pillow": frozenset({"PIL"}),
    "pymupdf": frozenset({"fitz", "pymupdf"}),
    "pypdf": frozenset({"pypdf"}),
    "python-docx": frozenset({"docx"}),
    "python-pptx": frozenset({"pptx"}),
}

FORBIDDEN_CALLS = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "setattr",
        "vars",
    }
)

FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "__builtins__",
        "__class__",
        "__closure__",
        "__code__",
        "__globals__",
        "__loader__",
        "__mro__",
        "__spec__",
        "__subclasses__",
    }
)

_ABSOLUTE_PATH = re.compile(r"^(?:[a-zA-Z]:[\\/]|[\\/]{1,2})")


class _CandidateVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        file_path: str,
        allowed_import_roots: frozenset[str],
        local_import_roots: frozenset[str],
    ) -> None:
        self.file_path = file_path
        self.allowed_import_roots = allowed_import_roots
        self.local_import_roots = local_import_roots
        self.findings: list[StaticFinding] = []

    def add(self, code: str, message: str, node: ast.AST) -> None:
        self.findings.append(
            StaticFinding(
                file_path=self.file_path,
                code=code,
                severity=FindingSeverity.ERROR,
                message=message,
                line=getattr(node, "lineno", None),
            )
        )

    def check_import(self, module: str, node: ast.AST) -> None:
        root = module.split(".", maxsplit=1)[0]
        if root in FORBIDDEN_MODULE_ROOTS:
            self.add("forbidden_import", f"Import '{root}' is forbidden", node)
        elif (
            root not in sys.stdlib_module_names
            and root not in self.allowed_import_roots
            and root not in self.local_import_roots
        ):
            self.add("unapproved_import", f"Import '{root}' is not allow-listed", node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.check_import(alias.name, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self.check_import(node.module, node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            self.add("forbidden_call", f"Call '{node.func.id}' is forbidden", node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRIBUTES:
            self.add("forbidden_attribute", f"Attribute '{node.attr}' is forbidden", node)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _ABSOLUTE_PATH.match(node.value):
            self.add("absolute_path_literal", "Absolute path literals are forbidden", node)
        self.generic_visit(node)


def _allowed_import_roots(spec: ToolBuildSpec) -> frozenset[str]:
    roots: set[str] = set()
    for package in spec.allowed_packages:
        roots.update(PACKAGE_IMPORT_ROOTS.get(package.casefold(), ()))
    return frozenset(roots)


def analyze_candidate(
    spec: ToolBuildSpec,
    payload: ToolCandidatePayload,
) -> StaticAnalysisReport:
    findings: list[StaticFinding] = []
    python_files = [file for file in payload.files if file.path.casefold().endswith(".py")]
    local_roots = frozenset(
        file.path.split("/", maxsplit=1)[0].removesuffix(".py") for file in python_files
    )
    allowed_roots = _allowed_import_roots(spec)

    for file in python_files:
        try:
            tree = ast.parse(file.content, filename=file.path)
        except SyntaxError as error:
            findings.append(
                StaticFinding(
                    file_path=file.path,
                    code="syntax_error",
                    severity=FindingSeverity.ERROR,
                    message="Candidate Python does not parse",
                    line=error.lineno,
                )
            )
            continue
        visitor = _CandidateVisitor(
            file_path=file.path,
            allowed_import_roots=allowed_roots,
            local_import_roots=local_roots,
        )
        visitor.visit(tree)
        findings.extend(visitor.findings)

    return StaticAnalysisReport(
        candidate_sha256=candidate_payload_sha256(payload),
        allowed=not any(finding.severity is FindingSeverity.ERROR for finding in findings),
        scanned_python_files=len(python_files),
        findings=findings,
    )
