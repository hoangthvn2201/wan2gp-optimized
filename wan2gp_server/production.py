"""Resumable Episode Studio production coordinator."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import queue
import shlex
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .config import ServerConfig
from .engine import Wan2GPEngine
from .jobs import Job, JobStatus, media_type_of
from .narration import synthesize_longform
from .presets import PresetRegistry, build_settings
from .projects import ProjectError, ProjectRepository, fingerprint, utc_stamp
from .scene_plan import preset_for_mode


logger = logging.getLogger("wan2gp_server.production")
TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class ProductionError(RuntimeError):
    pass


def _relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _words_from_transcript(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("words", "transcript", "items"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if not isinstance(value, list):
        raise ProductionError("HyperFrames transcript is not a word list")
    words = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", item.get("word", ""))).strip()
        if not text:
            continue
        try:
            start = float(item.get("start", 0))
            end = float(item.get("end", start + 0.001))
        except (TypeError, ValueError):
            continue
        words.append(
            {
                "id": str(item.get("id", f"w{index}")),
                "text": text,
                "start": round(max(0.0, start), 3),
                "end": round(max(start + 0.001, end), 3),
            }
        )
    if not words:
        raise ProductionError("HyperFrames transcript contains no timed words")
    return words


def _caption_groups(words: list[dict[str, Any]], max_words: int = 6) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        current.append(word)
        following = words[index + 1] if index + 1 < len(words) else None
        pause = following and float(following["start"]) - float(word["end"]) >= 0.2
        sentence_end = str(word["text"]).rstrip().endswith((".", "?", "!"))
        if len(current) >= max_words or pause or sentence_end or following is None:
            groups.append(
                {
                    "start": float(current[0]["start"]),
                    "end": float(current[-1]["end"]),
                    "text": " ".join(str(item["text"]).strip() for item in current),
                }
            )
            current = []
    return groups


def _scene_timings(scenes: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    total = sum(float(scene.get("duration_weight", 1)) for scene in scenes)
    cursor = 0.0
    result = []
    for index, scene in enumerate(scenes):
        start = cursor
        cursor = duration if index == len(scenes) - 1 else cursor + duration * float(scene.get("duration_weight", 1)) / total
        result.append(
            {
                "scene_id": scene["id"],
                "start": round(start, 3),
                "duration": round(max(0.1, cursor - start), 3),
            }
        )
    return result


class ProductionCoordinator:
    def __init__(
        self,
        config: ServerConfig,
        repository: ProjectRepository,
        engine: Wan2GPEngine,
        presets: PresetRegistry,
    ):
        self.config = config
        self.repository = repository
        self.engine = engine
        self.presets = presets
        self._queue: "queue.Queue[tuple[str, str, dict[str, Any]]]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._active_project: str | None = None
        self._active_job: Job | None = None

    def start(self) -> None:
        self.repository.recover_interrupted()
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._worker, name="episode-production", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._shutdown.set()
        if self._active_job and self._active_job.status not in TERMINAL:
            self.engine.cancel(self._active_job)
        if self._thread:
            self._thread.join(timeout=10)

    def submit_run(self, project_id: str) -> None:
        self.repository.update(
            project_id,
            lambda project: project.update({"status": "queued", "cancel_requested": False}),
        )
        self._queue.put(("run", project_id, {}))

    def submit_regeneration(self, project_id: str, scene_id: str, values: dict[str, Any]) -> None:
        self._set_scene(
            project_id,
            scene_id,
            status="queued",
            progress=0,
            detail="Regeneration queued",
        )
        self.repository.update(project_id, lambda project: project.update({"status": "queued", "cancel_requested": False}))
        self._queue.put(("regenerate", project_id, {"scene_id": scene_id, **values}))

    def submit_rebuild(self, project_id: str) -> None:
        self.repository.update(project_id, lambda project: project.update({"status": "queued", "cancel_requested": False}))
        self._queue.put(("rebuild", project_id, {}))

    def cancel(self, project_id: str) -> None:
        self.repository.update(project_id, lambda project: project.update({"cancel_requested": True}))
        if self._active_project == project_id and self._active_job:
            self.engine.cancel(self._active_job)

    def _worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                action, project_id, payload = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            self._active_project = project_id
            try:
                if action == "run":
                    self._run_project(project_id)
                elif action == "regenerate":
                    self._regenerate(project_id, payload)
                elif action == "rebuild":
                    self._rebuild(project_id)
            except Exception as exc:
                logger.exception("Episode operation failed: project=%s action=%s", project_id, action)
                self._fail(project_id, str(exc))
            finally:
                self._active_job = None
                self._active_project = None
                self._queue.task_done()

    def _project_cancelled(self, project_id: str) -> bool:
        return self._shutdown.is_set() or bool(self.repository.load(project_id).get("cancel_requested"))

    def _set_stage(
        self,
        project_id: str,
        stage: str,
        status: str,
        detail: str,
        progress: int,
        **extra: Any,
    ) -> None:
        def update(project: dict[str, Any]) -> None:
            project["status"] = "running" if status in {"queued", "running"} else project.get("status", "running")
            project["active_operation"] = stage if status in {"queued", "running"} else None
            project["stages"][stage] = {
                **project["stages"].get(stage, {}),
                "status": status,
                "detail": detail,
                "progress": max(0, min(100, int(progress))),
                **extra,
            }

        self.repository.update(project_id, update)

    def _set_scene(self, project_id: str, scene_id: str, **values: Any) -> None:
        def update(project: dict[str, Any]) -> None:
            scene = next((item for item in project["scenes"] if item["id"] == scene_id), None)
            if scene is None:
                raise ProjectError(f"Unknown scene: {scene_id}")
            scene.update(values)

        self.repository.update(project_id, update)

    def _fail(self, project_id: str, message: str) -> None:
        def update(project: dict[str, Any]) -> None:
            if project.get("cancel_requested"):
                project["status"] = "cancelled"
            else:
                project["status"] = "failed"
            project["error"] = message
            active = project.get("active_operation")
            if active in project.get("stages", {}):
                project["stages"][active].update(
                    {"status": project["status"], "detail": message}
                )
            for scene in project.get("scenes", []):
                if scene.get("status") in {"queued", "running"}:
                    scene.update(
                        {"status": project["status"], "detail": message, "progress": scene.get("progress", 0)}
                    )
            project["active_operation"] = None

        try:
            self.repository.update(project_id, update)
            self.repository.event(project_id, "operation_failed", message)
        except ProjectError:
            logger.exception("Could not persist project failure")

    def _run_project(self, project_id: str) -> None:
        self.repository.event(project_id, "pipeline_started", "End-to-end production started")
        self._narrate(project_id)
        self._transcribe(project_id)
        self._resolve_timing(project_id)
        project = self.repository.load(project_id)
        pending = [scene for scene in project["scenes"] if scene.get("accepted_revision") is None]
        for index, scene in enumerate(pending, 1):
            if self._project_cancelled(project_id):
                raise ProductionError("Pipeline cancelled")
            self._set_stage(
                project_id,
                "assets",
                "running",
                f"Generating scene {index} of {len(pending)}",
                int((index - 1) * 100 / max(1, len(pending))),
            )
            self._generate_revision(project_id, scene["id"], candidate=False)
        self._set_stage(project_id, "assets", "ready", "All scene assets ready", 100)
        self._rebuild(project_id)

    def _narrate(self, project_id: str) -> None:
        project = self.repository.load(project_id)
        root = self.repository.project_root(project_id)
        existing_audio = root / str(project.get("narration", {}).get("audio", "missing"))
        if (
            project["stages"]["narration"].get("status") == "ready"
            and existing_audio.is_file()
            and existing_audio.stat().st_size >= 1000
        ):
            return
        self._set_stage(project_id, "narration", "running", "Generating Kokoro narration", 5)
        source = root / project["source"]["voiceover"]
        output = root / "narration" / "narration.wav"
        if self.config.mock_pipeline:
            self._mock_narration(source, output)
        else:
            text = source.read_text(encoding="utf-8")
            synthesize_longform(
                text,
                output,
                voice=project["configuration"]["voice"],
                speed=float(project["configuration"]["speed"]),
                progress=lambda current, total: self._set_stage(
                    project_id,
                    "narration",
                    "running",
                    f"Generating Kokoro chunk {current} of {total}",
                    max(5, int(current * 90 / total)),
                ),
            )
        if not output.is_file() or output.stat().st_size < 1000:
            raise ProductionError("Kokoro did not produce a usable narration WAV")

        def update(value: dict[str, Any]) -> None:
            value["narration"].update(
                {"audio": _relative(root, output), "voice": value["configuration"]["voice"]}
            )

        self.repository.update(project_id, update)
        self._set_stage(project_id, "narration", "ready", "Narration ready", 100)

    def _transcribe(self, project_id: str) -> None:
        project = self.repository.load(project_id)
        root = self.repository.project_root(project_id)
        existing_transcript = root / str(project.get("narration", {}).get("transcript", "missing"))
        if (
            project["stages"]["transcription"].get("status") == "ready"
            and existing_transcript.is_file()
            and existing_transcript.stat().st_size > 2
        ):
            return
        self._set_stage(project_id, "transcription", "running", "Creating word timestamps", 5)
        narration_dir = root / "narration"
        audio = root / project["narration"]["audio"]
        transcript = narration_dir / "transcript.json"
        raw = narration_dir / "transcript.raw.json"
        if self.config.mock_pipeline:
            words = self._mock_transcript(root / project["source"]["voiceover"], audio)
            raw.write_text(json.dumps(words, indent=2) + "\n", encoding="utf-8")
        else:
            command = [
                *shlex.split(self.config.hyperframes_bin),
                "transcribe",
                str(audio),
                "--dir",
                str(narration_dir),
                "--engine",
                "whisper",
                "--model",
                "small.en",
            ]
            self._run_command(command, cwd=root, timeout=7200)
            generated = narration_dir / "transcript.json"
            if not generated.is_file():
                raise ProductionError("HyperFrames produced no transcript.json")
            generated.replace(raw)
        words = _words_from_transcript(json.loads(raw.read_text(encoding="utf-8")))
        transcript.write_text(json.dumps(words, indent=2) + "\n", encoding="utf-8")
        source_words = (root / project["source"]["voiceover"]).read_text(encoding="utf-8").split()
        report = {
            "raw_word_count": len(words),
            "source_word_count": len(source_words),
            "coverage": round(min(1.0, len(words) / max(1, len(source_words))), 4),
            "note": "Raw word timings normalized; source text remains the narration authority.",
        }
        report_path = narration_dir / "reconciliation-report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        def update(value: dict[str, Any]) -> None:
            value["narration"].update(
                {
                    "transcript": _relative(root, transcript),
                    "raw_transcript": _relative(root, raw),
                    "report": _relative(root, report_path),
                    "duration_seconds": round(float(words[-1]["end"]), 3),
                }
            )

        self.repository.update(project_id, update)
        self._set_stage(project_id, "transcription", "ready", "Transcript ready", 100)

    def _resolve_timing(self, project_id: str) -> None:
        project = self.repository.load(project_id)
        if (
            project["stages"]["timing"].get("status") == "ready"
            and len(project.get("timing", {}).get("scenes", [])) == len(project["scenes"])
        ):
            return
        self._set_stage(project_id, "timing", "running", "Resolving scene timing", 20)
        duration = float(project["narration"]["duration_seconds"])
        timing = _scene_timings(project["scenes"], duration)

        def update(value: dict[str, Any]) -> None:
            value["timing"] = {"duration_seconds": duration, "scenes": timing}
            by_id = {item["scene_id"]: item for item in timing}
            for scene in value["scenes"]:
                scene["timing"] = by_id[scene["id"]]

        self.repository.update(project_id, update)
        self._set_stage(project_id, "timing", "ready", "Scene timing resolved", 100)

    def _generate_revision(
        self,
        project_id: str,
        scene_id: str,
        *,
        candidate: bool,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.repository.load(project_id)
        scene = next((item for item in project["scenes"] if item["id"] == scene_id), None)
        if scene is None:
            raise ProjectError(f"Unknown scene: {scene_id}")
        overrides = overrides or {}
        prompt = str(overrides.get("prompt") or scene["prompt"]).strip()
        negative_prompt = str(overrides.get("negative_prompt", scene.get("negative_prompt", "")))
        revision_number = len(scene["revisions"]) + 1
        revision_id = f"r{revision_number:04d}"
        seed_value = overrides.get("seed")
        if seed_value is None:
            digest = hashlib.sha256(f"{project_id}:{scene_id}:{revision_id}".encode()).hexdigest()
            seed_value = int(digest[:8], 16)
        seed = int(seed_value)
        mode = project["configuration"]["studio_mode"]
        task, preset_id = preset_for_mode(mode)
        extension = ".mp4" if mode == "video-ltx" else ".png"
        root = self.repository.project_root(project_id)
        revision_root = root / "scenes" / scene_id / revision_id
        revision_root.mkdir(parents=True, exist_ok=True)
        output = revision_root / f"output{extension}"
        request_value = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": seed,
            "studio_mode": mode,
            "model": preset_id,
            "clip_duration_seconds": float(overrides.get("clip_duration_seconds", scene["clip_duration_seconds"])),
        }
        (revision_root / "request.json").write_text(
            json.dumps(request_value, indent=2) + "\n", encoding="utf-8"
        )
        self._set_scene(
            project_id,
            scene_id,
            status="running",
            progress=2,
            detail=f"Generating {revision_id} with {preset_id}",
        )
        if self.config.mock_pipeline:
            if mode == "video-ltx":
                self._mock_video(output, request_value["clip_duration_seconds"], seed)
            else:
                self._mock_image(output, scene_id, seed)
        else:
            preset = self.presets.get(preset_id, task=task)
            settings = build_settings(
                preset,
                prompt,
                width=1280 if mode == "video-ltx" else 1536,
                height=704 if mode == "video-ltx" else 864,
                duration_seconds=request_value["clip_duration_seconds"] if mode == "video-ltx" else None,
                negative_prompt=negative_prompt,
                seed=seed,
            )
            job = Job(task="text_to_video" if mode == "video-ltx" else "text_to_image", model=preset_id, settings=settings)
            self._active_job = self.engine.submit(job)
            while job.status not in TERMINAL:
                if self._project_cancelled(project_id):
                    self.engine.cancel(job)
                progress = job.progress or {}
                self._set_scene(
                    project_id,
                    scene_id,
                    status=job.status.value,
                    progress=int(progress.get("percent", 0)),
                    detail=str(progress.get("status") or progress.get("phase") or job.status.value),
                )
                job.wait(1.0)
            if job.status is not JobStatus.SUCCEEDED:
                raise ProductionError(job.error or f"Scene generation {job.status.value}")
            generated = next((Path(path) for path in job.files if media_type_of(path) in {"image", "video"}), None)
            if generated is None or not generated.is_file():
                raise ProductionError("WanGP generation returned no media file")
            shutil.copy2(generated, output)
            request_value["job_id"] = job.id
        if not output.is_file() or output.stat().st_size == 0:
            raise ProductionError(f"Scene {scene_id} produced no usable output")
        revision = {
            "id": revision_id,
            "status": "ready",
            "created_at": utc_stamp(),
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "seed": seed,
            "model": preset_id,
            "media_type": "video" if mode == "video-ltx" else "image",
            "path": _relative(root, output),
            "request": _relative(root, revision_root / "request.json"),
        }
        if request_value.get("job_id"):
            revision["job_id"] = request_value["job_id"]
        (revision_root / "result.json").write_text(
            json.dumps(revision, indent=2) + "\n", encoding="utf-8"
        )

        def update(value: dict[str, Any]) -> None:
            target = next(item for item in value["scenes"] if item["id"] == scene_id)
            target["revisions"].append(revision)
            target["status"] = "ready"
            target["progress"] = 100
            target["detail"] = "Candidate ready" if candidate else "Scene ready"
            if candidate:
                target["candidate_revision"] = revision_id
            else:
                target["accepted_revision"] = revision_id

        self.repository.update(project_id, update)
        self.repository.event(
            project_id,
            "scene_candidate_ready" if candidate else "scene_ready",
            f"{scene_id} {revision_id} ready",
            scene_id=scene_id,
            revision=revision_id,
        )
        return revision

    def _regenerate(self, project_id: str, payload: dict[str, Any]) -> None:
        scene_id = str(payload.pop("scene_id"))
        self.repository.update(project_id, lambda project: project.update({"status": "running", "cancel_requested": False}))
        self._generate_revision(project_id, scene_id, candidate=True, overrides=payload)
        self.repository.update(project_id, lambda project: project.update({"status": "ready", "active_operation": None}))

    def accept_revision(self, project_id: str, scene_id: str, revision_id: str) -> dict[str, Any]:
        def update(project: dict[str, Any]) -> None:
            scene = next((item for item in project["scenes"] if item["id"] == scene_id), None)
            if scene is None:
                raise ProjectError(f"Unknown scene: {scene_id}")
            if not any(item["id"] == revision_id and item["status"] == "ready" for item in scene["revisions"]):
                raise ProjectError(f"Unknown ready revision: {revision_id}")
            revision = next(item for item in scene["revisions"] if item["id"] == revision_id)
            scene["accepted_revision"] = revision_id
            scene["candidate_revision"] = None
            scene["prompt"] = revision["prompt"]
            scene["negative_prompt"] = revision.get("negative_prompt", scene.get("negative_prompt", ""))
            for name in ("composition", "checks", "render"):
                project["stages"][name].update({"status": "stale", "detail": "Accepted scene changed", "progress": 0})
            if project.get("final"):
                project["final"]["stale"] = True
            project["status"] = "ready"

        project = self.repository.update(project_id, update)
        self.repository.event(project_id, "scene_revision_accepted", f"Accepted {scene_id} {revision_id}")
        return project

    def _rebuild(self, project_id: str) -> None:
        if self._project_cancelled(project_id):
            raise ProductionError("Pipeline cancelled")
        self._compose(project_id)
        self._check(project_id)
        self._render(project_id)
        self.repository.update(
            project_id,
            lambda project: project.update(
                {"status": "complete", "active_operation": None, "cancel_requested": False}
            ),
        )
        self.repository.event(project_id, "pipeline_complete", "Final video is ready")

    def _compose(self, project_id: str) -> None:
        self._set_stage(project_id, "composition", "running", "Building HyperFrames composition", 10)
        project = self.repository.load(project_id)
        root = self.repository.project_root(project_id)
        composition = root / "composition"
        assets_dir = composition / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / project["narration"]["audio"], assets_dir / "narration.wav")
        words = json.loads((root / project["narration"]["transcript"]).read_text(encoding="utf-8"))
        scene_nodes = []
        scene_animations = []
        timings = {item["scene_id"]: item for item in project["timing"]["scenes"]}
        for index, scene in enumerate(project["scenes"]):
            accepted = scene.get("accepted_revision")
            revision = next((item for item in scene["revisions"] if item["id"] == accepted), None)
            if revision is None:
                raise ProductionError(f"Scene {scene['id']} has no accepted revision")
            source = root / revision["path"]
            destination = assets_dir / f"{scene['id']}{source.suffix.lower()}"
            shutil.copy2(source, destination)
            timing = timings[scene["id"]]
            media_id = f"media-{scene['id']}"
            if revision["media_type"] == "video":
                scene_nodes.append(
                    f'<video id="{media_id}" class="clip scene-media" '
                    f'src="assets/{html.escape(destination.name)}" '
                    f'data-start="{timing["start"]:.3f}" data-duration="{timing["duration"]:.3f}" '
                    f'data-track-index="{index % 2}" data-volume="0" muted playsinline loop preload="auto" '
                    'data-layout-allow-overflow></video>'
                )
                entrance_id = media_id
            else:
                media = (
                    f'<img id="{media_id}" src="assets/{html.escape(destination.name)}" '
                    'crossorigin="anonymous" alt="" data-layout-allow-overflow>'
                )
                scene_nodes.append(
                    f'<div id="clip-{scene["id"]}" class="clip scene" data-start="{timing["start"]:.3f}" '
                    f'data-duration="{timing["duration"]:.3f}" data-track-index="{index % 2}" '
                    f'data-layout-allow-overflow data-layout-allow-overlap><div id="shell-{scene["id"]}" '
                    f'class="media-shell" data-layout-ignore>{media}</div></div>'
                )
                entrance_id = f"shell-{scene['id']}"
            scene_animations.append(
                f'animate("{entrance_id}",[{{opacity:0}},{{opacity:1}}],'
                f'{timing["start"]:.3f},.45,"ease-out");'
            )
            if revision["media_type"] == "image":
                scale = 1.055 + (index % 3) * 0.01
                scene_animations.append(
                    f'animate("{media_id}",[{{transform:"scale(1)"}},{{transform:"scale({scale:.3f})"}}],'
                    f'{timing["start"]:.3f},{timing["duration"]:.3f},"linear");'
                )
        captions = []
        caption_animations = []
        for index, group in enumerate(_caption_groups(words)):
            duration = max(0.08, float(group["end"]) - float(group["start"]))
            captions.append(
                f'<div id="caption-{index}" class="clip caption" data-start="{group["start"]:.3f}" '
                f'data-duration="{duration:.3f}" data-track-index="20" data-layout-allow-occlusion>'
                f'<span>{html.escape(group["text"])}</span></div>'
            )
            caption_animations.append(
                f'animate("caption-{index}",[{{opacity:0,transform:"translateY(16px)"}},'
                f'{{opacity:1,transform:"translateY(0)"}}],{group["start"]:.3f},.22,"ease-out");'
            )
        duration = float(project["timing"]["duration_seconds"])
        output = composition / "index.html"
        output.write_text(
            self._composition_html(
                project["title"], duration, scene_nodes, captions, scene_animations, caption_animations
            ),
            encoding="utf-8",
        )
        self._set_stage(
            project_id,
            "composition",
            "ready",
            "Composition ready",
            100,
            path=_relative(root, output),
            fingerprint=fingerprint(
                {"scenes": [(scene["id"], scene["accepted_revision"]) for scene in project["scenes"]], "timing": project["timing"]}
            ),
        )

    @staticmethod
    def _composition_html(
        title: str,
        duration: float,
        scene_nodes: list[str],
        captions: list[str],
        scene_animations: list[str],
        caption_animations: list[str],
    ) -> str:
        return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title></head>
<body><div id="episode" data-composition-id="episode" data-start="0" data-duration="{duration:.3f}" data-width="1920" data-height="1080">
<div class="backdrop" data-layout-ignore></div>{''.join(scene_nodes)}
<div class="caption-layer" data-layout-allow-occlusion>{''.join(captions)}</div>
<audio id="narration" src="assets/narration.wav" data-start="0" data-duration="{duration:.3f}" data-track-index="30" data-volume="1"></audio>
</div><style>
html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#101827}}body{{font-family:Inter,system-ui,sans-serif}}
#episode{{position:relative;width:1920px;height:1080px;overflow:hidden;background:#101827}}.backdrop,.scene,.scene-media,.media-shell,.media-shell img{{position:absolute;inset:0;width:100%;height:100%}}
.backdrop{{background:radial-gradient(circle at 18% 8%,#32465f,#101827 55%)}}.scene,.media-shell{{overflow:hidden}}.media-shell,.scene-media{{opacity:0}}.media-shell img,.scene-media{{object-fit:cover;transform-origin:center}}
.caption-layer{{position:absolute;inset:0;z-index:1000;pointer-events:none}}.caption{{position:absolute;left:0;right:0;bottom:48px;display:flex;justify-content:center;padding:0 150px;text-align:center}}
.caption span{{max-width:1520px;color:#fff9ef;font-size:36px;line-height:1.18;font-weight:650;text-shadow:0 3px 18px rgba(8,15,28,.95)}}
</style><script>
window.__timelines=window.__timelines||{{}};window.__timelines["episode"]={{pause(){{return this}},seek(){{return this}},totalTime(){{return this}}}};
function animate(id,keyframes,delay,duration,easing){{const node=document.getElementById(id);if(!node)return;const animation=node.animate(keyframes,{{delay:delay*1000,duration:duration*1000,easing,fill:"both"}});animation.pause()}}
{''.join(scene_animations)}{''.join(caption_animations)}
</script></body></html>'''

    def _check(self, project_id: str) -> None:
        self._set_stage(project_id, "checks", "running", "Checking HyperFrames composition", 10)
        root = self.repository.project_root(project_id)
        if not self.config.mock_pipeline:
            prefix = shlex.split(self.config.hyperframes_bin)
            args = ["check", "--samples", "12", "--timeout", "120000"]
            self._set_stage(project_id, "checks", "running", "HyperFrames check", 60)
            self._run_command([*prefix, *args], cwd=root / "composition", timeout=1800)
        self._set_stage(project_id, "checks", "ready", "Composition checks passed", 100)

    def _render(self, project_id: str) -> None:
        self._set_stage(project_id, "render", "running", "Rendering final video", 5)
        project = self.repository.load(project_id)
        root = self.repository.project_root(project_id)
        revision = 1 + len(list((root / "renders").glob("cut-r*.mp4")))
        output = root / "renders" / f"cut-r{revision:04d}.mp4"
        if self.config.mock_pipeline:
            self._mock_final(output, root / project["narration"]["audio"])
        else:
            command = [
                *shlex.split(self.config.hyperframes_bin),
                "render",
                "--output",
                str(output),
                "--quality",
                project["configuration"]["render_quality"],
                "--fps",
                str(project["configuration"]["render_fps"]),
                "--workers",
                "1",
                "--strict",
            ]
            self._run_command(command, cwd=root / "composition", timeout=6 * 3600)
        media = self._probe_media(output)
        if not media["has_video"] or not media["has_audio"] or media["duration_seconds"] <= 0:
            raise ProductionError("Final media verification failed")

        def update(value: dict[str, Any]) -> None:
            value["final"] = {
                "revision": revision,
                "path": _relative(root, output),
                "stale": False,
                **media,
            }

        self.repository.update(project_id, update)
        self._set_stage(project_id, "render", "ready", "Final video ready", 100, path=_relative(root, output))

    @staticmethod
    def _run_command(command: list[str], *, cwd: Path, timeout: float) -> None:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "command failed")[-3000:]
            raise ProductionError(f"{' '.join(command[:3])} failed: {detail.strip()}")

    @staticmethod
    def _probe_media(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ProductionError(f"Missing rendered file: {path}")
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_streams", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise ProductionError(f"ffprobe failed: {completed.stderr.strip()}")
        value = json.loads(completed.stdout)
        streams = value.get("streams", [])
        return {
            "duration_seconds": round(float(value.get("format", {}).get("duration", 0)), 3),
            "has_video": any(item.get("codec_type") == "video" for item in streams),
            "has_audio": any(item.get("codec_type") == "audio" for item in streams),
        }

    @staticmethod
    def _mock_narration(source: Path, output: Path) -> None:
        word_count = len(source.read_text(encoding="utf-8").split())
        seconds = max(2.0, word_count / 2.7)
        sample_rate = 16000
        frames = int(seconds * sample_rate)
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(sample_rate)
            chunk = bytearray()
            for index in range(frames):
                amplitude = int(900 * math.sin(2 * math.pi * 180 * index / sample_rate))
                chunk.extend(amplitude.to_bytes(2, byteorder="little", signed=True))
            target.writeframes(bytes(chunk))

    @staticmethod
    def _mock_transcript(source: Path, audio: Path) -> list[dict[str, Any]]:
        text_words = source.read_text(encoding="utf-8").split()
        with wave.open(str(audio), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
        width = duration / max(1, len(text_words))
        return [
            {"id": f"w{index}", "text": word, "start": round(index * width, 3), "end": round((index + 1) * width, 3)}
            for index, word in enumerate(text_words)
        ]

    @staticmethod
    def _mock_image(output: Path, scene_id: str, seed: int) -> None:
        color = (20 + seed % 80, 32 + (seed // 3) % 80, 55 + (seed // 7) % 90)
        image = Image.new("RGB", (1280, 720), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((90, 90, 1190, 630), outline=(242, 107, 81), width=8)
        draw.text((120, 120), f"MOCK {scene_id}\nseed {seed}", fill=(255, 249, 239))
        image.save(output)

    @staticmethod
    def _mock_video(output: Path, duration: float, seed: int) -> None:
        hue = seed % 360
        completed = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                f"color=c=hsv({hue},0.55,0.25):s=640x360:r=24:d={max(1.0, min(duration, 3.0))}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            # Some ffmpeg builds do not accept hsv() color expressions.
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=#192a44:s=640x360:r=24:d={max(1.0, min(duration, 3.0))}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)],
                check=True,
            )

    @staticmethod
    def _mock_final(output: Path, narration: Path) -> None:
        completed = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=#101827:s=640x360:r=24",
                "-i", str(narration), "-map", "0:v:0", "-map", "1:a:0", "-shortest",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(output),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if completed.returncode != 0:
            raise ProductionError(f"Mock final render failed: {completed.stderr.strip()}")
