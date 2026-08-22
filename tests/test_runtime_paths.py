"""Guard the canonical Windows host paths for local AI runtimes."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY_FILES = (
    "README.md",
    "docs/DESKTOP_SETUP.md",
    "docs/H3_HANDOFF_ZH-CN.md",
    "docs/MCP_SURFACE.md",
    "scripts/start_comfyui_h3.ps1",
)


def test_comfyui_host_root_is_canonical_everywhere() -> None:
    for relative in COMFY_FILES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert r"C:\AI\ComfyUI-H3" not in text
        assert r"C:\AI\ComfyUI" in text


def test_local_snapshot_uses_canonical_runtime_roots() -> None:
    text = (ROOT / "operator-archive/scripts/Update-LocalSnapshot.ps1").read_text(encoding="utf-8")
    assert r'"C:\AI\Llama"' in text
    assert r'"C:\AI\ComfyUI"' in text
    assert r"Documents\Codex\local-ai\qwen3.8-27b" not in text
    assert r"C:\AI\ComfyUI-H3" not in text
