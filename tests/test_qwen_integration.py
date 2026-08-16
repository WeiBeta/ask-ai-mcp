"""Opt-in checks against the real loopback Qwen service."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from ask_ai_mcp.models import SourceExtractionCommand, SourceExtractionProfile
from ask_ai_mcp.qwen import QwenOpenAIClient
from ask_ai_mcp.qwen_source import LocalQwenSourceBackend
from ask_ai_mcp.source import StagedSource

pytestmark = pytest.mark.skipif(
    os.environ.get("ASK_AI_MCP_RUN_QWEN_INTEGRATION") != "1",
    reason="real local Qwen integration is opt-in",
)

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_real_qwen_health_and_fixed_model_identity() -> None:
    client = QwenOpenAIClient()

    assert client.health()
    assert client.model_is_available(client.models())


def test_real_qwen_extracts_synthetic_image_and_releases_model(tmp_path: Path) -> None:
    source = tmp_path / "one-pixel.png"
    source.write_bytes(_ONE_PIXEL_PNG)
    output = tmp_path / "output"
    output.mkdir()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    backend = LocalQwenSourceBackend()

    result = backend.extract(
        SourceExtractionCommand(
            source_files=[str(source)],
            profile=SourceExtractionProfile.VISUAL_STRUCTURE,
        ),
        [
            StagedSource(
                original_name=source.name,
                staged_name="source-0001.png",
                path=source,
                sha256=digest,
                size_bytes=source.stat().st_size,
                media_type="image/png",
            )
        ],
        output,
    )

    assert (output / "evidence.json").is_file()
    assert set(result.warnings) <= {
        "qwen_model_unloaded_after_task",
        "qwen_model_release_deferred_busy",
        "qwen_model_release_failed",
    }


def test_real_qwen_extracts_two_page_pdf_with_page_provenance(tmp_path: Path) -> None:
    source = tmp_path / "two-page-flow.pdf"
    pages = []
    for page_number, label in ((1, "INPUT -> VALIDATE"), (2, "VALIDATE -> OUTPUT")):
        image = Image.new("RGB", (800, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 180, 320, 300), outline="black", width=5)
        draw.rectangle((480, 180, 720, 300), outline="black", width=5)
        draw.line((320, 240, 480, 240), fill="black", width=5)
        draw.text((100, 220), label.split(" -> ")[0], fill="black")
        draw.text((510, 220), label.split(" -> ")[1], fill="black")
        draw.text((20, 20), f"PAGE {page_number}: {label}", fill="black")
        pages.append(image)
    pages[0].save(source, format="PDF", save_all=True, append_images=pages[1:])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "output"
    output.mkdir()
    backend = LocalQwenSourceBackend()

    backend.extract(
        SourceExtractionCommand(
            source_files=[str(source)],
            profile=SourceExtractionProfile.VISUAL_STRUCTURE,
            page_start=1,
            page_end=2,
        ),
        [
            StagedSource(
                original_name=source.name,
                staged_name="source-0001.pdf",
                path=source,
                sha256=digest,
                size_bytes=source.stat().st_size,
                media_type="application/pdf",
            )
        ],
        output,
    )

    bundle = (output / "evidence.json").read_text(encoding="utf-8")
    assert digest in bundle
    assert '"page":1' in bundle or '"page": 1' in bundle
    assert '"page":2' in bundle or '"page": 2' in bundle
    assert (output / "preprocessed" / "manifest.json").is_file()
