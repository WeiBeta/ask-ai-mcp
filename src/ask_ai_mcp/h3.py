"""Loopback-only ComfyUI adapter for the local MiniMax H3 FL2VA workflow."""

from __future__ import annotations

import mimetypes
import os
import re
import shutil
import subprocess
import time
from hashlib import sha256
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from ask_ai_mcp.models import (
    H3BackendStatus,
    H3GenerationCommand,
    H3InterpolationModel,
    H3JobReport,
    H3JobState,
    H3JobSubmission,
    H3OutputAsset,
    H3PostprocessCommand,
    H3PostprocessSubmission,
    H3ResolutionPreset,
    H3UpscaleModel,
)

H3_UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
H3_TEXT_ENCODER = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
H3_VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
H3_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
H3_RIFE = "rife_v4.26.safetensors"
H3_FILM = "film_net_fp16.safetensors"
H3_ANIME_UPSCALER = "realesr-animevideov3.pth"
H3_SEEDVR2 = "seedvr2_3b_int8_convrot.safetensors"
H3_SEEDVR2_VAE = "seedvr2_ema_vae_fp16.safetensors"

REQUIRED_MODELS = (H3_UNET, H3_TEXT_ENCODER, H3_VIDEO_VAE, H3_AUDIO_VAE)
POSTPROCESS_MODELS = (
    H3_RIFE,
    H3_FILM,
    H3_ANIME_UPSCALER,
    H3_SEEDVR2,
    H3_SEEDVR2_VAE,
)
MODEL_FOLDERS = {
    "diffusion_models": (H3_UNET, H3_SEEDVR2),
    "text_encoders": (H3_TEXT_ENCODER,),
    "vae": (H3_VIDEO_VAE, H3_AUDIO_VAE, H3_SEEDVR2_VAE),
    "frame_interpolation": (H3_RIFE, H3_FILM),
    "upscale_models": (H3_ANIME_UPSCALER,),
}
RESOLUTIONS = {
    H3ResolutionPreset.LANDSCAPE_480P: (864, 480),
    H3ResolutionPreset.PORTRAIT_480P: (480, 864),
    H3ResolutionPreset.SQUARE_480P: (480, 480),
}
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class H3ClientError(RuntimeError):
    """A safe local-backend or request-contract failure."""


def load_h3_input_roots() -> list[Path]:
    value = os.environ.get("ASK_AI_MCP_H3_INPUT_ROOTS", "")
    return [Path(item).resolve() for item in value.split(";") if item.strip()]


class H3ComfyClient:
    """Submit and inspect bounded H3 jobs through a local ComfyUI server."""

    _backend_start_lock = Lock()

    def __init__(
        self,
        *,
        base_url: str | None = None,
        output_root: Path | None = None,
        input_roots: list[Path] | None = None,
        workspace_root: Path | None = None,
        timeout_seconds: float = 20.0,
        auto_start: bool | None = None,
        start_script: Path | None = None,
        start_timeout_seconds: float | None = None,
        start_poll_interval_seconds: float = 1.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("ASK_AI_MCP_H3_URL", "")).rstrip(
            "/"
        ) or "http://127.0.0.1:8188"
        self._validate_loopback_url(self.base_url)
        configured_output = os.environ.get("ASK_AI_MCP_H3_OUTPUT_ROOT", "")
        self.output_root = (
            output_root.resolve()
            if output_root is not None
            else Path(configured_output).resolve()
            if configured_output
            else None
        )
        self.input_roots = [root.resolve() for root in (input_roots or load_h3_input_roots())]
        configured_comfy_input = os.environ.get("ASK_AI_MCP_H3_COMFY_INPUT_ROOT", "")
        self.comfy_input_root = (
            Path(configured_comfy_input).resolve()
            if configured_comfy_input
            else self.input_roots[0]
            if self.input_roots
            else None
        )
        configured_workspace = os.environ.get("ASK_AI_MCP_H3_WORKSPACE_ROOT", "")
        self.workspace_root = (
            workspace_root.resolve()
            if workspace_root is not None
            else Path(configured_workspace).resolve()
            if configured_workspace
            else None
        )
        self.timeout_seconds = timeout_seconds
        self.auto_start = (
            auto_start
            if auto_start is not None
            else os.environ.get("ASK_AI_MCP_H3_AUTO_START", "1").strip().lower()
            not in _FALSE_VALUES
        )
        self.start_script = self._resolve_start_script(start_script)
        self.start_timeout_seconds = (
            start_timeout_seconds
            if start_timeout_seconds is not None
            else self._load_start_timeout()
        )
        if self.start_timeout_seconds <= 0:
            raise H3ClientError("ComfyUI startup timeout must be positive")
        if start_poll_interval_seconds < 0:
            raise H3ClientError("ComfyUI startup poll interval cannot be negative")
        self.start_poll_interval_seconds = start_poll_interval_seconds
        self.auto_free_vram = (
            os.environ.get("ASK_AI_MCP_H3_AUTO_FREE_VRAM", "1").strip().lower() not in _FALSE_VALUES
        )
        self._vram_release_lock = Lock()
        self._vram_release_requested: set[str] = set()

    def status(self) -> H3BackendStatus:
        try:
            self._ensure_backend()
            with self._client() as client:
                system = client.get("/system_stats").raise_for_status().json()
                present: set[str] = set()
                for folder in MODEL_FOLDERS:
                    response = client.get(f"/models/{folder}")
                    response.raise_for_status()
                    values = response.json()
                    if isinstance(values, list):
                        present.update(str(item) for item in values)
        except (H3ClientError, httpx.HTTPError, ValueError, TypeError) as error:
            failure = str(error) if isinstance(error, H3ClientError) else type(error).__name__
            return H3BackendStatus(
                backend_url=self.base_url,
                reachable=False,
                required_models=list(REQUIRED_MODELS),
                missing_models=list(REQUIRED_MODELS),
                ready=False,
                postprocess_models=list(POSTPROCESS_MODELS),
                missing_postprocess_models=list(POSTPROCESS_MODELS),
                postprocess_ready=False,
                detail=f"ComfyUI local backend unavailable: {failure}",
            )

        missing = [name for name in REQUIRED_MODELS if name not in present]
        missing_postprocess = [name for name in POSTPROCESS_MODELS if name not in present]
        system_info = system.get("system", {}) if isinstance(system, dict) else {}
        devices = system.get("devices", []) if isinstance(system, dict) else []
        device_name = None
        if isinstance(devices, list) and devices and isinstance(devices[0], dict):
            device_name = str(devices[0].get("name") or "") or None
        version = None
        if isinstance(system_info, dict):
            version = str(system_info.get("comfyui_version") or "") or None
        return H3BackendStatus(
            backend_url=self.base_url,
            reachable=True,
            comfyui_version=version,
            device_name=device_name,
            required_models=list(REQUIRED_MODELS),
            missing_models=missing,
            ready=not missing,
            postprocess_models=list(POSTPROCESS_MODELS),
            missing_postprocess_models=missing_postprocess,
            postprocess_ready=not missing_postprocess,
            detail=(
                "MiniMax H3 generation and post-processing are ready"
                if not missing and not missing_postprocess
                else "Required generation or post-processing models are missing"
            ),
        )

    def submit(self, command: H3GenerationCommand) -> H3JobSubmission:
        self._ensure_backend()
        width, height = RESOLUTIONS[command.resolution]
        frame_count = self.frame_count(command.duration_seconds)
        with self._client() as client:
            first = self._upload_image(client, command.first_frame) if command.first_frame else None
            last = self._upload_image(client, command.last_frame) if command.last_frame else None
            workflow = self.build_workflow(
                command,
                width=width,
                height=height,
                frame_count=frame_count,
                first_image=first,
                last_image=last,
            )
            try:
                response = client.post(
                    "/prompt",
                    json={"prompt": workflow, "client_id": str(uuid4())},
                )
                response.raise_for_status()
                payload = response.json()
                prompt_id = str(payload["prompt_id"])
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                raise H3ClientError(f"ComfyUI rejected H3 job: {type(error).__name__}") from error
        return H3JobSubmission(
            prompt_id=prompt_id,
            resolution=command.resolution,
            width=width,
            height=height,
            requested_duration_seconds=command.duration_seconds,
            frame_count=frame_count,
            seed=command.seed,
        )

    def submit_postprocess(self, command: H3PostprocessCommand) -> H3PostprocessSubmission:
        self._ensure_backend()
        source = self._validate_source_video(command.source_video)
        staged_source = self._stage_source_video(source)
        workflow = self.build_postprocess_workflow(command, source_video=staged_source.name)
        try:
            with self._client() as client:
                response = client.post(
                    "/prompt",
                    json={"prompt": workflow, "client_id": str(uuid4())},
                )
                response.raise_for_status()
                payload = response.json()
                prompt_id = str(payload["prompt_id"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise H3ClientError(
                f"ComfyUI rejected H3 post-processing job: {type(error).__name__}"
            ) from error
        return H3PostprocessSubmission(
            prompt_id=prompt_id,
            source_video=str(source),
            visual_profile=command.visual_profile,
            interpolation=command.interpolation,
            upscale=command.upscale,
            target_fps=command.target_fps,
            target_short_side=command.target_short_side,
        )

    def job_status(self, prompt_id: str) -> H3JobReport:
        if re.fullmatch(r"[A-Za-z0-9-]{1,128}", prompt_id) is None:
            raise H3ClientError("Invalid H3 prompt identifier")
        self._ensure_backend()
        try:
            with self._client() as client:
                response = client.get(f"/history/{prompt_id}")
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise H3ClientError(f"Unable to read H3 job: {type(error).__name__}") from error

        if not isinstance(payload, dict) or prompt_id not in payload:
            return H3JobReport(
                prompt_id=prompt_id,
                state=H3JobState.QUEUED_OR_RUNNING,
                detail="Job is queued or running; poll again later",
            )
        record = payload[prompt_id]
        if not isinstance(record, dict):
            return H3JobReport(
                prompt_id=prompt_id,
                state=H3JobState.UNKNOWN,
                detail="ComfyUI returned an unrecognized history record",
            )
        status = record.get("status", {})
        status_text = status.get("status_str") if isinstance(status, dict) else None
        completed = bool(status.get("completed")) if isinstance(status, dict) else False
        assets = self._collect_assets(record.get("outputs", {}))
        if completed and assets:
            state = H3JobState.SUCCEEDED
            detail = "H3 task completed"
        elif status_text in {"error", "failed"}:
            state = H3JobState.FAILED
            detail = "H3 generation failed; inspect the local ComfyUI log"
        elif completed:
            state = H3JobState.FAILED
            detail = "H3 job completed without a video output"
        else:
            state = H3JobState.QUEUED_OR_RUNNING
            detail = "Job is queued or running; poll again later"
        if state in {H3JobState.SUCCEEDED, H3JobState.FAILED}:
            detail = f"{detail}; {self._release_vram_if_idle(prompt_id)}"
        return H3JobReport(prompt_id=prompt_id, state=state, assets=assets, detail=detail)

    def _ensure_backend(self) -> None:
        """Start the configured loopback ComfyUI service when it is not listening."""

        if not self.auto_start:
            return
        if self._backend_is_ready():
            return

        with self._backend_start_lock:
            if self._backend_is_ready():
                return

            self._start_backend()
            deadline = time.monotonic() + self.start_timeout_seconds
            last_error: httpx.HTTPError | None = None
            while time.monotonic() < deadline:
                try:
                    self._probe_backend()
                    return
                except httpx.HTTPError as error:
                    last_error = error
                    if self.start_poll_interval_seconds:
                        time.sleep(self.start_poll_interval_seconds)
            error_name = type(last_error).__name__ if last_error is not None else "Timeout"
            raise H3ClientError(
                f"ComfyUI did not become ready within {self.start_timeout_seconds:g} seconds: "
                f"{error_name}"
            )

    def _backend_is_ready(self) -> bool:
        """Distinguish a stopped backend from a persistently unhealthy HTTP service."""

        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            try:
                self._probe_backend()
                return True
            except (httpx.ConnectError, httpx.ConnectTimeout):
                return False
            except httpx.HTTPError as error:
                last_error = error
                if attempt < 2:
                    time.sleep(min(max(self.start_poll_interval_seconds, 0.1), 0.5))
        error_name = type(last_error).__name__ if last_error is not None else "HTTPError"
        raise H3ClientError(
            f"ComfyUI responded but failed its readiness probe: {error_name}"
        ) from last_error

    def _probe_backend(self) -> None:
        with self._client() as client:
            client.get("/system_stats").raise_for_status()

    def _start_backend(self) -> None:
        if self.start_script is None:
            raise H3ClientError(
                "ComfyUI is unavailable and ASK_AI_MCP_H3_START_SCRIPT is not configured"
            )
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            raise H3ClientError("PowerShell 7 (pwsh) is required to start ComfyUI")
        try:
            completed = subprocess.run(
                [pwsh, "-NoProfile", "-NonInteractive", "-File", str(self.start_script)],
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                timeout=min(self.start_timeout_seconds, 30.0),
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise H3ClientError(f"Unable to start ComfyUI: {type(error).__name__}") from error
        if completed.returncode != 0:
            raise H3ClientError(
                f"ComfyUI start script failed with exit code {completed.returncode}"
            )

    @staticmethod
    def _resolve_start_script(start_script: Path | None) -> Path | None:
        configured = os.environ.get("ASK_AI_MCP_H3_START_SCRIPT", "").strip()
        candidate = (
            start_script if start_script is not None else Path(configured) if configured else None
        )
        if candidate is None:
            repository_candidate = (
                Path(__file__).resolve().parents[2] / "scripts" / "start_comfyui_h3.ps1"
            )
            candidate = repository_candidate if repository_candidate.is_file() else None
        if candidate is None:
            return None
        if not candidate.is_absolute():
            raise H3ClientError("ComfyUI start script path must be absolute")
        resolved = candidate.resolve()
        if resolved.suffix.casefold() != ".ps1" or not resolved.is_file():
            raise H3ClientError("Configured ComfyUI start script is not a readable .ps1 file")
        return resolved

    @staticmethod
    def _load_start_timeout() -> float:
        raw = os.environ.get("ASK_AI_MCP_H3_START_TIMEOUT_SECONDS", "120").strip()
        try:
            value = float(raw)
        except ValueError as error:
            raise H3ClientError("ComfyUI startup timeout must be numeric") from error
        if not 5 <= value <= 600:
            raise H3ClientError("ComfyUI startup timeout must be between 5 and 600 seconds")
        return value

    def _release_vram_if_idle(self, prompt_id: str) -> str:
        """Ask ComfyUI to unload models only after its global queue is idle."""

        if not self.auto_free_vram:
            return "automatic VRAM release is disabled"
        with self._vram_release_lock:
            if prompt_id in self._vram_release_requested:
                return "VRAM release was already requested"
            try:
                with self._client() as client:
                    queue_response = client.get("/queue")
                    queue_response.raise_for_status()
                    queue = queue_response.json()
                    if not isinstance(queue, dict):
                        return "VRAM release skipped because queue status was invalid"
                    running = queue.get("queue_running")
                    pending = queue.get("queue_pending")
                    if not isinstance(running, list) or not isinstance(pending, list):
                        return "VRAM release skipped because queue status was invalid"
                    if running or pending:
                        return "VRAM release deferred because the ComfyUI queue is not idle"
                    response = client.post(
                        "/free",
                        json={"unload_models": True, "free_memory": True},
                    )
                    response.raise_for_status()
            except (httpx.HTTPError, TypeError, ValueError) as error:
                return f"VRAM release request failed: {type(error).__name__}"
            self._vram_release_requested.add(prompt_id)
            return "ComfyUI model unload and VRAM release requested"

    @staticmethod
    def frame_count(duration_seconds: float) -> int:
        base = max(5, round(duration_seconds * 24))
        return base + (5 - (base % 17)) % 17

    @staticmethod
    def build_workflow(
        command: H3GenerationCommand,
        *,
        width: int,
        height: int,
        frame_count: int,
        first_image: str | None = None,
        last_image: str | None = None,
    ) -> dict[str, dict[str, object]]:
        conditioning_inputs: dict[str, object] = {
            "clip": ["2", 0],
            "vae": ["3", 0],
            "prompt": command.prompt,
            "width": width,
            "height": height,
            "length": frame_count,
        }
        workflow: dict[str, dict[str, object]] = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": H3_UNET, "weight_dtype": "default"},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": H3_TEXT_ENCODER,
                    "type": "minimax",
                    "device": "default",
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": H3_VIDEO_VAE}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": H3_AUDIO_VAE}},
            "5": {"class_type": "MiniMaxH3ImageToVideo", "inputs": conditioning_inputs},
            "6": {"class_type": "RandomNoise", "inputs": {"noise_seed": command.seed}},
            "7": {
                "class_type": "BasicScheduler",
                "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": 20, "denoise": 1.0},
            },
            "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "9": {
                "class_type": "BasicGuider",
                "inputs": {"model": ["1", 0], "conditioning": ["5", 0]},
            },
            "10": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["6", 0],
                    "guider": ["9", 0],
                    "sampler": ["8", 0],
                    "sigmas": ["7", 0],
                    "latent_image": ["5", 1],
                },
            },
            "11": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["10", 0], "vae": ["3", 0]},
            },
            "12": {
                "class_type": "VAEDecodeAudio",
                "inputs": {"samples": ["10", 0], "vae": ["4", 0]},
            },
            "13": {
                "class_type": "CreateVideo",
                "inputs": {"images": ["11", 0], "audio": ["12", 0], "fps": 24},
            },
            "14": {
                "class_type": "SaveVideo",
                "inputs": {
                    "video": ["13", 0],
                    "filename_prefix": "video/MiniMax_H3_API",
                    "format": "auto",
                    "codec": "auto",
                },
            },
        }
        next_node = 15
        if first_image:
            workflow[str(next_node)] = {
                "class_type": "LoadImage",
                "inputs": {"image": first_image},
            }
            conditioning_inputs["first_frame"] = [str(next_node), 0]
            next_node += 1
        if last_image:
            workflow[str(next_node)] = {
                "class_type": "LoadImage",
                "inputs": {"image": last_image},
            }
            conditioning_inputs["last_frame"] = [str(next_node), 0]
        return workflow

    @staticmethod
    def build_postprocess_workflow(
        command: H3PostprocessCommand,
        *,
        source_video: str,
    ) -> dict[str, dict[str, object]]:
        workflow: dict[str, dict[str, object]] = {}

        def add_node(class_type: str, inputs: dict[str, object]) -> str:
            node_id = str(len(workflow) + 1)
            workflow[node_id] = {"class_type": class_type, "inputs": inputs}
            return node_id

        load_video = add_node("LoadVideo", {"file": source_video})
        components = add_node("GetVideoComponents", {"video": [load_video, 0]})
        images: list[object] = [components, 0]

        if command.interpolation is not H3InterpolationModel.NONE:
            interpolation_name = {
                H3InterpolationModel.RIFE_4_26: H3_RIFE,
                H3InterpolationModel.FILM: H3_FILM,
            }[command.interpolation]
            loader = add_node("FrameInterpolationModelLoader", {"model_name": interpolation_name})
            interpolated = add_node(
                "FrameInterpolate",
                {
                    "interp_model": [loader, 0],
                    "images": images,
                    "multiplier": command.target_fps // 24,
                },
            )
            images = [interpolated, 0]

        if command.upscale is H3UpscaleModel.ANIME_VIDEO:
            loader = add_node("UpscaleModelLoader", {"model_name": H3_ANIME_UPSCALER})
            upscaled = add_node(
                "ImageUpscaleWithModel",
                {"upscale_model": [loader, 0], "image": images},
            )
            resized = add_node(
                "ImageScaleBy",
                {
                    "image": [upscaled, 0],
                    "upscale_method": "lanczos",
                    "scale_by": command.target_short_side / (480 * 4),
                },
            )
            images = [resized, 0]
        elif command.upscale is H3UpscaleModel.SEEDVR2_3B:
            resized = add_node(
                "ImageScaleBy",
                {
                    "image": images,
                    "upscale_method": "lanczos",
                    "scale_by": command.target_short_side / 480,
                },
            )
            model = add_node(
                "UNETLoader",
                {"unet_name": H3_SEEDVR2, "weight_dtype": "default"},
            )
            vae = add_node("VAELoader", {"vae_name": H3_SEEDVR2_VAE})
            preprocessed = add_node("SeedVR2Preprocess", {"resized_images": [resized, 0]})
            encoded = add_node(
                "VAEEncodeTiled",
                {
                    "pixels": [preprocessed, 0],
                    "vae": [vae, 0],
                    "tile_size": 512,
                    "overlap": 128,
                    "temporal_size": 64,
                    "temporal_overlap": 8,
                },
            )
            chunks = add_node(
                "SeedVR2TemporalChunk",
                {
                    "latent": [encoded, 0],
                    "temporal_overlap": 0,
                    "chunking_mode": "auto",
                },
            )
            conditioning = add_node(
                "SeedVR2Conditioning",
                {"model": [model, 0], "vae_conditioning": [chunks, 0]},
            )
            sampled = add_node(
                "KSampler",
                {
                    "model": [model, 0],
                    "seed": command.seed,
                    "steps": 1,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "positive": [conditioning, 0],
                    "negative": [conditioning, 1],
                    "latent_image": [chunks, 0],
                    "denoise": 1.0,
                },
            )
            merged = add_node(
                "SeedVR2TemporalMerge",
                {"latents": [sampled, 0], "temporal_overlap": [chunks, 1]},
            )
            decoded = add_node(
                "VAEDecodeTiled",
                {
                    "samples": [merged, 0],
                    "vae": [vae, 0],
                    "tile_size": 512,
                    "overlap": 128,
                    "temporal_size": 64,
                    "temporal_overlap": 8,
                },
            )
            corrected = add_node(
                "SeedVR2PostProcessing",
                {
                    "images": [decoded, 0],
                    "original_resized_images": [resized, 0],
                    "color_correction_method": "none",
                },
            )
            images = [corrected, 0]

        video = add_node(
            "CreateVideo",
            {
                "images": images,
                "audio": [components, 1],
                "fps": command.target_fps,
                "bit_depth": 8,
            },
        )
        add_node(
            "SaveVideo",
            {
                "video": [video, 0],
                "filename_prefix": (
                    "postprocessed/H3_"
                    f"{command.visual_profile.value}_{command.target_short_side}p_"
                    f"{command.target_fps}fps"
                ),
                "format": "mp4",
                "codec": "h264",
            },
        )
        return workflow

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            trust_env=False,
        )

    def _upload_image(self, client: httpx.Client, value: str) -> str:
        path = self._validate_input_file(value)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as handle:
                response = client.post(
                    "/upload/image",
                    files={"image": (path.name, handle, media_type)},
                    data={"overwrite": "false", "type": "input"},
                )
                response.raise_for_status()
                payload = response.json()
            name = str(payload["name"])
            subfolder = str(payload.get("subfolder") or "")
        except (OSError, httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise H3ClientError(
                f"Unable to stage H3 input image: {type(error).__name__}"
            ) from error
        return str(Path(subfolder) / name) if subfolder else name

    def _validate_input_file(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            raise H3ClientError("H3 input image path must be absolute")
        if not self.input_roots:
            raise H3ClientError("ASK_AI_MCP_H3_INPUT_ROOTS is not configured")
        if path.is_symlink() or not path.is_file():
            raise H3ClientError("H3 input image must be a plain file")
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for root in self.input_roots):
            raise H3ClientError("H3 input image is outside configured roots")
        if resolved.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise H3ClientError("H3 input image must be PNG, JPEG, or WebP")
        return resolved

    def _stage_source_video(self, source: Path) -> Path:
        if self.comfy_input_root is None:
            raise H3ClientError("ASK_AI_MCP_H3_COMFY_INPUT_ROOT is not configured")
        self.comfy_input_root.mkdir(parents=True, exist_ok=True)
        digest = sha256()
        try:
            with source.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            target = self.comfy_input_root / (
                f"h3-selected-{digest.hexdigest()[:16]}{source.suffix.casefold()}"
            )
            if not target.exists():
                temporary = target.with_suffix(target.suffix + ".tmp")
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
        except OSError as error:
            raise H3ClientError(
                f"Unable to stage selected H3 video: {type(error).__name__}"
            ) from error
        return target

    def _validate_source_video(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            raise H3ClientError("H3 source video path must be absolute")
        if self.workspace_root is None:
            raise H3ClientError("ASK_AI_MCP_H3_WORKSPACE_ROOT is not configured")
        if path.is_symlink() or not path.is_file():
            raise H3ClientError("H3 source video must be a plain file")
        if path.stat().st_size > 2 * 1024 * 1024 * 1024:
            raise H3ClientError("H3 source video exceeds the 2 GiB limit")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.workspace_root):
            raise H3ClientError("H3 source video is outside the shared workspace")
        if resolved.suffix.casefold() not in {".mp4", ".mov", ".mkv", ".webm"}:
            raise H3ClientError("H3 source video must be MP4, MOV, MKV, or WebM")
        return resolved

    def _collect_assets(self, outputs: object) -> list[H3OutputAsset]:
        assets: list[H3OutputAsset] = []
        if not isinstance(outputs, dict):
            return assets
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for values in node_output.values():
                if not isinstance(values, list):
                    continue
                for item in values:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    filename = str(item["filename"])
                    subfolder = str(item.get("subfolder") or "")
                    storage_type = str(item.get("type") or "output")
                    params = {"filename": filename, "subfolder": subfolder, "type": storage_type}
                    view_url = str(httpx.URL(f"{self.base_url}/view", params=params))
                    local_path = self._resolve_output_path(filename, subfolder, storage_type)
                    assets.append(
                        H3OutputAsset(
                            filename=filename,
                            subfolder=subfolder,
                            storage_type=storage_type,
                            local_path=str(local_path) if local_path else None,
                            view_url=view_url,
                        )
                    )
        return assets

    def _resolve_output_path(self, filename: str, subfolder: str, storage_type: str) -> Path | None:
        if self.output_root is None or storage_type != "output":
            return None
        candidate = (self.output_root / subfolder / filename).resolve()
        if not candidate.is_relative_to(self.output_root):
            return None
        return candidate

    @staticmethod
    def _validate_loopback_url(value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise H3ClientError("H3 backend must be an HTTP loopback URL")
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise H3ClientError("H3 backend URL contains unsupported components")
