"""Generation engine: a lazy WanGP session + a single worker thread.

WanGP's `shared.api.WanGPSession` refuses to run two generations at once
(`RuntimeError: WanGP session already has a generation in progress`), and
the GPU could not serve them anyway — so the engine serializes everything
through one queue + one worker thread. The session (and the model weights
it keeps in VRAM) lives for the whole process.
"""

import logging
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .config import ServerConfig
from .jobs import Job, JobStatus, JobStore

logger = logging.getLogger("wan2gp_server")


class _JobCallbacks:
    """Bridge WanGP progress events into the Job's progress dict."""

    def __init__(self, job: Job):
        self._job = job

    def on_progress(self, update: Any) -> None:
        self._job.progress = {
            "phase": getattr(update, "phase", None),
            "status": getattr(update, "status", None),
            "percent": int(getattr(update, "progress", 0) or 0),
            "current_step": getattr(update, "current_step", None),
            "total_steps": getattr(update, "total_steps", None),
        }

    def on_status(self, text: str) -> None:
        self._job.progress = {**self._job.progress, "status": str(text or "").strip()}


class Wan2GPEngine:
    def __init__(self, config: ServerConfig, store: JobStore):
        self.config = config
        self.store = store
        self._session = None
        self._session_lock = threading.Lock()
        self._queue: "queue.Queue[Job]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._worker_lock = threading.Lock()
        self._shutdown = threading.Event()

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    @property
    def runtime_loaded(self) -> bool:
        return self._session is not None

    def ensure_session(self):
        """Get or lazily create the WanGP session (thread-safe, slow on first call)."""
        with self._session_lock:
            if self._session is None:
                root = self.config.wan2gp_root
                if not (root / "wgp.py").exists():
                    raise RuntimeError(
                        f"Wan2GP installation not found at '{root}' (wgp.py is missing). "
                        f"Set the WAN2GP_ROOT environment variable."
                    )
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))

                from shared.api import init  # WanGP official Python API

                logger.info("Initializing WanGP session (root=%s, cli_args=%s)", root, self.config.cli_args)
                init_kwargs: Dict[str, Any] = {"root": root, "cli_args": self.config.cli_args}
                if self.config.output_dir is not None:
                    self.config.output_dir.mkdir(parents=True, exist_ok=True)
                    init_kwargs["output_dir"] = self.config.output_dir
                self._session = init(**init_kwargs)
                logger.info("WanGP session ready")
            return self._session

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._worker_lock:
            if self._worker is None or not self._worker.is_alive():
                self._shutdown.clear()
                self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="wan2gp-engine")
                self._worker.start()
        if self.config.eager_init:
            threading.Thread(target=self._eager_init, daemon=True, name="wan2gp-eager-init").start()

    def _eager_init(self) -> None:
        try:
            self.ensure_session()
        except Exception:
            logger.exception("Eager WanGP session init failed (will retry on first job)")

    def stop(self) -> None:
        self._shutdown.set()

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job._cancel_requested:
                job.finish(JobStatus.CANCELLED, error="Cancelled while queued")
                continue
            self._run_job(job)

    def _run_job(self, job: Job) -> None:
        import time

        job.status = JobStatus.RUNNING
        job.started_at = time.time()
        job.progress = {"phase": "loading_model", "status": "Preparing generation...", "percent": 0}
        logger.info(
            "Job %s started: task=%s model_type=%s resolution=%s video_length=%s",
            job.id, job.task, job.settings.get("model_type"),
            job.settings.get("resolution"), job.settings.get("video_length", "-"),
        )
        try:
            if job.task == "concatenate":
                self._run_concatenation(job)
                return
            session = self.ensure_session()
            session_job = session.submit_task(dict(job.settings), callbacks=_JobCallbacks(job))
            job._session_job = session_job
            if job._cancel_requested:  # cancel arrived between queue pop and submit
                session_job.cancel()
            result = session_job.result()

            if getattr(result, "cancelled", False) or job._cancel_requested:
                job.finish(JobStatus.CANCELLED, files=list(result.generated_files), error="Cancelled")
            elif result.success:
                if job.task == "preload":
                    # Warmup output is a throwaway: the point was downloading
                    # the checkpoint and loading the weights into VRAM.
                    for path in result.generated_files:
                        try:
                            Path(path).unlink(missing_ok=True)
                        except OSError:
                            logger.warning("Could not remove warmup output %s", path)
                    job.finish(JobStatus.SUCCEEDED)
                    logger.info("Job %s succeeded: model '%s' preloaded", job.id, job.model)
                elif not result.generated_files:
                    job.finish(JobStatus.FAILED, error="Generation completed but produced no output files")
                else:
                    job.finish(JobStatus.SUCCEEDED, files=list(result.generated_files))
                    logger.info("Job %s succeeded: %s", job.id, job.files)
            else:
                messages = "; ".join(e.message for e in result.errors) or "Unknown generation error"
                job.finish(JobStatus.FAILED, files=list(result.generated_files), error=messages)
                logger.warning("Job %s failed: %s", job.id, messages)
        except Exception as exc:
            logger.exception("Job %s crashed", job.id)
            job.finish(JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        finally:
            job._session_job = None

    def _run_concatenation(self, job: Job) -> None:
        """Normalize scene clips and join them into a downloadable MP4.

        Normalization is intentional: scenes may use different generation
        ratios or resolutions, while FFmpeg's concat demuxer requires matching
        streams. Clips are letterboxed onto one canvas and receive silent
        stereo audio when the source has no audio track.
        """
        source_paths = [Path(p) for p in job.settings.get("_source_paths", [])]
        output_path = Path(job.settings["_output_path"])
        width, height = [int(v) for v in job.settings["resolution"].split("x", 1)]
        fps = int(job.settings.get("fps", 24))

        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("FFmpeg and ffprobe are required for video concatenation")
        if len(source_paths) < 2 or any(not p.is_file() for p in source_paths):
            raise RuntimeError("Two or more existing source videos are required")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="wan2gp-join-", dir=str(self.config.data_dir)) as tmp:
            tmp_dir = Path(tmp)
            normalized = []
            count = len(source_paths)
            for index, source in enumerate(source_paths):
                if job._cancel_requested:
                    job.finish(JobStatus.CANCELLED, error="Concatenation cancelled")
                    return
                job.progress = {
                    "phase": "normalizing_clips",
                    "status": f"Preparing scene {index + 1} of {count}",
                    "percent": 5 + int(70 * index / count),
                    "current_step": index + 1,
                    "total_steps": count + 1,
                }
                audio_probe = subprocess.run(
                    [ffprobe, "-v", "error", "-select_streams", "a:0",
                     "-show_entries", "stream=index", "-of", "csv=p=0", str(source)],
                    capture_output=True, text=True, check=False,
                )
                has_audio = bool(audio_probe.stdout.strip())
                part = tmp_dir / f"part-{index:03d}.mp4"
                video_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                    f"setsar=1,fps={fps},format=yuv420p"
                )
                command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
                if has_audio:
                    command += [
                        "-map", "0:v:0", "-map", "0:a:0", "-vf", video_filter,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-c:a", "aac", "-ar", "48000", "-ac", "2",
                    ]
                else:
                    command += [
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-map", "0:v:0", "-map", "1:a:0", "-vf", video_filter,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest",
                    ]
                command += ["-movflags", "+faststart", str(part)]
                completed = subprocess.run(command, capture_output=True, text=True, check=False)
                if completed.returncode != 0:
                    raise RuntimeError(
                        f"FFmpeg could not normalize scene {index + 1}: {completed.stderr[-1200:]}")
                normalized.append(part)

            job.progress = {
                "phase": "concatenating",
                "status": f"Joining {count} scenes",
                "percent": 82,
                "current_step": count + 1,
                "total_steps": count + 1,
            }
            concat_list = tmp_dir / "clips.txt"
            concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in normalized))
            completed = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "concat", "-safe", "0", "-i", str(concat_list),
                 "-c", "copy", "-movflags", "+faststart", str(output_path)],
                capture_output=True, text=True, check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"FFmpeg concat failed: {completed.stderr[-1200:]}")

        job.progress = {
            "phase": "complete",
            "status": "Assembly ready",
            "percent": 100,
            "current_step": count + 1,
            "total_steps": count + 1,
        }
        job.finish(JobStatus.SUCCEEDED, files=[str(output_path)])
        logger.info("Job %s succeeded: concatenated %d scenes", job.id, count)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, job: Job) -> Job:
        self.store.add(job)
        self._queue.put(job)
        return job

    def cancel(self, job: Job) -> Job:
        job._cancel_requested = True
        if job.status is JobStatus.RUNNING and job._session_job is not None:
            try:
                job._session_job.cancel()  # cooperative; worker observes the result
            except Exception:
                logger.exception("Cancellation of job %s raised", job.id)
        # Queued jobs are finalized by the worker when popped; nothing else to do.
        return job
