"""Deterministic visual preprocessing for staged PDF and PPTX copies."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

import pypdfium2
from PIL import Image

from ask_ai_mcp.models import SourceExtractionCommand
from ask_ai_mcp.source import SourceProcessingError, StagedSource

MAX_DOCUMENT_VISUALS = 32
MAX_PPTX_ZIP_ENTRIES = 20_000
MAX_PPTX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
PDF_RENDER_SCALE = 2.0
_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True, slots=True)
class PreparedVisual:
    """One model-ready image tied to its immutable original source."""

    source_index: int
    original_name: str
    source_sha256: str
    source_kind: str
    location_number: int
    path: Path
    sha256: str
    width: int
    height: int
    media_type: str = "image/png"

    def manifest_entry(self, output_root: Path) -> dict[str, object]:
        entry = asdict(self)
        entry["path"] = self.path.relative_to(output_root).as_posix()
        return entry


PptxExporter = Callable[[Path, Path, int, int], None]


class DocumentPreprocessor:
    """Render staged documents without granting the model file or shell access."""

    def __init__(self, *, pptx_exporter: PptxExporter | None = None) -> None:
        self.pptx_exporter = pptx_exporter or self._export_pptx_with_powerpoint

    def prepare(
        self,
        command: SourceExtractionCommand,
        staged_sources: list[StagedSource],
        output_root: Path,
    ) -> list[PreparedVisual]:
        prepared: list[PreparedVisual] = []
        preprocess_root = output_root / "preprocessed"
        preprocess_root.mkdir(parents=True, exist_ok=True)
        for source_index, source in enumerate(staged_sources, start=1):
            suffix = source.path.suffix.casefold()
            if suffix not in {".pdf", ".pptx"}:
                continue
            source_root = preprocess_root / f"source-{source_index:04d}"
            source_root.mkdir()
            if suffix == ".pdf":
                visuals = self._render_pdf(command, source, source_index, source_root)
            else:
                visuals = self._render_pptx(command, source, source_index, source_root)
            prepared.extend(visuals)
            if len(prepared) > MAX_DOCUMENT_VISUALS:
                raise SourceProcessingError("document selection exceeds the 32-visual job limit")
        manifest = {
            "contract": "document_visuals_v1",
            "renderers": {
                "pdf": "pypdfium2",
                "pptx": "microsoft_powerpoint_com",
            },
            "visuals": [item.manifest_entry(output_root) for item in prepared],
        }
        (preprocess_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return prepared

    def _render_pdf(
        self,
        command: SourceExtractionCommand,
        source: StagedSource,
        source_index: int,
        destination: Path,
    ) -> list[PreparedVisual]:
        try:
            document = pypdfium2.PdfDocument(source.path)
        except Exception as error:
            raise SourceProcessingError("PDF could not be opened by the locked renderer") from error
        try:
            start, end = self._selection(command, len(document), "PDF")
            visuals = []
            for page_number in range(start, end + 1):
                page = document[page_number - 1]
                bitmap = None
                try:
                    bitmap = page.render(scale=PDF_RENDER_SCALE)
                    image = bitmap.to_pil().convert("RGB")
                    path = destination / f"page-{page_number:04d}.png"
                    image.save(path, format="PNG", compress_level=9, optimize=False)
                    visuals.append(
                        self._visual(
                            source_index,
                            source,
                            "page",
                            page_number,
                            path,
                            image.width,
                            image.height,
                        )
                    )
                except Exception as error:
                    raise SourceProcessingError("PDF page rendering failed") from error
                finally:
                    if bitmap is not None:
                        bitmap.close()
                    page.close()
            return visuals
        finally:
            document.close()

    def _render_pptx(
        self,
        command: SourceExtractionCommand,
        source: StagedSource,
        source_index: int,
        destination: Path,
    ) -> list[PreparedVisual]:
        slide_count = self._inspect_pptx(source.path)
        start, end = self._selection(command, slide_count, "PPTX")
        try:
            self.pptx_exporter(source.path, destination, start, end)
        except SourceProcessingError:
            raise
        except Exception as error:
            raise SourceProcessingError("PowerPoint slide export failed") from error
        visuals = []
        for slide_number in range(start, end + 1):
            path = destination / f"slide-{slide_number:04d}.png"
            if not path.is_file() or path.is_symlink():
                raise SourceProcessingError("PowerPoint did not export every requested slide")
            try:
                with Image.open(path) as image:
                    image.verify()
                with Image.open(path) as image:
                    width, height = image.size
            except (OSError, ValueError) as error:
                raise SourceProcessingError("PowerPoint produced an invalid slide image") from error
            visuals.append(
                self._visual(
                    source_index,
                    source,
                    "slide",
                    slide_number,
                    path,
                    width,
                    height,
                )
            )
        return visuals

    @staticmethod
    def _selection(
        command: SourceExtractionCommand,
        total: int,
        label: str,
    ) -> tuple[int, int]:
        if total < 1:
            raise SourceProcessingError(f"{label} contains no renderable pages")
        start = command.page_start or 1
        end = command.page_end or total
        if end > total:
            raise SourceProcessingError(f"requested page range exceeds the {label} page count")
        if end - start + 1 > MAX_DOCUMENT_VISUALS:
            if command.page_start is None:
                raise SourceProcessingError(
                    f"{label} exceeds 32 pages; provide page_start and page_end"
                )
            raise SourceProcessingError("document selection exceeds the 32-visual job limit")
        return start, end

    @staticmethod
    def _inspect_pptx(path: Path) -> int:
        total_uncompressed = 0
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_PPTX_ZIP_ENTRIES:
                    raise SourceProcessingError("PPTX contains too many package entries")
                names = set()
                for info in infos:
                    normalized = info.filename.replace("\\", "/")
                    parts = PurePosixPath(normalized).parts
                    if (
                        normalized.startswith("/")
                        or ".." in parts
                        or not parts
                        or stat.S_ISLNK(info.external_attr >> 16)
                    ):
                        raise SourceProcessingError("PPTX package contains an unsafe entry")
                    if info.flag_bits & 1:
                        raise SourceProcessingError("encrypted PPTX packages are not supported")
                    total_uncompressed += info.file_size
                    if total_uncompressed > MAX_PPTX_UNCOMPRESSED_BYTES:
                        raise SourceProcessingError("PPTX expanded size exceeds 2 GiB")
                    names.add(normalized.casefold())
                if any(
                    name.endswith("vbaproject.bin") or name.endswith("vbadata.xml")
                    for name in names
                ):
                    raise SourceProcessingError("macro-enabled PPTX content is not supported")
                DocumentPreprocessor._reject_external_relationships(archive)
                presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        except SourceProcessingError:
            raise
        except (KeyError, OSError, ET.ParseError, zipfile.BadZipFile) as error:
            raise SourceProcessingError("PPTX package is malformed") from error
        namespace = "http://schemas.openxmlformats.org/presentationml/2006/main"
        slides = presentation.findall(f".//{{{namespace}}}sldId")
        return len(slides)

    @staticmethod
    def _reject_external_relationships(archive: zipfile.ZipFile) -> None:
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            if not normalized.casefold().endswith(".rels"):
                continue
            try:
                root = ET.fromstring(archive.read(info))
            except ET.ParseError as error:
                raise SourceProcessingError("PPTX relationship data is malformed") from error
            for relationship in root.findall(f"{{{_RELATIONSHIP_NAMESPACE}}}Relationship"):
                if relationship.attrib.get("TargetMode", "").casefold() == "external":
                    raise SourceProcessingError("PPTX external relationships are not supported")

    @staticmethod
    def _visual(
        source_index: int,
        source: StagedSource,
        source_kind: str,
        location_number: int,
        path: Path,
        width: int,
        height: int,
    ) -> PreparedVisual:
        return PreparedVisual(
            source_index=source_index,
            original_name=source.original_name,
            source_sha256=source.sha256,
            source_kind=source_kind,
            location_number=location_number,
            path=path,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            width=width,
            height=height,
        )

    @staticmethod
    def _export_pptx_with_powerpoint(
        source: Path,
        destination: Path,
        start: int,
        end: int,
    ) -> None:
        script = Path(__file__).with_name("export_pptx_slides.ps1")
        if not script.is_file():
            raise SourceProcessingError("the fixed PowerPoint export script is unavailable")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(script),
                    "-SourcePath",
                    str(source),
                    "-DestinationPath",
                    str(destination),
                    "-StartSlide",
                    str(start),
                    "-EndSlide",
                    str(end),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=creation_flags,
                env={**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1"},
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SourceProcessingError("PowerPoint export process was unavailable") from error
        if completed.returncode != 0:
            raise SourceProcessingError("PowerPoint export process failed")
