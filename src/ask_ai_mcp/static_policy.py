"""Defense-in-depth AST checks for untrusted Python candidates.

These checks are not a sandbox. Passing them only permits a candidate to be
staged for a later Windows Sandbox run.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import PurePosixPath

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

TEST_ONLY_IMPORT_ROOTS = frozenset({"tempfile"})

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
        if root in FORBIDDEN_MODULE_ROOTS and root not in self.allowed_import_roots:
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


def _is_test_file(path: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.name.startswith("test_") and candidate.suffix.casefold() == ".py"


def _has_discoverable_unittest(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_test_case = any(
            (
                isinstance(base, ast.Attribute)
                and isinstance(base.value, ast.Name)
                and base.value.id == "unittest"
                and base.attr == "TestCase"
            )
            or (isinstance(base, ast.Name) and base.id == "TestCase")
            for base in node.bases
        )
        if is_test_case and any(
            isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
            for item in node.body
        ):
            return True
    return False


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
    test_files = [file for file in python_files if _is_test_file(file.path)]
    discoverable_test_found = False

    if not test_files:
        findings.append(
            StaticFinding(
                file_path="test_*.py",
                code="missing_test_file",
                severity=FindingSeverity.ERROR,
                message=(
                    "Candidate must include a test_*.py file containing a discoverable "
                    "stdlib unittest.TestCase"
                ),
            )
        )

    if spec.entrypoint not in {file.path for file in python_files}:
        findings.append(
            StaticFinding(
                file_path=spec.entrypoint,
                code="missing_entrypoint",
                severity=FindingSeverity.ERROR,
                message="Candidate does not contain the declared Python entrypoint",
            )
        )

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
        file_allowed_roots = allowed_roots
        if _is_test_file(file.path):
            file_allowed_roots = frozenset((*allowed_roots, *TEST_ONLY_IMPORT_ROOTS))
            discoverable_test_found = discoverable_test_found or _has_discoverable_unittest(tree)

        visitor = _CandidateVisitor(
            file_path=file.path,
            allowed_import_roots=file_allowed_roots,
            local_import_roots=local_roots,
        )
        visitor.visit(tree)
        findings.extend(visitor.findings)

        if file.path == spec.entrypoint:
            run_functions = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "run"
            ]
            if not run_functions:
                findings.append(
                    StaticFinding(
                        file_path=file.path,
                        code="missing_run_function",
                        severity=FindingSeverity.ERROR,
                        message="Entrypoint must define run(request, input_dir, output_dir)",
                    )
                )
            else:
                arguments = run_functions[0].args
                positional = [*arguments.posonlyargs, *arguments.args]
                if (
                    [argument.arg for argument in positional]
                    != ["request", "input_dir", "output_dir"]
                    or arguments.vararg is not None
                    or arguments.kwarg is not None
                    or arguments.kwonlyargs
                ):
                    findings.append(
                        StaticFinding(
                            file_path=file.path,
                            code="invalid_run_signature",
                            severity=FindingSeverity.ERROR,
                            message=(
                                "Entrypoint run function must accept exactly request, "
                                "input_dir, output_dir"
                            ),
                            line=run_functions[0].lineno,
                        )
                    )

    if test_files and not discoverable_test_found:
        findings.append(
            StaticFinding(
                file_path=test_files[0].path,
                code="missing_unittest_case",
                severity=FindingSeverity.ERROR,
                message=(
                    "Test files must define a unittest.TestCase subclass with at least "
                    "one test_* method"
                ),
            )
        )

    return StaticAnalysisReport(
        candidate_sha256=candidate_payload_sha256(payload),
        allowed=not any(finding.severity is FindingSeverity.ERROR for finding in findings),
        scanned_python_files=len(python_files),
        findings=findings,
    )
