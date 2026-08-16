"""Tests for the no-retry local Qwen transport and bounded adapters."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ask_ai_mcp.document_preprocess import PreparedVisual
from ask_ai_mcp.models import (
    CandidateRepairFeedback,
    DeepSeekModel,
    ModelProvider,
    RepairKind,
    SourceExtractionCommand,
    SourceExtractionProfile,
    ToolBuildSpec,
    ToolCandidateResult,
    ToolCategory,
)
from ask_ai_mcp.qwen import (
    QwenClientError,
    QwenOpenAIClient,
    QwenRequestStillProcessing,
    QwenRuntimeConfig,
)
from ask_ai_mcp.qwen_source import LocalQwenSourceBackend, _QwenVisual
from ask_ai_mcp.qwen_toolsmith import LocalQwenToolsmithClient
from ask_ai_mcp.source import StagedSource
from ask_ai_mcp.usage import UsageStore


def runtime(*, unload: bool = False) -> QwenRuntimeConfig:
    return QwenRuntimeConfig(unload_after_task=unload)


def candidate_response() -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "synthetic candidate",
                            "files": [
                                {"path": "tool.py", "content": "def run(a, b, c): return {}"},
                                {
                                    "path": "test_tool.py",
                                    "content": (
                                        "import unittest\nclass T(unittest.TestCase):\n"
                                        "    def test_x(self): self.assertTrue(True)"
                                    ),
                                },
                            ],
                            "risks": [],
                        }
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


def make_spec() -> ToolBuildSpec:
    return ToolBuildSpec(
        name="synthetic_helper",
        category=ToolCategory.TEST_UTILITY,
        purpose="Build one deterministic synthetic helper.",
        input_contract="Accept only synthetic fixture input values.",
        output_contract="Return one deterministic synthetic JSON object.",
        acceptance_tests=["A stdlib unittest validates the result."],
        model=DeepSeekModel.FLASH,
    )


def test_runtime_rejects_remote_urls_and_short_vision_timeout() -> None:
    with pytest.raises(QwenClientError, match="loopback"):
        QwenRuntimeConfig(base_url="https://example.com/v1")
    with pytest.raises(QwenClientError, match="3600"):
        QwenRuntimeConfig(vision_timeout_seconds=3_599)


def test_timed_out_post_checks_slots_once_and_never_resubmits() -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "POST":
            posts += 1
            raise httpx.ReadTimeout("synthetic", request=request)
        assert request.url.path == "/slots"
        assert request.url.params.get("model") == runtime().model_id
        return httpx.Response(200, json=[{"id": 0, "is_processing": True}])

    client = QwenOpenAIClient(runtime(), transport=httpx.MockTransport(handler))

    with pytest.raises(QwenRequestStillProcessing, match="do not resubmit"):
        client.chat({"model": runtime().model_id, "messages": []}, vision=True)
    assert posts == 1


def test_idle_router_model_is_unloaded_without_stopping_service() -> None:
    requests: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.url.params.get("model")))
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": runtime().model_id,
                            "status": {"value": "loaded"},
                        }
                    ]
                },
            )
        if request.url.path == "/slots":
            return httpx.Response(200, json=[{"id": 0, "is_processing": False}])
        assert json.loads(request.content)["model"] == runtime().model_id
        return httpx.Response(200, json={"success": True})

    client = QwenOpenAIClient(
        runtime(unload=True),
        transport=httpx.MockTransport(handler),
    )

    assert client.release_after_task() == "unloaded"
    assert requests == [
        ("GET", "/v1/models", None),
        ("GET", "/slots", runtime().model_id),
        ("POST", "/models/unload", None),
    ]


def test_already_unloaded_router_model_is_not_autoloaded_by_slots_check() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": runtime().model_id,
                        "status": {"value": "unloaded"},
                    }
                ]
            },
        )

    client = QwenOpenAIClient(
        runtime(unload=True),
        transport=httpx.MockTransport(handler),
    )

    assert client.release_after_task() == "unloaded"
    assert requests == ["/v1/models"]


def test_local_toolsmith_uses_fixed_model_and_records_zero_cost(tmp_path: Path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=candidate_response())

    client = LocalQwenToolsmithClient(
        qwen_client=QwenOpenAIClient(runtime(), transport=httpx.MockTransport(handler)),
        usage_store=UsageStore(tmp_path / "usage.db"),
    )

    result = client.build_candidate(make_spec(), client_name="codex_desktop")

    assert result.provider is ModelProvider.LOCAL_QWEN
    assert result.provider_model_id == "qwen3.8-27b-local"
    assert result.provider_runtime == "openai_chat_completions_local"
    assert seen[0]["model"] == "qwen3.8-27b-local"
    assert seen[0]["reasoning_effort"] == "none"
    assert seen[0]["max_tokens"] == 32_768
    assert result.thinking_enabled is False


def test_local_toolsmith_static_repair_uses_small_patch_schema(tmp_path: Path) -> None:
    from ask_ai_mcp.hashing import candidate_payload_sha256

    initial_client = LocalQwenToolsmithClient(
        qwen_client=QwenOpenAIClient(
            runtime(),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json=candidate_response())
            ),
        ),
        usage_store=UsageStore(tmp_path / "initial.db"),
    )
    previous_payload = initial_client._parse_candidate(candidate_response())
    previous_hash = candidate_payload_sha256(previous_payload)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "none"
        assert "base_candidate_sha256" not in body["messages"][0]["content"]
        assert '"edits"' in body["messages"][0]["content"]
        patch = {
            "base_candidate_sha256": "ignored-model-value",
            "summary": "Replace one exact function signature.",
            "edits": [
                {
                    "file_path": "tool.py",
                    "old_text": "no-op",
                    "new_text": "no-op",
                },
                {
                    "file_path": "tool.py",
                    "old_text": "def run(a, b, c):",
                    "new_text": "def run(request, input_dir, output_dir):",
                },
            ],
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(patch)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    client = LocalQwenToolsmithClient(
        qwen_client=QwenOpenAIClient(runtime(), transport=httpx.MockTransport(handler)),
        usage_store=UsageStore(tmp_path / "repair.db"),
    )
    result = client.repair_candidate(
        make_spec(),
        ToolCandidateResult(
            candidate_sha256=previous_hash,
            model=DeepSeekModel.FLASH,
            provider=ModelProvider.LOCAL_QWEN,
            thinking_enabled=True,
            payload=previous_payload,
        ),
        CandidateRepairFeedback(
            repair_round=1,
            repair_kind=RepairKind.STATIC_POLICY,
            reason_codes=["invalid_run_signature"],
        ),
        client_name="codex_desktop",
        thinking_enabled=False,
        max_output_tokens=4_096,
    )

    assert "def run(request, input_dir, output_dir):" in result.payload.files[0].content


def test_source_request_places_image_before_instruction_and_writes_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"synthetic-png")
    output = tmp_path / "output"
    output.mkdir()
    staged = StagedSource(
        original_name="diagram.png",
        staged_name="source-0001.png",
        path=source,
        sha256="a" * 64,
        size_bytes=source.stat().st_size,
        media_type="image/png",
    )
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        evidence = {
            "contract": "canonical_evidence_v1",
            "profile": "visual_structure",
            "records": [
                {
                    "evidence_id": "record-1",
                    "source_sha256": "a" * 64,
                    "kind": "diagram",
                    "location": {"whole_file": True},
                    "data": {"title": "Synthetic"},
                    "extraction_method": "local-qwen-test",
                }
            ],
            "warnings": [],
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(evidence)}}]
            },
        )

    backend = LocalQwenSourceBackend(
        QwenOpenAIClient(runtime(), transport=httpx.MockTransport(handler))
    )
    result = backend.extract(
        SourceExtractionCommand(
            source_files=[str(source)],
            profile=SourceExtractionProfile.VISUAL_STRUCTURE,
        ),
        [staged],
        output,
    )

    content = seen[0]["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[-1]["type"] == "text"
    assert seen[0]["reasoning_effort"] == "none"
    assert result.warnings == ()
    assert json.loads((output / "evidence.json").read_text(encoding="utf-8"))["records"]


def test_document_visuals_are_batched_and_keep_original_page_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"staged-copy")
    staged_source = StagedSource(
        original_name="report.pdf",
        staged_name="source-0001.pdf",
        path=source,
        sha256="b" * 64,
        size_bytes=source.stat().st_size,
        media_type="application/pdf",
    )
    visual_paths = []
    for page in range(1, 6):
        path = tmp_path / f"page-{page:04d}.png"
        path.write_bytes(f"page-{page}".encode())
        visual_paths.append(path)

    class FakePreprocessor:
        def prepare(self, _command, _sources, _output):
            return [
                PreparedVisual(
                    source_index=1,
                    original_name="report.pdf",
                    source_sha256="b" * 64,
                    source_kind="page",
                    location_number=page,
                    path=path,
                    sha256="c" * 64,
                    width=100,
                    height=100,
                )
                for page, path in enumerate(visual_paths, start=1)
            ]

    calls = 0
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        seen.append(body)
        page = calls
        evidence = {
            "contract": "canonical_evidence_v1",
            "profile": "document_evidence",
            "records": [
                {
                    "evidence_id": "record-1",
                    "source_sha256": "b" * 64,
                    "kind": "text",
                    "location": {"page": page},
                    "verbatim_text": f"page {page}",
                    "extraction_method": "local-qwen-test",
                }
            ],
            "warnings": [],
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(evidence)}}]
            },
        )

    output = tmp_path / "output"
    output.mkdir()
    backend = LocalQwenSourceBackend(
        QwenOpenAIClient(runtime(), transport=httpx.MockTransport(handler)),
        preprocessor=FakePreprocessor(),
    )
    backend.extract(
        SourceExtractionCommand(
            source_files=[str(source)],
            profile=SourceExtractionProfile.DOCUMENT_EVIDENCE,
        ),
        [staged_source],
        output,
    )

    assert calls == 5
    assert [len(item["messages"][0]["content"]) - 1 for item in seen] == [1, 1, 1, 1, 1]
    bundle = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    assert [record["evidence_id"] for record in bundle["records"]] == [
        "batch-0001-record-1",
        "batch-0002-record-1",
        "batch-0003-record-1",
        "batch-0004-record-1",
        "batch-0005-record-1",
    ]
    assert {record["source_sha256"] for record in bundle["records"]} == {"b" * 64}


def test_document_pixel_regions_are_deterministically_normalized() -> None:
    response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "profile": "visual_structure",
                            "records": [
                                {
                                    "evidence_id": "region-1",
                                    "source_sha256": "d" * 64,
                                    "kind": "diagram",
                                    "location": {
                                        "page": 2,
                                        "region_xywh": [100, 200, 400, 300],
                                    },
                                    "data": {"label": "Synthetic"},
                                    "extraction_method": "local-qwen-test",
                                }
                            ],
                        }
                    )
                },
            }
        ]
    }
    visual = LocalQwenSourceBackend._parse_bundle(
        response,
        [
            _QwenVisual(
                source_index=1,
                original_name="report.pdf",
                source_sha256="d" * 64,
                media_type="image/png",
                path=Path("page.png"),
                source_kind="page",
                location_number=2,
                width=1000,
                height=1000,
            )
        ],
    )

    record = visual.records[0]
    assert record.location.region_xywh == [0.1, 0.2, 0.4, 0.3]
    assert record.warnings == ["region_xywh_normalized_from_pixels"]
