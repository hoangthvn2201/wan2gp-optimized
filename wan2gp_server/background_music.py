"""Deterministic, low-memory ambient scoring and narration-safe final mixing."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


SAMPLE_RATE = 48_000
CHUNK_SECONDS = 2.0
SUPPORTED_STYLES = {"editorial", "gentle"}

_CHORDS = (
    ((45, 52, 57, 61, 64), (42, 49, 54, 57, 61)),
    ((45, 52, 57, 60, 64), (44, 51, 56, 59, 63)),
    ((40, 47, 52, 55, 59), (42, 49, 54, 57, 61)),
    ((38, 45, 50, 54, 57), (40, 47, 52, 55, 59)),
    ((41, 48, 53, 56, 60), (38, 45, 50, 53, 57)),
    ((45, 52, 57, 61, 64), (47, 54, 59, 62, 66)),
)
_ROLE_HINTS = (
    ("familiar", "opening", "setup", "surface"),
    ("decision", "attention", "question", "choice"),
    ("hidden", "mechanism", "reveal", "definition", "math"),
    ("business", "gain", "incentive", "system"),
    ("boundary", "consequence", "cost", "risk"),
    ("agency", "resolution", "defense", "action", "closing"),
)


class BackgroundMusicError(RuntimeError):
    """A user-actionable score generation or mixing failure."""


def _midi_hz(note: float) -> float:
    return 440.0 * 2.0 ** ((note - 69.0) / 12.0)


def _role_family(role: str) -> int:
    normalized = role.casefold()
    for index, hints in enumerate(_ROLE_HINTS):
        if any(hint in normalized for hint in hints):
            return index
    return int(hashlib.sha256(normalized.encode()).hexdigest()[:4], 16) % len(_CHORDS)


def _section_starts(
    scenes: list[dict[str, Any]], timings: list[dict[str, Any]]
) -> list[tuple[float, int]]:
    roles = {scene["id"]: _role_family(str(scene["narrative_role"])) for scene in scenes}
    starts: list[tuple[float, int]] = []
    previous: int | None = None
    for timing in timings:
        family = roles[str(timing["scene_id"])]
        if family != previous:
            starts.append((float(timing["start"]), family))
            previous = family
    return starts or [(0.0, 0)]


def _chord_signal(
    notes: tuple[int, ...],
    t: np.ndarray,
    local: np.ndarray,
    *,
    gentle: bool,
) -> np.ndarray:
    left = np.zeros(len(t), dtype=np.float64)
    right = np.zeros(len(t), dtype=np.float64)
    swell_period = 27.0 if gentle else 19.0
    swell = 0.82 + 0.09 * np.sin(2 * np.pi * local / swell_period)
    harmonic = 0.055 if gentle else 0.13
    for index, note in enumerate(notes):
        frequency = _midi_hz(note)
        phase = 2 * np.pi * frequency * t + 0.0012 * np.sin(
            2 * np.pi * (0.035 + index * 0.007) * t
        )
        voice = np.sin(phase) + harmonic * np.sin(phase * 2 + 0.4)
        pan = index / max(1, len(notes) - 1)
        left += voice * (0.92 - 0.30 * pan)
        right += voice * (0.62 + 0.30 * pan)
    amount = 0.255 if gentle else 0.36
    return np.column_stack((left, right)) * (amount * swell / len(notes))[:, None]


def _section_signal(
    family: int,
    t: np.ndarray,
    section_start: float,
    style: str,
) -> np.ndarray:
    gentle = style == "gentle"
    local = np.maximum(0.0, t - section_start)
    chords = _CHORDS[family]
    chord_seconds = 24.0 if gentle else 15.0
    chord_index = np.floor(local / chord_seconds).astype(int)
    chord_phase = np.mod(local, chord_seconds)
    signal = np.zeros((len(t), 2), dtype=np.float64)
    blend_start = chord_seconds - (5.0 if gentle else 3.2)
    for index, chord in enumerate(chords):
        mask = chord_index % len(chords) == index
        if not np.any(mask):
            continue
        current = _chord_signal(chord, t[mask], local[mask], gentle=gentle)
        upcoming = _chord_signal(
            chords[(index + 1) % len(chords)], t[mask], local[mask], gentle=gentle
        )
        blend = np.clip(
            (chord_phase[mask] - blend_start) / (chord_seconds - blend_start),
            0.0,
            1.0,
        )[:, None]
        signal[mask] = (
            current * np.cos(blend * np.pi / 2)
            + upcoming * np.sin(blend * np.pi / 2)
        )

    accent_seconds = 6.0 if gentle else 60.0 / (66 + family * 2)
    accent_phase = np.mod(local, accent_seconds)
    decay = np.exp(-accent_phase * (1.7 if gentle else 4.5))
    accent_note = chords[0][3] + 12
    accent = np.sin(2 * np.pi * _midi_hz(accent_note) * accent_phase) * decay
    accent *= 0.010 if gentle else 0.020
    signal[:, 0] += accent * 0.84
    signal[:, 1] += accent * 0.92
    if not gentle:
        root = chords[0][0] - 12
        bass = np.sin(2 * np.pi * _midi_hz(root) * t) * 0.042
        signal += bass[:, None]
    return signal


def generate_ambient_score(
    output: Path,
    *,
    duration: float,
    scenes: list[dict[str, Any]],
    timings: list[dict[str, Any]],
    style: str = "editorial",
    seed_key: str = "episode-studio",
) -> Path:
    """Generate an exact-length stereo ambient WAV without holding it all in RAM."""
    if style not in SUPPORTED_STYLES:
        raise BackgroundMusicError(
            f"Unknown background music style: {style}. Choose editorial or gentle"
        )
    if duration <= 0:
        raise BackgroundMusicError("Background music requires a positive duration")
    starts = _section_starts(scenes, timings)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".assembling.wav")
    total_samples = int(round(duration * SAMPLE_RATE))
    chunk_samples = int(CHUNK_SECONDS * SAMPLE_RATE)
    seed = int(hashlib.sha256(seed_key.encode()).hexdigest()[:16], 16)
    rng = np.random.default_rng(seed)
    try:
        with sf.SoundFile(
            temporary,
            mode="w",
            samplerate=SAMPLE_RATE,
            channels=2,
            subtype="PCM_16",
        ) as target:
            for first in range(0, total_samples, chunk_samples):
                last = min(total_samples, first + chunk_samples)
                t = np.arange(first, last, dtype=np.float64) / SAMPLE_RATE
                section_index = max(
                    index for index, (start, _) in enumerate(starts) if start <= t[0]
                )
                section_start, family = starts[section_index]
                signal = _section_signal(family, t, section_start, style)
                if section_index + 1 < len(starts):
                    next_start, next_family = starts[section_index + 1]
                    blend = np.clip((t - (next_start - 4.0)) / 4.0, 0.0, 1.0)[:, None]
                    if np.any(blend > 0):
                        upcoming = _section_signal(next_family, t, next_start, style)
                        signal = (
                            signal * np.cos(blend * np.pi / 2)
                            + upcoming * np.sin(blend * np.pi / 2)
                        )
                noise = rng.normal(0.0, 1.0, len(t) + 192)
                air = np.convolve(noise, np.ones(193) / 193, mode="valid")
                signal[:, 0] += air * (0.004 if style == "gentle" else 0.009)
                signal[:, 1] += np.roll(air, 47) * (
                    0.004 if style == "gentle" else 0.009
                )
                envelope = np.clip(t / 5.0, 0.0, 1.0) * np.clip(
                    (duration - t) / 8.0, 0.0, 1.0
                )
                gain = 0.44 if style == "gentle" else 0.54
                target.write(
                    (np.tanh(signal * 1.22) * gain * envelope[:, None]).astype(
                        np.float32
                    )
                )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def mix_ambient_score(
    video: Path,
    music: Path,
    output: Path,
    *,
    duration: float,
    volume: float = 0.22,
) -> Path:
    """Mix ambient music under existing narration while copying the video stream."""
    if not 0 <= volume <= 1:
        raise BackgroundMusicError("Background music volume must be between 0 and 1")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".assembling.mp4")
    fade_duration = min(8.0, duration)
    fade_start = max(0.0, duration - fade_duration)
    filter_graph = (
        f"[1:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,volume={volume:g},"
        f"afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}[bgm];"
        "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.97[aout]"
    )
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-i",
            str(music),
            "-filter_complex",
            filter_graph,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(temporary),
        ],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise BackgroundMusicError(
            f"FFmpeg could not mix background music: {completed.stderr[-2000:]}"
        )
    temporary.replace(output)
    return output
