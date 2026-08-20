"""Durable filesystem-backed projects for Episode Studio."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .scene_plan import SUPPORTED_MODES, resolve_prompt, validate_scene_plan, validate_voiceover


class ProjectError(RuntimeError):
    pass


def utc_stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:42]
    return slug or "episode"


class ProjectRepository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def lock(self, project_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(project_id, threading.RLock())

    def project_root(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,79}", project_id):
            raise ProjectError("Invalid project id")
        path = (self.root / project_id).resolve()
        if self.root not in path.parents:
            raise ProjectError("Project path escapes the data directory")
        return path

    def _manifest_path(self, project_id: str) -> Path:
        return self.project_root(project_id) / "project.json"

    def create(
        self,
        plan_value: Any,
        voiceover_text: str,
        *,
        studio_mode: str,
        voice: str,
        speed: float,
        render_quality: str,
        render_fps: int,
        add_background_music: bool = False,
        background_music_style: str = "editorial",
        background_music_volume: float = 0.22,
    ) -> dict[str, Any]:
        plan = validate_scene_plan(plan_value)
        voiceover = validate_voiceover(voiceover_text)
        if studio_mode not in SUPPORTED_MODES:
            raise ProjectError(f"Unknown studio mode: {studio_mode}")
        if not 0.7 <= speed <= 1.2:
            raise ProjectError("Kokoro speed must be between 0.7 and 1.2")
        if render_quality not in {"draft", "standard", "high"}:
            raise ProjectError("Render quality must be draft, standard, or high")
        if render_fps not in {24, 25, 30, 50, 60}:
            raise ProjectError("Render FPS must be 24, 25, 30, 50, or 60")
        if background_music_style not in {"editorial", "gentle"}:
            raise ProjectError("Background music style must be editorial or gentle")
        if not 0 <= background_music_volume <= 1:
            raise ProjectError("Background music volume must be between 0 and 1")

        project_id = f"{_slug(plan['title'])}-{uuid.uuid4().hex[:8]}"
        root = self.project_root(project_id)
        for relative in (
            "source",
            "narration",
            "scenes",
            "composition/assets",
            "background-music",
            "renders",
            "reports",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)
        (root / "source" / "scene-plan.json").write_text(
            json.dumps(plan, indent=2) + "\n", encoding="utf-8"
        )
        (root / "source" / "voiceover-source.txt").write_text(
            voiceover + "\n", encoding="utf-8"
        )

        created = utc_stamp()
        stages = {
            name: {"status": "pending", "progress": 0, "detail": "Waiting"}
            for name in (
                "inputs",
                "narration",
                "transcription",
                "timing",
                "assets",
                "composition",
                "checks",
                "render",
                "music",
            )
        }
        stages["inputs"] = {"status": "ready", "progress": 100, "detail": "Sources validated"}
        scenes = []
        for scene in plan["scenes"]:
            scenes.append(
                {
                    "id": scene["id"],
                    "narrative_role": scene["narrative_role"],
                    "script_reference": scene["script_reference"],
                    "prompt": resolve_prompt(scene, studio_mode),
                    "negative_prompt": scene.get("negative_prompt", ""),
                    "duration_weight": scene["duration_weight"],
                    "clip_duration_seconds": scene["clip_duration_seconds"],
                    "status": "pending",
                    "progress": 0,
                    "detail": "Waiting",
                    "accepted_revision": None,
                    "candidate_revision": None,
                    "revisions": [],
                }
            )
        manifest = {
            "schema_version": 1,
            "id": project_id,
            "title": plan["title"],
            "visual_concept": plan["visual_concept"],
            "status": "queued",
            "created_at": created,
            "updated_at": created,
            "configuration": {
                "studio_mode": studio_mode,
                "voice": voice,
                "speed": speed,
                "render_quality": render_quality,
                "render_fps": render_fps,
                "add_background_music": bool(add_background_music),
                "background_music_style": background_music_style,
                "background_music_volume": background_music_volume,
            },
            "source": {
                "scene_plan": "source/scene-plan.json",
                "voiceover": "source/voiceover-source.txt",
                "fingerprint": fingerprint({"plan": plan, "voiceover": voiceover}),
            },
            "stages": stages,
            "scenes": scenes,
            "narration": {},
            "background_music": {},
            "timing": {},
            "final": {},
            "active_operation": None,
            "cancel_requested": False,
            "event_sequence": 0,
        }
        self.save(manifest)
        self.event(project_id, "project_created", "Project accepted and queued")
        return self.load(project_id)

    def load(self, project_id: str) -> dict[str, Any]:
        path = self._manifest_path(project_id)
        if not path.is_file():
            raise ProjectError(f"Project not found: {project_id}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"Could not read project: {project_id}") from exc

    def save(self, manifest: dict[str, Any]) -> None:
        project_id = str(manifest["id"])
        with self.lock(project_id):
            manifest["updated_at"] = utc_stamp()
            path = self._manifest_path(project_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)

    def update(self, project_id: str, operation: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self.lock(project_id):
            manifest = self.load(project_id)
            operation(manifest)
            self.save(manifest)
            return manifest

    def event(self, project_id: str, kind: str, message: str, **details: Any) -> dict[str, Any]:
        with self.lock(project_id):
            manifest = self.load(project_id)
            sequence = int(manifest.get("event_sequence", 0)) + 1
            manifest["event_sequence"] = sequence
            self.save(manifest)
            event = {
                "sequence": sequence,
                "at": utc_stamp(),
                "kind": kind,
                "message": message,
                **details,
            }
            with (self.project_root(project_id) / "events.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps(event, separators=(",", ":")) + "\n")
            return event

    def list(self) -> list[dict[str, Any]]:
        projects = []
        for path in sorted(self.root.glob("*/project.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                projects.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return projects

    def events(self, project_id: str, after: int = 0) -> list[dict[str, Any]]:
        path = self.project_root(project_id) / "events.jsonl"
        if not path.is_file():
            return []
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(event.get("sequence", 0)) > after:
                result.append(event)
        return result

    def artifact(self, project_id: str, relative: str) -> Path:
        root = self.project_root(project_id)
        path = (root / relative).resolve()
        if root != path and root not in path.parents:
            raise ProjectError("Artifact path escapes the project directory")
        if not path.is_file():
            raise ProjectError("Artifact not found")
        return path

    def recover_interrupted(self) -> None:
        for manifest in self.list():
            if manifest.get("status") not in {"running", "queued"}:
                continue
            manifest["status"] = "interrupted"
            manifest["active_operation"] = None
            for stage in manifest.get("stages", {}).values():
                if stage.get("status") in {"running", "queued"}:
                    stage["status"] = "interrupted"
                    stage["detail"] = "Server restarted; resume to continue"
            for scene in manifest.get("scenes", []):
                if scene.get("status") in {"running", "queued"}:
                    scene["status"] = "interrupted"
                    scene["detail"] = "Server restarted; resume to continue"
            self.save(manifest)
