"""Input image/video handling: uploads, base64, URLs and server paths.

Every input image ends up as a validated file under the server's data
directory (except `image_path`, which is used in place), and the absolute
path is what gets passed to WanGP as `image_start`.
"""

import base64
import binascii
import io
import re
import urllib.request
import uuid
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

_DATA_URI_RE = re.compile(r"^data:image/[\w.+-]+;base64,", re.IGNORECASE)
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
_MAX_VIDEO_BYTES = 500 * 1024 * 1024


class ImageInputError(ValueError):
    """Raised when an input image cannot be resolved/decoded."""


class VideoInputError(ValueError):
    """Raised when an input video cannot be resolved."""


def _validate_and_save(data: bytes, dest_dir: Path, stem: str) -> Tuple[Path, Tuple[int, int]]:
    """Validate bytes as an image with PIL and save them under dest_dir."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            fmt = (img.format or "PNG").lower()
            size = img.size
    except Exception as exc:
        raise ImageInputError(f"Input is not a decodable image: {exc}") from exc

    ext = {"jpeg": ".jpg"}.get(fmt, f".{fmt}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{stem}{ext}"
    path.write_bytes(data)
    return path, size


def _looks_like_video(data: bytes, suffix: str) -> bool:
    if suffix not in _VIDEO_EXTS or not data:
        return False
    if suffix in {".mp4", ".mov"}:
        return len(data) >= 12 and data[4:8] == b"ftyp"
    if suffix in {".webm", ".mkv"}:
        return data[:4] == b"\x1aE\xdf\xa3"
    if suffix == ".avi":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"AVI "
    return False


def save_upload(
    data: bytes,
    assets_dir: Path,
    original_name: Optional[str] = None,
) -> Tuple[str, Path, Optional[Tuple[int, int]], str]:
    """Save an uploaded image or video.

    Returns ``(asset_id, path, image_size_or_none, media_type)``.
    """
    asset_id = uuid.uuid4().hex[:12]
    try:
        path, size = _validate_and_save(data, assets_dir, asset_id)
        return asset_id, path, size, "image"
    except ImageInputError:
        suffix = Path(original_name or "").suffix.lower()
        if len(data) > _MAX_VIDEO_BYTES:
            raise VideoInputError("Uploaded video exceeds the 500 MB limit")
        if not _looks_like_video(data, suffix):
            raise VideoInputError(
                "Upload must be a decodable image or MP4, MOV, WebM, MKV, or AVI video")
        assets_dir.mkdir(parents=True, exist_ok=True)
        path = assets_dir / f"{asset_id}{suffix}"
        path.write_bytes(data)
        return asset_id, path, None, "video"


def find_asset(asset_id: str, assets_dir: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", asset_id or ""):
        raise ImageInputError(f"Invalid asset id: {asset_id!r}")
    matches = list(assets_dir.glob(f"{asset_id}.*"))
    if not matches:
        raise ImageInputError(f"Asset '{asset_id}' not found (upload via POST /v1/assets first)")
    return matches[0]


def resolve_image_input(
    *,
    data_dir: Path,
    image_b64: Optional[str] = None,
    image_url: Optional[str] = None,
    image_path: Optional[str] = None,
    image_asset_id: Optional[str] = None,
) -> Tuple[Path, Tuple[int, int]]:
    """Resolve one of the image sources to (local_path, (width, height))."""
    inputs_dir = data_dir / "inputs"

    if image_asset_id is not None:
        path = find_asset(image_asset_id, data_dir / "assets")
        with Image.open(path) as img:
            return path, img.size

    if image_path is not None:
        path = Path(image_path).expanduser().resolve()
        if not path.is_file():
            raise ImageInputError(f"image_path does not exist on the server: {path}")
        try:
            with Image.open(path) as img:
                return path, img.size
        except Exception as exc:
            raise ImageInputError(f"image_path is not a decodable image: {exc}") from exc

    if image_b64 is not None:
        payload = _DATA_URI_RE.sub("", image_b64.strip())
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageInputError(f"image_b64 is not valid base64: {exc}") from exc
        return _validate_and_save(data, inputs_dir, uuid.uuid4().hex[:12])

    if image_url is not None:
        if not image_url.lower().startswith(("http://", "https://")):
            raise ImageInputError("image_url must be an http(s) URL")
        try:
            req = urllib.request.Request(image_url, headers={"User-Agent": "wan2gp-server/0.1"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read(_MAX_DOWNLOAD_BYTES + 1)
        except Exception as exc:
            raise ImageInputError(f"Failed to download image_url: {exc}") from exc
        if len(data) > _MAX_DOWNLOAD_BYTES:
            raise ImageInputError("Downloaded image exceeds the 50 MB limit")
        return _validate_and_save(data, inputs_dir, uuid.uuid4().hex[:12])

    raise ImageInputError("No image source provided")


def resolve_video_input(
    *,
    data_dir: Path,
    video_asset_id: Optional[str] = None,
    video_path: Optional[str] = None,
) -> Path:
    """Resolve an uploaded asset or server-side path to a local video."""
    if video_asset_id is not None:
        path = find_asset(video_asset_id, data_dir / "assets")
    elif video_path is not None:
        path = Path(video_path).expanduser().resolve()
        if not path.is_file():
            raise VideoInputError(f"video_path does not exist on the server: {path}")
    else:
        raise VideoInputError("No video source provided")

    if path.suffix.lower() not in _VIDEO_EXTS:
        raise VideoInputError(f"Input is not a supported video file: {path.name}")
    return path
