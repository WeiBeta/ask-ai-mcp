"""Tests for deterministic, source-bound document rendering."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from ask_ai_mcp.document_preprocess import DocumentPreprocessor
from ask_ai_mcp.models import SourceExtractionCommand, SourceExtractionProfile
from ask_ai_mcp.source import SourceProcessingError, StagedSource


def staged(path: Path, *, sha256: str = "a" * 64) -> StagedSource:
    return StagedSource(
        original_name=path.name,
        staged_name=path.name,
        path=path,
        sha256=sha256,
        size_bytes=path.stat().st_size,
        media_type="application/pdf" if path.suffix == ".pdf" else "application/vnd.ms-powerpoint",
    )


def command(path: Path, *, start: int | None = None, end: int | None = None):
    return SourceExtractionCommand(
        source_files=[str(path)],
        profile=SourceExtractionProfile.DOCUMENT_EVIDENCE,
        page_start=start,
        page_end=end,
    )


def make_pdf(path: Path, pages: int = 3) -> None:
    images = [Image.new("RGB", (80, 60), (index * 30, 20, 40)) for index in range(pages)]
    images[0].save(path, format="PDF", save_all=True, append_images=images[1:])


def make_pptx(
    path: Path,
    *,
    slides: int = 2,
    extra: dict[str, bytes] | None = None,
) -> None:
    ids = "".join(f'<p:sldId id="{256 + number}"/>' for number in range(slides))
    presentation = (
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        f"<p:sldIdLst>{ids}</p:sldIdLst></p:presentation>"
    ).encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)


def test_pdf_page_selection_and_manifest_are_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    make_pdf(source)
    preprocessor = DocumentPreprocessor()
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()

    first = preprocessor.prepare(command(source, start=2, end=3), [staged(source)], first_root)
    second = preprocessor.prepare(command(source, start=2, end=3), [staged(source)], second_root)

    assert [item.location_number for item in first] == [2, 3]
    assert [item.sha256 for item in first] == [item.sha256 for item in second]
    first_manifest = json.loads(
        (first_root / "preprocessed" / "manifest.json").read_text(encoding="utf-8")
    )
    second_manifest = json.loads(
        (second_root / "preprocessed" / "manifest.json").read_text(encoding="utf-8")
    )
    assert first_manifest == second_manifest
    assert first_manifest["visuals"][0]["path"].endswith("page-0002.png")


def test_large_document_requires_explicit_bounded_range(tmp_path: Path) -> None:
    source = tmp_path / "large.pdf"
    make_pdf(source, pages=33)

    with pytest.raises(SourceProcessingError, match="provide page_start"):
        DocumentPreprocessor().prepare(command(source), [staged(source)], tmp_path / "output")


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"../escape.bin": b"x"}, "unsafe entry"),
        ({"ppt/vbaProject.bin": b"x"}, "macro-enabled"),
        (
            {
                "ppt/_rels/presentation.xml.rels": (
                    b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                    b'relationships"><Relationship Id="r1" Target="https://example.com" '
                    b'TargetMode="External"/></Relationships>'
                )
            },
            "external relationships",
        ),
    ],
)
def test_pptx_preflight_rejects_active_or_external_content(
    tmp_path: Path,
    extra: dict[str, bytes],
    message: str,
) -> None:
    source = tmp_path / "unsafe.pptx"
    make_pptx(source, extra=extra)

    with pytest.raises(SourceProcessingError, match=message):
        DocumentPreprocessor(pptx_exporter=lambda *_args: None).prepare(
            command(source),
            [staged(source)],
            tmp_path / "output",
        )


def test_pptx_uses_fixed_selection_and_validates_exported_images(tmp_path: Path) -> None:
    source = tmp_path / "safe.pptx"
    make_pptx(source, slides=3)
    seen: list[tuple[int, int]] = []

    def exporter(_source: Path, destination: Path, start: int, end: int) -> None:
        seen.append((start, end))
        for number in range(start, end + 1):
            Image.new("RGB", (1920, 1080), (number, 2, 3)).save(
                destination / f"slide-{number:04d}.png"
            )

    visuals = DocumentPreprocessor(pptx_exporter=exporter).prepare(
        command(source, start=2, end=3),
        [staged(source)],
        tmp_path / "output",
    )

    assert seen == [(2, 3)]
    assert [(item.source_kind, item.location_number) for item in visuals] == [
        ("slide", 2),
        ("slide", 3),
    ]
    assert all(item.width == 1920 and item.height == 1080 for item in visuals)
