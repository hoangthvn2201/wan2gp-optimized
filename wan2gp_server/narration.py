"""Long-form Kokoro synthesis using HyperFrames' model cache."""

from __future__ import annotations

import hashlib
import json
import os
import wave
from pathlib import Path
from typing import Callable


def _usable_wav(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1000:
        return False
    try:
        with wave.open(str(path), "rb") as source:
            return source.getnframes() > 0 and source.getframerate() > 0
    except (OSError, EOFError, wave.Error):
        return False


def chunk_text(text: str, max_words: int = 350) -> list[str]:
    """Group natural paragraphs into Kokoro-safe long-form segments."""
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for paragraph in paragraphs:
        words = paragraph.split()
        if len(words) > max_words:
            if current:
                chunks.append("\n\n".join(current))
                current, current_words = [], 0
            for start in range(0, len(words), max_words):
                chunks.append(" ".join(words[start : start + max_words]))
            continue
        if current and current_words + len(words) > max_words:
            chunks.append("\n\n".join(current))
            current, current_words = [], 0
        current.append(paragraph)
        current_words += len(words)
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text.strip()]


def _backend():
    cache_root = Path(
        os.environ.get(
            "WAN2GP_KOKORO_CACHE_DIR",
            Path.home() / ".cache" / "hyperframes" / "tts",
        )
    ).expanduser()
    model_path = cache_root / "models" / "kokoro-v1.0.onnx"
    voices_path = cache_root / "voices" / "voices-v1.0.bin"
    missing = [str(path) for path in (model_path, voices_path) if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Kokoro model cache is incomplete; run `hyperframes tts` once. Missing: "
            + ", ".join(missing)
        )
    try:
        import soundfile
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        raise RuntimeError("Install kokoro-onnx and soundfile before generating narration") from exc
    return Kokoro(str(model_path), str(voices_path)), soundfile


def _concatenate(segments: list[Path], destination: Path) -> None:
    temporary = destination.with_suffix(".assembling.wav")
    parameters = None
    try:
        with wave.open(str(temporary), "wb") as output:
            for segment in segments:
                with wave.open(str(segment), "rb") as source:
                    current = (
                        source.getnchannels(),
                        source.getsampwidth(),
                        source.getframerate(),
                        source.getcomptype(),
                    )
                    if parameters is None:
                        parameters = current
                        output.setnchannels(current[0])
                        output.setsampwidth(current[1])
                        output.setframerate(current[2])
                        output.setcomptype(current[3], source.getcompname())
                    elif current != parameters:
                        raise RuntimeError("Kokoro WAV chunks have incompatible formats")
                    output.writeframes(source.readframes(source.getnframes()))
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def synthesize_longform(
    text: str,
    output: Path,
    *,
    voice: str,
    speed: float,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    chunks = chunk_text(text)
    cache_key = hashlib.sha256(
        json.dumps(
            {"text": text, "voice": voice, "speed": speed, "version": 1},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:20]
    segment_root = output.parent / ".kokoro-cache" / cache_key
    segment_root.mkdir(parents=True, exist_ok=True)
    segments = [segment_root / f"chunk-{index:03d}.wav" for index in range(len(chunks))]
    pending = [index for index, path in enumerate(segments) if not _usable_wav(path)]
    if pending:
        model, soundfile = _backend()
        for index in pending:
            if progress:
                progress(index + 1, len(chunks))
            temporary = segments[index].with_suffix(".generating.wav")
            temporary.unlink(missing_ok=True)
            try:
                samples, sample_rate = model.create(chunks[index], voice=voice, speed=speed)
                soundfile.write(temporary, samples, sample_rate)
                if not _usable_wav(temporary):
                    raise RuntimeError("Kokoro produced an invalid WAV chunk")
                temporary.replace(segments[index])
            finally:
                temporary.unlink(missing_ok=True)
    _concatenate(segments, output)
    if not _usable_wav(output):
        raise RuntimeError("Kokoro did not produce a usable narration WAV")
    return output

