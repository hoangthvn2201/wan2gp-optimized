from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from wan2gp_server.config import ServerConfig
from wan2gp_server.narration import chunk_text
from wan2gp_server.production import ProductionCoordinator
from wan2gp_server.projects import ProjectRepository
from wan2gp_server.scene_plan import InputValidationError, validate_scene_plan


PLAN = {
    "title": "Why the Checkout Total Changes",
    "visual_concept": "The Hidden Layer",
    "aspect_ratio": "16:9",
    "continuity": {
        "character": "A thoughtful adult shopper",
        "anchor_object_immutable": "A cream smartphone",
        "anchor_object_states": ["offer", "decision", "comparison"],
        "style": "Premium editorial illustration",
    },
    "scenes": [
        {
            "id": "scene-001",
            "narrative_role": "familiar-situation",
            "script_reference": "A simple offer appears.",
            "prompt": "A thoughtful shopper studies a simple offer on a cream phone.",
            "motion": "The camera slowly approaches.",
            "duration_weight": 1,
        },
        {
            "id": "scene-002",
            "narrative_role": "agency-reversal",
            "script_reference": "The shopper compares complete totals.",
            "prompt": "The shopper calmly compares two complete totals.",
            "duration_weight": 1.2,
        },
    ],
}

VOICEOVER = (
    "A simple price can feel complete before the checkout reveals its full shape. "
    "The useful response is to define the complete purchase first, compare final totals, "
    "and choose only after every required charge is visible."
)


class FakeEngine:
    def submit(self, job):  # pragma: no cover - mock pipeline bypasses WanGP
        raise AssertionError("mock pipeline must not submit GPU work")

    def cancel(self, job):
        return job


class FakePresets:
    def get(self, preset_id, task=None):  # pragma: no cover
        raise AssertionError("mock pipeline must not resolve a real preset")


def wait_for(repository: ProjectRepository, project_id: str, predicate, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        project = repository.load(project_id)
        if predicate(project):
            return project
        time.sleep(0.1)
    raise AssertionError(f"project did not reach expected state: {repository.load(project_id)}")


class ScenePlanTests(unittest.TestCase):
    def test_long_narration_is_chunked_without_losing_words(self):
        text = " ".join(f"word-{index}" for index in range(725))
        chunks = chunk_text(text, max_words=300)
        self.assertEqual([len(chunk.split()) for chunk in chunks], [300, 300, 125])
        self.assertEqual(" ".join(chunks), text)

    def test_plan_normalizes_optional_generation_values(self):
        plan = validate_scene_plan(PLAN)
        self.assertEqual(plan["scenes"][0]["duration_weight"], 1.0)
        self.assertEqual(plan["scenes"][0]["clip_duration_seconds"], 8.0)
        self.assertTrue(plan["scenes"][0]["negative_prompt"])

    def test_duplicate_scene_ids_are_rejected(self):
        duplicate = json.loads(json.dumps(PLAN))
        duplicate["scenes"][1]["id"] = duplicate["scenes"][0]["id"]
        with self.assertRaises(InputValidationError):
            validate_scene_plan(duplicate)


class MockEndToEndTests(unittest.TestCase):
    def test_project_runs_regenerates_and_rebuilds(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            config = ServerConfig(data_dir=data, mock_pipeline=True, studio_mode="image-z")
            repository = ProjectRepository(config.projects_dir)
            project = repository.create(
                PLAN,
                VOICEOVER,
                studio_mode="image-z",
                voice="am_adam",
                speed=1.0,
                render_quality="draft",
                render_fps=24,
            )
            coordinator = ProductionCoordinator(config, repository, FakeEngine(), FakePresets())
            coordinator.start()
            try:
                coordinator.submit_run(project["id"])
                completed = wait_for(repository, project["id"], lambda value: value["status"] == "complete")
                self.assertTrue(all(scene["accepted_revision"] == "r0001" for scene in completed["scenes"]))
                self.assertTrue((repository.project_root(project["id"]) / completed["final"]["path"]).is_file())
                self.assertTrue(completed["final"]["has_audio"])
                self.assertTrue(completed["final"]["has_video"])

                coordinator.submit_regeneration(
                    project["id"], "scene-001", {"prompt": "A stronger alternate composition"}
                )
                candidate = wait_for(
                    repository,
                    project["id"],
                    lambda value: value["scenes"][0].get("candidate_revision") == "r0002",
                )
                self.assertEqual(candidate["scenes"][0]["accepted_revision"], "r0001")
                coordinator.accept_revision(project["id"], "scene-001", "r0002")
                stale = repository.load(project["id"])
                self.assertTrue(stale["final"]["stale"])
                coordinator.submit_rebuild(project["id"])
                rebuilt = wait_for(
                    repository,
                    project["id"],
                    lambda value: value["status"] == "complete" and value["final"].get("revision") == 2,
                )
                self.assertFalse(rebuilt["final"]["stale"])
                self.assertEqual(rebuilt["scenes"][0]["accepted_revision"], "r0002")
            finally:
                coordinator.stop()


if __name__ == "__main__":
    unittest.main()
