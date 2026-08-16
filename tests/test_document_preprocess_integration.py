"""Opt-in Microsoft PowerPoint integration checks for the document adapter."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ask_ai_mcp.document_preprocess import DocumentPreprocessor
from ask_ai_mcp.models import SourceExtractionCommand, SourceExtractionProfile
from ask_ai_mcp.source import StagedSource

pytestmark = pytest.mark.skipif(
    os.environ.get("ASK_AI_MCP_RUN_POWERPOINT_INTEGRATION") != "1",
    reason="PowerPoint integration tests are opt-in",
)


def test_real_powerpoint_round_trip_exports_one_slide(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.pptx"
    create_script = tmp_path / "create-pptx.ps1"
    create_script.write_text(
        """
$ErrorActionPreference = 'Stop'
$app = $null
$presentation = $null
try {
    $app = New-Object -ComObject PowerPoint.Application
    $app.AutomationSecurity = 3
    $presentation = $app.Presentations.Add()
    $slide = $presentation.Slides.Add(1, 12)
    $slide.Shapes.AddTextbox(1, 100, 100, 600, 100).TextFrame.TextRange.Text = 'Synthetic'
    $presentation.SaveAs($args[0], 24)
}
finally {
    if ($null -ne $presentation) {
        $presentation.Close()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation)
    }
    if ($null -ne $app) {
        $app.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app)
    }
}
""".strip(),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(create_script),
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, "PowerPoint fixture creation failed"
    staged = StagedSource(
        original_name="synthetic.pptx",
        staged_name="source-0001.pptx",
        path=source,
        sha256="a" * 64,
        size_bytes=source.stat().st_size,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    output = tmp_path / "output"
    output.mkdir()

    visuals = DocumentPreprocessor().prepare(
        SourceExtractionCommand(
            source_files=[str(source)],
            profile=SourceExtractionProfile.DOCUMENT_EVIDENCE,
        ),
        [staged],
        output,
    )

    assert len(visuals) == 1
    assert visuals[0].path.is_file()
    assert visuals[0].width == 1920
    assert visuals[0].height > 0
