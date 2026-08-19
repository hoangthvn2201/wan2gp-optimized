"""Validation and normalization for Episode Studio inputs."""

from __future__ import annotations

import copy
import re
from typing import Any


SCENE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
SUPPORTED_MODES = {"image-z", "image-ltx", "video-ltx"}
DEFAULT_NEGATIVE_PROMPT = (
    "clutter, unreadable microtext, stock-vector look, childish proportions, "
    "real logos, copied interfaces, watermark"
)


class InputValidationError(ValueError):
    """A user-actionable source-file validation error."""


def validate_voiceover(text: str) -> str:
    text = text.strip()
    if len(text.split()) < 20:
        raise InputValidationError("Voiceover must contain at least 20 words")
    banned = ("http://", "https://", "<speak", "KOKORO VOICEOVER TEXT START")
    found = [token for token in banned if token.casefold() in text.casefold()]
    if found:
        raise InputValidationError(
            "Voiceover must contain only spoken prose; found: " + ", ".join(found)
        )
    if re.search(r"(?m)^\s*(?:#{1,6}|[-*])\s+", text):
        raise InputValidationError("Voiceover must not contain Markdown headings or lists")
    return text


def _required_text(value: dict[str, Any], field: str, context: str) -> str:
    text = str(value.get(field, "")).strip()
    if not text:
        raise InputValidationError(f"{context} is missing {field}")
    return text


def validate_scene_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputValidationError("Scene plan must be a JSON object")
    plan = copy.deepcopy(value)
    _required_text(plan, "title", "Scene plan")
    _required_text(plan, "visual_concept", "Scene plan")
    if plan.get("aspect_ratio", "16:9") != "16:9":
        raise InputValidationError("Episode Studio currently requires a 16:9 scene plan")

    continuity = plan.get("continuity")
    if not isinstance(continuity, dict):
        raise InputValidationError("Scene plan continuity object is required")
    for field in ("character", "anchor_object_immutable", "style"):
        _required_text(continuity, field, "Scene plan continuity")
    states = continuity.get("anchor_object_states")
    if not isinstance(states, list) or not states or not all(str(item).strip() for item in states):
        raise InputValidationError("continuity.anchor_object_states must contain text values")

    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise InputValidationError("Scene plan must contain at least one scene")
    ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(scenes, 1):
        if not isinstance(source, dict):
            raise InputValidationError(f"Scene {index} must be an object")
        scene = copy.deepcopy(source)
        scene_id = str(scene.get("id", "")).strip()
        if not SCENE_ID_RE.fullmatch(scene_id) or scene_id in ids:
            raise InputValidationError(f"Scene {index} has an invalid or duplicate id: {scene_id}")
        ids.add(scene_id)
        for field in ("narrative_role", "script_reference", "prompt"):
            _required_text(scene, field, f"Scene {scene_id}")
        try:
            weight = float(scene.get("duration_weight", 1.0))
        except (TypeError, ValueError) as exc:
            raise InputValidationError(f"Scene {scene_id} duration_weight must be numeric") from exc
        if weight <= 0:
            raise InputValidationError(f"Scene {scene_id} duration_weight must be positive")
        try:
            clip_duration = float(scene.get("clip_duration_seconds", 8.0))
        except (TypeError, ValueError) as exc:
            raise InputValidationError(f"Scene {scene_id} clip_duration_seconds must be numeric") from exc
        if not 0 < clip_duration <= 30:
            raise InputValidationError(
                f"Scene {scene_id} clip_duration_seconds must be between 0 and 30"
            )
        scene["duration_weight"] = weight
        scene["clip_duration_seconds"] = clip_duration
        scene.setdefault("negative_prompt", DEFAULT_NEGATIVE_PROMPT)
        normalized.append(scene)
    plan["aspect_ratio"] = "16:9"
    plan["scenes"] = normalized
    return plan


def resolve_prompt(scene: dict[str, Any], studio_mode: str) -> str:
    if studio_mode not in SUPPORTED_MODES:
        raise InputValidationError(f"Unknown studio mode: {studio_mode}")
    if studio_mode == "video-ltx":
        explicit = str(scene.get("video_prompt", "")).strip()
        if explicit:
            return explicit
        prompt = str(scene["prompt"]).strip()
        motion = str(scene.get("motion", "")).strip()
        return f"{prompt}\nMotion: {motion}" if motion else prompt
    return str(scene.get("image_prompt") or scene["prompt"]).strip()


def preset_for_mode(studio_mode: str) -> tuple[str, str]:
    """Return (task, preset id) for a project mode."""
    mapping = {
        "image-z": ("t2i", "z-image-turbo"),
        "image-ltx": ("t2i", "ltx25-distilled-image"),
        "video-ltx": ("t2v", "ltx25-distilled"),
    }
    try:
        return mapping[studio_mode]
    except KeyError as exc:
        raise InputValidationError(f"Unknown studio mode: {studio_mode}") from exc

