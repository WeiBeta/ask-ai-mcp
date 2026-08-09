"""Tests for the loopback-only MiniMax H3 ComfyUI adapter."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ask_ai_mcp.h3 import POSTPROCESS_MODELS, REQUIRED_MODELS, H3ClientError, H3ComfyClient
from ask_ai_mcp.models import (
    H3GenerationCommand,
    H3InterpolationModel,
    H3JobState,
    H3PostprocessCommand,
    H3ResolutionPreset,
    H3UpscaleModel,
    H3VisualProfile,
)


def make_client(handler, *, output_root: Path | None = None) -> H3ComfyClient:
    client = H3ComfyClient(output_root=output_root)
    transport = httpx.MockTransport(handler)
    client._client = lambda: httpx.Client(  # type: ignore[method-assign]
        base_url="http://127.0.0.1:8188", transport=transport
    )
    return client


def test_backend_rejects_non_loopback_urls() -> None:
    with pytest.raises(H3ClientError, match="loopback"):
        H3ComfyClient(base_url="https://example.com")

    with pytest.raises(H3ClientError, match="unsupported"):
        H3ComfyClient(base_url="http://127.0.0.1:8188/other-endpoint")


def test_backend_status_checks_all_four_models() -> None:
    folders = {
        "/models/diffusion_models": [REQUIRED_MODELS[0], POSTPROCESS_MODELS[3]],
        "/models/text_encoders": [REQUIRED_MODELS[1]],
        "/models/vae": [*REQUIRED_MODELS[2:], POSTPROCESS_MODELS[4]],
        "/models/frame_interpolation": list(POSTPROCESS_MODELS[:2]),
        "/models/upscale_models": [POSTPROCESS_MODELS[2]],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/system_stats":
            return httpx.Response(
                200,
                json={
                    "system": {"comfyui_version": "0.30.0"},
                    "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 3090"}],
                },
            )
        return httpx.Response(200, json=folders[request.url.path])

    status = make_client(handler).status()
    assert status.ready is True
    assert status.missing_models == []
    assert status.postprocess_ready is True
    assert status.device_name == "cuda:0 NVIDIA GeForce RTX 3090"


def test_workflow_is_bounded_and_uses_offload_friendly_models() -> None:
    command = H3GenerationCommand(
        prompt="A quiet lake at sunrise with birds and natural stereo ambience.",
        resolution=H3ResolutionPreset.LANDSCAPE_480P,
        duration_seconds=5,
        seed=42,
    )
    workflow = H3ComfyClient.build_workflow(
        command,
        width=864,
        height=480,
        frame_count=H3ComfyClient.frame_count(5),
    )
    assert workflow["1"]["inputs"]["unet_name"] == REQUIRED_MODELS[0]
    assert workflow["2"]["inputs"]["clip_name"] == REQUIRED_MODELS[1]
    assert workflow["5"]["inputs"] == {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "prompt": command.prompt,
        "width": 864,
        "height": 480,
        "length": 124,
    }
    assert workflow["14"]["inputs"]["filename_prefix"] == "video/MiniMax_H3_API"


def test_submit_returns_prompt_id_without_waiting_for_generation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prompt"
        payload = json.loads(request.content)
        assert payload["prompt"]["5"]["inputs"]["width"] == 864
        return httpx.Response(200, json={"prompt_id": "local-job-1", "number": 1})

    result = make_client(handler).submit(
        H3GenerationCommand(prompt="A cinematic paper boat floating through a rainy neon street.")
    )
    assert result.prompt_id == "local-job-1"
    assert result.state is H3JobState.QUEUED_OR_RUNNING
    assert result.frame_count == 124


def test_job_status_returns_validated_local_output_path(tmp_path: Path) -> None:
    free_requests: list[dict[str, bool]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/history/local-job-2":
            return httpx.Response(
                200,
                json={
                    "local-job-2": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {
                            "14": {
                                "videos": [
                                    {
                                        "filename": "MiniMax_H3_API_00001.mp4",
                                        "subfolder": "video",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if request.url.path == "/free":
            free_requests.append(json.loads(request.content))
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected path: {request.url.path}")

    report = make_client(handler, output_root=tmp_path).job_status("local-job-2")
    assert report.state is H3JobState.SUCCEEDED
    assert report.assets[0].local_path == str(tmp_path / "video" / "MiniMax_H3_API_00001.mp4")
    assert report.assets[0].view_url.startswith("http://127.0.0.1:8188/view?")
    assert "VRAM release requested" in report.detail
    assert free_requests == [{"unload_models": True, "free_memory": True}]


def test_terminal_job_defers_vram_release_while_queue_is_busy() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/history/local-job-busy":
            return httpx.Response(
                200,
                json={
                    "local-job-busy": {
                        "status": {"status_str": "error", "completed": True},
                        "outputs": {},
                    }
                },
            )
        if request.url.path == "/queue":
            return httpx.Response(
                200,
                json={"queue_running": [[1, "another-job"]], "queue_pending": []},
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    report = make_client(handler).job_status("local-job-busy")

    assert report.state is H3JobState.FAILED
    assert "queue is not idle" in report.detail
    assert paths == ["/history/local-job-busy", "/queue"]


def test_repeated_terminal_status_requests_vram_release_once() -> None:
    free_request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal free_request_count
        if request.url.path == "/history/local-job-repeat":
            return httpx.Response(
                200,
                json={
                    "local-job-repeat": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {},
                    }
                },
            )
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if request.url.path == "/free":
            free_request_count += 1
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected path: {request.url.path}")

    client = make_client(handler)
    first = client.job_status("local-job-repeat")
    second = client.job_status("local-job-repeat")

    assert first.state is H3JobState.FAILED
    assert "VRAM release requested" in first.detail
    assert "already requested" in second.detail
    assert free_request_count == 1


def test_job_status_rejects_endpoint_path_characters() -> None:
    with pytest.raises(H3ClientError, match="prompt identifier"):
        H3ComfyClient().job_status("../system_stats")


def test_last_frame_requires_first_frame() -> None:
    with pytest.raises(ValueError, match="last_frame requires first_frame"):
        H3GenerationCommand(
            prompt="Animate this final frame backward into a complete cinematic scene.",
            last_frame="C:\\inputs\\last.png",
        )


def test_h3_generation_only_exposes_480p_presets() -> None:
    assert {item.value for item in H3ResolutionPreset} == {
        "landscape_480p",
        "portrait_480p",
        "square_480p",
    }


def test_postprocess_command_rejects_cross_style_models() -> None:
    with pytest.raises(ValueError, match="SeedVR2"):
        H3PostprocessCommand(
            source_video="C:\\shared\\original.mp4",
            visual_profile=H3VisualProfile.REALISTIC,
            upscale=H3UpscaleModel.ANIME_VIDEO,
            target_short_side=720,
        )

    with pytest.raises(ValueError, match="RIFE"):
        H3PostprocessCommand(
            source_video="C:\\shared\\original.mp4",
            visual_profile=H3VisualProfile.ANIME,
            interpolation=H3InterpolationModel.FILM,
            target_fps=48,
        )


def test_anime_postprocess_workflow_is_explicit_and_non_destructive() -> None:
    command = H3PostprocessCommand(
        source_video="C:\\shared\\original.mp4",
        visual_profile=H3VisualProfile.ANIME,
        interpolation=H3InterpolationModel.RIFE_4_26,
        upscale=H3UpscaleModel.ANIME_VIDEO,
        target_fps=48,
        target_short_side=720,
    )
    workflow = H3ComfyClient.build_postprocess_workflow(
        command, source_video="outputs/video/original.mp4"
    )
    class_types = [node["class_type"] for node in workflow.values()]
    assert "FrameInterpolationModelLoader" in class_types
    assert "ImageUpscaleWithModel" in class_types
    assert "SeedVR2Conditioning" not in class_types
    save = next(node for node in workflow.values() if node["class_type"] == "SaveVideo")
    assert save["inputs"]["filename_prefix"].startswith("postprocessed/")


def test_realistic_postprocess_workflow_uses_seedvr2_chunks() -> None:
    command = H3PostprocessCommand(
        source_video="C:\\shared\\original.mp4",
        visual_profile=H3VisualProfile.REALISTIC,
        interpolation=H3InterpolationModel.FILM,
        upscale=H3UpscaleModel.SEEDVR2_3B,
        target_fps=48,
        target_short_side=720,
        seed=7,
    )
    workflow = H3ComfyClient.build_postprocess_workflow(
        command, source_video="outputs/video/original.mp4"
    )
    class_types = [node["class_type"] for node in workflow.values()]
    assert "SeedVR2TemporalChunk" in class_types
    assert "SeedVR2TemporalMerge" in class_types
    assert "SeedVR2PostProcessing" in class_types


def test_postprocess_submission_stages_selected_video(tmp_path: Path) -> None:
    source = tmp_path / "outputs" / "selected.mp4"
    source.parent.mkdir()
    source.write_bytes(b"synthetic-video-fixture")
    input_root = tmp_path / "inputs"
    client = H3ComfyClient(
        output_root=tmp_path / "outputs",
        input_roots=[input_root],
        workspace_root=tmp_path,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        load_video = payload["prompt"]["1"]
        assert load_video["class_type"] == "LoadVideo"
        assert load_video["inputs"]["file"].startswith("h3-selected-")
        return httpx.Response(200, json={"prompt_id": "postprocess-job-1"})

    transport = httpx.MockTransport(handler)
    client._client = lambda: httpx.Client(  # type: ignore[method-assign]
        base_url="http://127.0.0.1:8188", transport=transport
    )
    result = client.submit_postprocess(
        H3PostprocessCommand(
            source_video=str(source),
            visual_profile=H3VisualProfile.ANIME,
            interpolation=H3InterpolationModel.RIFE_4_26,
            target_fps=48,
        )
    )
    assert result.prompt_id == "postprocess-job-1"
    assert len(list(input_root.glob("h3-selected-*.mp4"))) == 1
