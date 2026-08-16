"""Source-faithful image and text extraction through the local Qwen runtime."""

from __future__ import annotations

import base64
import json
import math
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from ask_ai_mcp.document_preprocess import DocumentPreprocessor, PreparedVisual
from ask_ai_mcp.models import (
    CanonicalEvidenceBundle,
    EvidenceRecord,
    ModelProvider,
    SourceBackendStatus,
    SourceDetailLevel,
    SourceExtractionCommand,
    SourceExtractionProfile,
    VisualExtractionScope,
)
from ask_ai_mcp.qwen import QwenClientError, QwenOpenAIClient
from ask_ai_mcp.source import (
    SourceBackend,
    SourceBackendResult,
    SourceProcessingError,
    StagedSource,
    UnconfiguredQwenBackend,
)

SOURCE_PROVIDER_ENV = "ASK_AI_MCP_SOURCE_PROVIDER"
MAX_QWEN_IMAGE_BYTES = 256 * 1024 * 1024
MAX_QWEN_TEXT_CHARS = 400_000
# The current llama.cpp/Qwen runtime was observed to process only the final image
# reliably in a multi-image message. Keep one visual per request until that runtime
# behavior is explicitly revalidated.
MAX_QWEN_IMAGES_PER_REQUEST = 1
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv", ".json"})

_PROFILE_INSTRUCTIONS = {
    SourceExtractionProfile.DOCUMENT_EVIDENCE: (
        "Extract source-faithful text, tables, labels, and explicit relationships. "
        "Preserve exact wording where legible and do not infer final conclusions."
    ),
}

_VISUAL_INDEX_LIMITS = {
    SourceDetailLevel.COMPACT: {
        "regions": 12,
        "nodes": 40,
        "connectors": 60,
        "interfaces": 40,
        "decisions": 24,
    },
    SourceDetailLevel.STANDARD: {
        "regions": 32,
        "nodes": 120,
        "connectors": 180,
        "interfaces": 100,
        "decisions": 60,
    },
    SourceDetailLevel.DETAILED: {
        "regions": 64,
        "nodes": 240,
        "connectors": 360,
        "interfaces": 200,
        "decisions": 120,
    },
}


@dataclass(frozen=True, slots=True)
class _QwenVisual:
    source_index: int
    original_name: str
    source_sha256: str
    media_type: str
    path: Path
    source_kind: str
    location_number: int | None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class _QwenText:
    source_index: int
    original_name: str
    source_sha256: str
    text: str


def load_source_backend() -> SourceBackend:
    selected = os.environ.get(SOURCE_PROVIDER_ENV, "").strip().casefold()
    if not selected:
        return UnconfiguredQwenBackend()
    if selected == ModelProvider.LOCAL_QWEN.value:
        return LocalQwenSourceBackend()
    if selected == ModelProvider.OPENCODE.value:
        from ask_ai_mcp.opencode_source import OpenCodeQwenSourceBackend

        return OpenCodeQwenSourceBackend()
    raise SourceProcessingError(f"{SOURCE_PROVIDER_ENV} must be local_qwen or opencode")


class LocalQwenSourceBackend:
    """Convert staged image/text copies into canonical evidence JSON."""

    def __init__(
        self,
        client: QwenOpenAIClient | None = None,
        *,
        preprocessor: DocumentPreprocessor | None = None,
    ) -> None:
        self.client = client or QwenOpenAIClient()
        self.preprocessor = preprocessor or DocumentPreprocessor()

    def status(self) -> SourceBackendStatus:
        try:
            self.client.health()
            models = self.client.models()
            ready = self.client.model_is_available(models)
            detail = (
                "Local Qwen is ready for PDF/PPTX, image, and UTF-8 text evidence extraction."
                if ready
                else "Local Qwen is reachable but the configured model is unavailable."
            )
        except QwenClientError:
            ready = False
            detail = "Local Qwen health endpoints are unavailable."
        return SourceBackendStatus(
            provider=ModelProvider.LOCAL_QWEN,
            configured=True,
            ready=ready,
            model_id=self.client.config.model_id,
            runtime="openai_chat_completions_local",
            supported_profiles=[
                SourceExtractionProfile.DOCUMENT_EVIDENCE,
                SourceExtractionProfile.VISUAL_STRUCTURE,
            ],
            detail=detail,
        )

    def extract(
        self,
        command: SourceExtractionCommand,
        staged_sources: list[StagedSource],
        output_directory: Path,
    ) -> SourceBackendResult:
        if command.profile not in SourceExtractionProfile:
            raise SourceProcessingError("local Qwen media timeline extraction is not configured")
        warnings: list[str] = []
        try:
            visuals, text_sources = self._inputs(command, staged_sources, output_directory)
            if command.visual_scope is VisualExtractionScope.SELECTED_DETAILS:
                if not visuals:
                    raise SourceProcessingError("selected visual details require one visual")
                if len(visuals) != 1:
                    raise SourceProcessingError(
                        "selected visual details require exactly one visual; "
                        "select one image, page, or slide"
                    )
            batches = [
                visuals[index : index + MAX_QWEN_IMAGES_PER_REQUEST]
                for index in range(0, len(visuals), MAX_QWEN_IMAGES_PER_REQUEST)
            ]
            if not batches:
                batches = [[]]
            records: list[EvidenceRecord] = []
            bundle_warnings: list[str] = []
            for batch_number, batch in enumerate(batches, start=1):
                content = self._batch_content(
                    command,
                    batch,
                    text_sources if batch_number == 1 else [],
                )
                body = {
                    "model": self.client.config.model_id,
                    "messages": [{"role": "user", "content": content}],
                    "reasoning_effort": "none",
                    "max_tokens": self._max_tokens(command),
                    "temperature": 0.1,
                    "stream": False,
                }
                response = self.client.chat(body, vision=bool(batch))
                partial = self._parse_bundle(response, batch)
                self._validate_batch_locations(
                    command,
                    partial,
                    batch,
                    text_sources if batch_number == 1 else [],
                )
                self._validate_visual_contract(command, partial)
                records.extend(
                    record.model_copy(
                        update={"evidence_id": f"batch-{batch_number:04d}-{record.evidence_id}"}
                    )
                    for record in partial.records
                )
                bundle_warnings.extend(partial.warnings)
            bundle = CanonicalEvidenceBundle(
                profile=command.profile,
                records=records,
                warnings=bundle_warnings,
            )
            (output_directory / "evidence.json").write_text(
                bundle.model_dump_json(indent=2),
                encoding="utf-8",
            )
        finally:
            try:
                release = self.client.release_after_task()
                if release == "busy":
                    warnings.append("qwen_model_release_deferred_busy")
                elif release == "unloaded":
                    warnings.append("qwen_model_unloaded_after_task")
            except QwenClientError:
                warnings.append("qwen_model_release_failed")
        return SourceBackendResult(warnings=tuple(warnings))

    def _inputs(
        self,
        command: SourceExtractionCommand,
        staged_sources: list[StagedSource],
        output_directory: Path,
    ) -> tuple[list[_QwenVisual], list[_QwenText]]:
        prepared_by_index: dict[int, list[PreparedVisual]] = {}
        if any(source.path.suffix.casefold() in {".pdf", ".pptx"} for source in staged_sources):
            for visual in self.preprocessor.prepare(command, staged_sources, output_directory):
                prepared_by_index.setdefault(visual.source_index, []).append(visual)
        visuals: list[_QwenVisual] = []
        text_sources: list[_QwenText] = []
        total_text_chars = 0
        for index, source in enumerate(staged_sources, start=1):
            suffix = source.path.suffix.casefold()
            if suffix in _IMAGE_SUFFIXES:
                if source.size_bytes > MAX_QWEN_IMAGE_BYTES:
                    raise SourceProcessingError("one image exceeds the local Qwen input limit")
                visuals.append(
                    _QwenVisual(
                        source_index=index,
                        original_name=source.original_name,
                        source_sha256=source.sha256,
                        media_type=source.media_type,
                        path=source.path,
                        source_kind="whole_file",
                        location_number=None,
                    )
                )
                continue
            if suffix in {".pdf", ".pptx"}:
                visuals.extend(
                    _QwenVisual(
                        source_index=item.source_index,
                        original_name=item.original_name,
                        source_sha256=item.source_sha256,
                        media_type=item.media_type,
                        path=item.path,
                        source_kind=item.source_kind,
                        location_number=item.location_number,
                        width=item.width,
                        height=item.height,
                    )
                    for item in prepared_by_index.get(index, [])
                )
                continue
            if suffix in _TEXT_SUFFIXES:
                try:
                    text = source.path.read_text(encoding="utf-8-sig")
                except UnicodeError as error:
                    raise SourceProcessingError("text source is not valid UTF-8") from error
                total_text_chars += len(text)
                if total_text_chars > MAX_QWEN_TEXT_CHARS:
                    raise SourceProcessingError("text sources exceed the local Qwen context bound")
                text_sources.append(
                    _QwenText(
                        source_index=index,
                        original_name=source.original_name,
                        source_sha256=source.sha256,
                        text=text,
                    )
                )
                continue
            raise SourceProcessingError(
                "this local Qwen adapter accepts PDF/PPTX, PNG/JPEG/WebP/BMP, or UTF-8 text"
            )
        return visuals, text_sources

    def _batch_content(
        self,
        command: SourceExtractionCommand,
        visuals: list[_QwenVisual],
        text_sources: list[_QwenText],
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        source_map: list[dict[str, object]] = []
        for visual in visuals:
            media_type, encoded = self._encoded_visual(command, visual)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                }
            )
            entry: dict[str, object] = {
                "source_index": visual.source_index,
                "original_name": visual.original_name,
                "source_sha256": visual.source_sha256,
                "media_type": visual.media_type,
                "location_kind": visual.source_kind,
            }
            if visual.location_number is not None:
                entry[visual.source_kind] = visual.location_number
            if command.focus_region_xywh is not None:
                entry["focus_region_xywh"] = command.focus_region_xywh
            source_map.append(entry)
        for source in text_sources:
            source_map.append(
                {
                    "source_index": source.source_index,
                    "original_name": source.original_name,
                    "source_sha256": source.source_sha256,
                    "media_type": "text/plain; charset=utf-8",
                    "location_kind": "text",
                }
            )
        request = {
            "contract": "canonical_evidence_v1",
            "profile": command.profile.value,
            "detail_level": command.detail_level.value,
            "visual_scope": command.visual_scope.value,
            "focus_ids": command.focus_ids,
            "focus_region_xywh": command.focus_region_xywh,
            "page_range": [command.page_start, command.page_end],
            "language_hint": command.language_hint,
            "sources": source_map,
        }
        instruction, response_contract = self._response_contract(
            command,
            visuals,
            text_sources,
        )
        prompt_parts = [
            *(
                f"SOURCE {source.source_index} ({source.original_name}):\n{source.text}"
                for source in text_sources
            ),
            instruction,
            "Return exactly one JSON object matching the supplied schema. Every record must use "
            "the exact source_sha256. For rendered documents, use the exact page or slide from "
            "the source map. Use whole_file=true only for an original image or text source when "
            "no tighter coordinate is available. Do not include markdown fences, final advice, "
            "conclusions, or delivery prose.",
            f"Request JSON:\n{json.dumps(request, ensure_ascii=False)}",
            f"Required JSON shape:\n{json.dumps(response_contract, ensure_ascii=False)}",
        ]
        content.append({"type": "text", "text": "\n\n".join(prompt_parts)})
        return content

    @staticmethod
    def _encoded_visual(
        command: SourceExtractionCommand,
        visual: _QwenVisual,
    ) -> tuple[str, str]:
        if command.focus_region_xywh is None:
            encoded = base64.b64encode(visual.path.read_bytes()).decode("ascii")
            return visual.media_type, encoded
        try:
            with Image.open(visual.path) as image:
                image.load()
                x, y, width, height = command.focus_region_xywh
                left = math.floor(x * image.width)
                top = math.floor(y * image.height)
                right = min(image.width, max(left + 1, math.ceil((x + width) * image.width)))
                bottom = min(
                    image.height,
                    max(top + 1, math.ceil((y + height) * image.height)),
                )
                crop = image.crop((left, top, right, bottom))
                if crop.mode not in {"RGB", "RGBA", "L"}:
                    crop = crop.convert("RGB")
                buffer = BytesIO()
                crop.save(buffer, format="PNG")
        except OSError as error:
            raise SourceProcessingError("local Qwen focus region could not be rendered") from error
        return "image/png", base64.b64encode(buffer.getvalue()).decode("ascii")

    @staticmethod
    def _response_contract(
        command: SourceExtractionCommand,
        visuals: list[_QwenVisual],
        text_sources: list[_QwenText],
    ) -> tuple[str, dict[str, Any]]:
        primary = visuals[0] if visuals else text_sources[0]
        if command.focus_region_xywh is not None:
            location = {"region_xywh": command.focus_region_xywh}
            if isinstance(primary, _QwenVisual) and primary.location_number is not None:
                location[primary.source_kind] = primary.location_number
        elif isinstance(primary, _QwenVisual) and primary.location_number is not None:
            location = {primary.source_kind: primary.location_number}
        else:
            location = {"whole_file": True}
        common = {
            "source_sha256": primary.source_sha256,
            "kind": "diagram",
            "location": location,
            "extraction_method": "qwen-visual-structure",
            "warnings": [],
        }
        if command.profile is SourceExtractionProfile.DOCUMENT_EVIDENCE:
            return (
                _PROFILE_INSTRUCTIONS[command.profile],
                CanonicalEvidenceBundle.model_json_schema(),
            )
        if command.visual_scope is VisualExtractionScope.SELECTED_DETAILS:
            records = [
                {
                    "evidence_id": f"selected-detail-{index}",
                    **common,
                    "data": {
                        "focus_id": focus_id,
                        "title": "exact visible title",
                        "method": "exact visible method or empty string",
                        "sections": [
                            {"name": "exact visible section name", "items": ["exact item"]}
                        ],
                        "relationships": ["explicit visible relationship"],
                    },
                }
                for index, focus_id in enumerate(command.focus_ids, start=1)
            ]
            return (
                "Extract only the explicitly selected focus_ids. Return exactly one record per "
                "focus_id in the requested order and no records for unselected objects. Preserve "
                "visible wording; use empty arrays instead of expanding unrelated diagram content.",
                {
                    "contract": "canonical_evidence_v1",
                    "profile": "visual_structure",
                    "records": records,
                    "warnings": [],
                },
            )
        limits = _VISUAL_INDEX_LIMITS[command.detail_level]
        if command.visual_scope is VisualExtractionScope.TOPOLOGY:
            return (
                "Build one bounded topology record, not a full transcription. Capture the title, "
                "regions or swimlanes, decisions, diagram nodes, and explicit connectors, in that "
                "order. Do not return an interface catalog or copy caller, request-field, "
                "response-field, or explanatory paragraphs. Deduplicate labels and obey these "
                "array maxima: "
                f"{json.dumps(limits, separators=(',', ':'))}.",
                {
                    "contract": "canonical_evidence_v1",
                    "profile": "visual_structure",
                    "records": [
                        {
                            "evidence_id": "visual-topology",
                            **common,
                            "data": {
                                "title": "exact visible title",
                                "regions": [{"id": "region-id", "label": "label", "kind": "kind"}],
                                "decisions": [{"id": "decision-id", "label": "label"}],
                                "nodes": [
                                    {
                                        "id": "node-id",
                                        "label": "label",
                                        "region_id": "region-id or empty string",
                                        "type": "node type",
                                    }
                                ],
                                "connectors": [
                                    {
                                        "from_id": "node-id",
                                        "to_id": "node-id",
                                        "label": "label",
                                    }
                                ],
                            },
                        }
                    ],
                    "warnings": [],
                },
            )
        index_limits = {key: limits[key] for key in ("regions", "interfaces", "decisions")}
        return (
            "Build one bounded visual index record, not a full transcription. Capture only the "
            "title, regions or swimlanes, a compact interface index, and decisions. Interface "
            "names and methods are high-priority verbatim OCR: never infer, normalize, or "
            "paraphrase them. Each interface may contain only id, name, and method; never return "
            "nodes, connectors, caller, request-field, response-field, or explanatory paragraphs. "
            "Deduplicate labels and obey these array maxima: "
            f"{json.dumps(index_limits, separators=(',', ':'))}.",
            {
                "contract": "canonical_evidence_v1",
                "profile": "visual_structure",
                "records": [
                    {
                        "evidence_id": "visual-index",
                        **common,
                        "data": {
                            "title": "exact visible title",
                            "regions": [{"id": "region-id", "label": "label", "kind": "kind"}],
                            "interfaces": [{"id": "01", "name": "name", "method": "GET"}],
                            "decisions": [{"id": "decision-id", "label": "label"}],
                        },
                    }
                ],
                "warnings": [],
            },
        )

    @staticmethod
    def _validate_batch_locations(
        command: SourceExtractionCommand,
        bundle: CanonicalEvidenceBundle,
        visuals: list[_QwenVisual],
        text_sources: list[_QwenText],
    ) -> None:
        if not visuals and not text_sources:
            return
        text_hashes = {source.source_sha256 for source in text_sources}
        permitted: dict[str, set[tuple[str, int | None]]] = {}
        for visual in visuals:
            permitted.setdefault(visual.source_sha256, set()).add(
                (visual.source_kind, visual.location_number)
            )
        for record in bundle.records:
            if record.source_sha256 in text_hashes:
                continue
            allowed = permitted.get(record.source_sha256)
            if not allowed:
                raise SourceProcessingError(
                    "local Qwen evidence refers to a source outside its batch"
                )
            if command.focus_region_xywh is not None:
                if record.location.region_xywh != command.focus_region_xywh:
                    raise SourceProcessingError(
                        "local Qwen evidence returned an incorrect focus region"
                    )
                if ("whole_file", None) in allowed:
                    continue
            coordinates = {
                ("page", record.location.page),
                ("slide", record.location.slide),
                ("whole_file", None) if record.location.whole_file else ("invalid", None),
            }
            if not allowed.intersection(coordinates):
                raise SourceProcessingError(
                    "local Qwen evidence returned an incorrect visual location"
                )

    @staticmethod
    def _validate_visual_contract(
        command: SourceExtractionCommand,
        bundle: CanonicalEvidenceBundle,
    ) -> None:
        if command.profile is not SourceExtractionProfile.VISUAL_STRUCTURE:
            return
        if command.visual_scope is VisualExtractionScope.SELECTED_DETAILS:
            LocalQwenSourceBackend._validate_selected_details(command, bundle)
            return
        if len(bundle.records) != 1:
            raise SourceProcessingError("visual structure index must contain exactly one record")
        data = bundle.records[0].data
        if command.visual_scope is VisualExtractionScope.TOPOLOGY:
            required = {"title", "regions", "nodes", "connectors", "decisions"}
            shape_names = ("regions", "nodes", "connectors", "decisions")
        else:
            required = {"title", "regions", "interfaces", "decisions"}
            shape_names = ("regions", "interfaces", "decisions")
        if set(data) != required or not isinstance(data["title"], str):
            raise SourceProcessingError("visual structure index has an invalid bounded shape")
        limits = _VISUAL_INDEX_LIMITS[command.detail_level]
        shapes = {
            "regions": ({"id", "label", "kind"}, {"id", "label"}),
            "nodes": ({"id", "label", "region_id", "type"}, {"id", "label"}),
            "connectors": ({"from_id", "to_id", "label"}, {"from_id", "to_id"}),
            "interfaces": ({"id", "name", "method"}, {"id", "name", "method"}),
            "decisions": ({"id", "label"}, {"id", "label"}),
        }
        for field in shape_names:
            allowed, required_item = shapes[field]
            items = data[field]
            if not isinstance(items, list) or len(items) > limits[field]:
                raise SourceProcessingError("visual structure index exceeds a bounded array limit")
            for item in items:
                if not isinstance(item, dict):
                    raise SourceProcessingError("visual structure index contains a non-object item")
                if not required_item.issubset(item) or not set(item).issubset(allowed):
                    raise SourceProcessingError("visual structure index contains unbounded fields")
                if not all(isinstance(value, str) for value in item.values()):
                    raise SourceProcessingError("visual structure index labels must be strings")
        interfaces = data.get("interfaces")
        if isinstance(interfaces, list):
            identifiers = [item["id"].casefold() for item in interfaces]
            if len(identifiers) != len(set(identifiers)):
                raise SourceProcessingError(
                    "visual structure index contains duplicate interface ids"
                )
            names = [item["name"].casefold() for item in interfaces]
            if len(names) != len(set(names)):
                bundle.warnings.append("duplicate_interface_names_require_selected_details_review")

    @staticmethod
    def _validate_selected_details(
        command: SourceExtractionCommand,
        bundle: CanonicalEvidenceBundle,
    ) -> None:
        if len(bundle.records) != len(command.focus_ids):
            raise SourceProcessingError(
                "selected visual details must match the requested focus_ids"
            )
        expected = {value.casefold() for value in command.focus_ids}
        expected_order = [value.casefold() for value in command.focus_ids]
        seen: set[str] = set()
        returned_order: list[str] = []
        required = {"focus_id", "title", "method", "sections", "relationships"}
        for record in bundle.records:
            data = record.data
            if set(data) != required:
                raise SourceProcessingError("selected visual detail has an invalid bounded shape")
            focus_id = data["focus_id"]
            if not isinstance(focus_id, str) or focus_id.casefold() not in expected:
                raise SourceProcessingError(
                    "selected visual detail returned an unrequested focus_id"
                )
            seen.add(focus_id.casefold())
            returned_order.append(focus_id.casefold())
            if not isinstance(data["title"], str) or not isinstance(data["method"], str):
                raise SourceProcessingError("selected visual detail labels must be strings")
            sections = data["sections"]
            relationships = data["relationships"]
            if not isinstance(sections, list) or len(sections) > 32:
                raise SourceProcessingError("selected visual detail has too many sections")
            if (
                not isinstance(relationships, list)
                or len(relationships) > 32
                or not all(isinstance(value, str) for value in relationships)
            ):
                raise SourceProcessingError("selected visual detail has invalid relationships")
            for section in sections:
                if not isinstance(section, dict) or set(section) != {"name", "items"}:
                    raise SourceProcessingError("selected visual detail has an invalid section")
                if not isinstance(section["name"], str):
                    raise SourceProcessingError("selected visual detail section name must be text")
                items = section["items"]
                if (
                    not isinstance(items, list)
                    or len(items) > 64
                    or not all(isinstance(value, str) for value in items)
                ):
                    raise SourceProcessingError("selected visual detail section items are invalid")
        if seen != expected:
            raise SourceProcessingError("selected visual details omitted a requested focus_id")
        if returned_order != expected_order:
            raise SourceProcessingError(
                "selected visual details did not preserve the requested focus_id order"
            )

    @staticmethod
    def _parse_bundle(
        response: dict[str, Any],
        visuals: list[_QwenVisual] | None = None,
    ) -> CanonicalEvidenceBundle:
        try:
            choice = response["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("truncated")
            raw = choice["message"]["content"]
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("empty")
            data = json.loads(LocalQwenSourceBackend._unwrap_json_fence(raw))
            LocalQwenSourceBackend._normalize_pixel_regions(data, visuals or [])
            return CanonicalEvidenceBundle.model_validate(data)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise SourceProcessingError("local Qwen returned invalid canonical evidence") from error

    @staticmethod
    def _unwrap_json_fence(raw: str) -> str:
        stripped = raw.strip()
        for prefix in ("```json\n", "```JSON\n", "```\n"):
            if stripped.startswith(prefix) and stripped.endswith("\n```"):
                return stripped[len(prefix) : -4].strip()
        return stripped

    @staticmethod
    def _normalize_pixel_regions(data: object, visuals: list[_QwenVisual]) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            return
        lookup = {
            (visual.source_sha256, visual.source_kind, visual.location_number): visual
            for visual in visuals
            if visual.width and visual.height and visual.location_number is not None
        }
        for record in data["records"]:
            if not isinstance(record, dict) or not isinstance(record.get("location"), dict):
                continue
            location = record["location"]
            region = location.get("region_xywh")
            if (
                not isinstance(region, list)
                or len(region) != 4
                or not all(isinstance(value, int | float) for value in region)
                or all(0 <= value <= 1 for value in region)
            ):
                continue
            kind = "page" if location.get("page") is not None else "slide"
            number = location.get(kind)
            visual = lookup.get((record.get("source_sha256"), kind, number))
            if visual is None or any(value < 0 for value in region):
                continue
            x, y, width, height = region
            if x + width > visual.width or y + height > visual.height:
                continue
            location["region_xywh"] = [
                x / visual.width,
                y / visual.height,
                width / visual.width,
                height / visual.height,
            ]
            warnings = record.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.append("region_xywh_normalized_from_pixels")

    @staticmethod
    def _max_tokens(command: SourceExtractionCommand) -> int:
        if command.profile is SourceExtractionProfile.DOCUMENT_EVIDENCE:
            return {
                SourceDetailLevel.COMPACT: 4_096,
                SourceDetailLevel.STANDARD: 8_192,
                SourceDetailLevel.DETAILED: 16_384,
            }[command.detail_level]
        if command.visual_scope is VisualExtractionScope.STRUCTURE_INDEX:
            return 4_096
        if command.visual_scope is VisualExtractionScope.TOPOLOGY:
            return {
                SourceDetailLevel.COMPACT: 4_096,
                SourceDetailLevel.STANDARD: 8_192,
                SourceDetailLevel.DETAILED: 8_192,
            }[command.detail_level]
        return {
            SourceDetailLevel.COMPACT: 4_096,
            SourceDetailLevel.STANDARD: 8_192,
            SourceDetailLevel.DETAILED: 12_288,
        }[command.detail_level]
