"""Tests for the no-retry local Qwen transport and bounded adapters."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from ask_ai_mcp.document_preprocess import PreparedVisual
from ask_ai_mcp.models import (
    CandidateRepairFeedback,
    CanonicalEvidenceBundle,
    DeepSeekModel,
    ModelProvider,
    RepairKind,
    SourceExtractionCommand,
    SourceExtractionProfile,
    ToolBuildSpec,
    ToolCandidateResult,
    ToolCategory,
    VisualExtractionScope,
)
from ask_ai_mcp.qwen import (
    QwenClientError,
    QwenOpenAIClient,
    QwenRequestStillProcessing,
    QwenRuntimeConfig,
)
from ask_ai_mcp.qwen_source import LocalQwenSourceBackend, _QwenVisual
from ask_ai_mcp.qwen_toolsmith import LocalQwenToolsmithClient
from ask_ai_mcp.source import SourceProcessingError, StagedSource
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
                    "data": {
                        "title": "Synthetic",
                        "regions": [],
                        "interfaces": [{"id": "01", "name": "Lookup", "method": "GET"}],
                        "decisions": [],
                    },
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
    assert seen[0]["max_tokens"] == 4_096
    prompt = content[-1]["text"]
    assert "Each interface may contain only id, name, and method" in prompt
    assert "request-field" in prompt
    assert result.warnings == ()
    assert json.loads((output / "evidence.json").read_text(encoding="utf-8"))["records"]


def test_visual_topology_has_a_separate_bounded_shape() -> None:
    command = SourceExtractionCommand(
        source_files=[r"C:\Source\diagram.png"],
        profile=SourceExtractionProfile.VISUAL_STRUCTURE,
        visual_scope=VisualExtractionScope.TOPOLOGY,
    )
    visual = _QwenVisual(
        source_index=1,
        original_name="diagram.png",
        source_sha256="a" * 64,
        media_type="image/png",
        path=Path("diagram.png"),
        source_kind="whole_file",
        location_number=None,
    )

    instruction, contract = LocalQwenSourceBackend._response_contract(command, [visual], [])

    assert "Do not return an interface catalog" in instruction
    assert set(contract["records"][0]["data"]) == {
        "title",
        "regions",
        "decisions",
        "nodes",
        "connectors",
    }
    assert LocalQwenSourceBackend._max_tokens(command) == 8_192


def test_duplicate_interface_names_are_flagged_for_selected_review() -> None:
    command = SourceExtractionCommand(
        source_files=[r"C:\Source\diagram.png"],
        profile=SourceExtractionProfile.VISUAL_STRUCTURE,
    )
    bundle = CanonicalEvidenceBundle.model_validate(
        {
            "profile": "visual_structure",
            "records": [
                {
                    "evidence_id": "visual-index",
                    "source_sha256": "a" * 64,
                    "kind": "diagram",
                    "location": {"whole_file": True},
                    "data": {
                        "title": "Synthetic",
                        "regions": [],
                        "interfaces": [
                            {"id": "10", "name": "Repeated", "method": "POST"},
                            {"id": "12", "name": "Repeated", "method": "POST"},
                        ],
                        "decisions": [],
                    },
                    "extraction_method": "local-qwen-test",
                }
            ],
        }
    )

    LocalQwenSourceBackend._validate_visual_contract(command, bundle)

    assert bundle.warnings == ["duplicate_interface_names_require_selected_details_review"]


def test_selected_visual_details_are_bounded_to_explicit_focus_ids(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(source)
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
        records = []
        for index, focus_id in enumerate(("08", "17"), start=1):
            records.append(
                {
                    "evidence_id": f"detail-{index}",
                    "source_sha256": "a" * 64,
                    "kind": "diagram",
                    "location": {"region_xywh": [0.5, 0.1, 0.4, 0.3]},
                    "data": {
                        "focus_id": focus_id,
                        "title": f"Interface {focus_id}",
                        "method": "POST",
                        "sections": [{"name": "request", "items": ["field_a"]}],
                        "relationships": [],
                    },
                    "extraction_method": "local-qwen-test",
                }
            )
        evidence = {
            "contract": "canonical_evidence_v1",
            "profile": "visual_structure",
            "records": records,
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
    backend.extract(
        SourceExtractionCommand(
            source_files=[str(source)],
            profile=SourceExtractionProfile.VISUAL_STRUCTURE,
            visual_scope=VisualExtractionScope.SELECTED_DETAILS,
            focus_ids=["08", "17"],
            focus_region_xywh=[0.5, 0.1, 0.4, 0.3],
        ),
        [staged],
        output,
    )

    prompt = seen[0]["messages"][0]["content"][-1]["text"]
    assert '"focus_ids": ["08", "17"]' in prompt
    assert '"focus_region_xywh": [0.5, 0.1, 0.4, 0.3]' in prompt
    assert "only the explicitly selected focus_ids" in prompt
    encoded_url = seen[0]["messages"][0]["content"][0]["image_url"]["url"]
    assert encoded_url.startswith("data:image/png;base64,")
    with Image.open(BytesIO(base64.b64decode(encoded_url.split(",", 1)[1]))) as crop:
        assert crop.size == (40, 30)
    records = json.loads((output / "evidence.json").read_text(encoding="utf-8"))["records"]
    assert [record["data"]["focus_id"] for record in records] == ["08", "17"]


def test_selected_visual_details_reject_multiple_visuals(tmp_path: Path) -> None:
    staged_sources = []
    for index in range(2):
        source = tmp_path / f"source-{index}.png"
        Image.new("RGB", (10, 10), "white").save(source)
        staged_sources.append(
            StagedSource(
                original_name=source.name,
                staged_name=source.name,
                path=source,
                sha256=str(index + 1) * 64,
                size_bytes=source.stat().st_size,
                media_type="image/png",
            )
        )
    backend = LocalQwenSourceBackend(QwenOpenAIClient(runtime()))

    with pytest.raises(SourceProcessingError, match="exactly one visual"):
        backend.extract(
            SourceExtractionCommand(
                source_files=[str(source.path) for source in staged_sources],
                profile=SourceExtractionProfile.VISUAL_STRUCTURE,
                visual_scope=VisualExtractionScope.SELECTED_DETAILS,
                focus_ids=["08"],
                focus_region_xywh=[0.0, 0.0, 1.0, 1.0],
            ),
            staged_sources,
            tmp_path / "output",
        )


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
                    "content": "```json\n"
                    + json.dumps(
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
                    + "\n```"
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
