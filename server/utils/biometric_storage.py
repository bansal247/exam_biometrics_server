"""File-based biometric image storage utilities.

Images are stored on a Docker volume (BIOMETRICS_DIR) instead of in the DB.
Auth photos (operator login selfies) go to auth/{exam_id}/.
Candidate biometrics go to {exam_id}/{center_code}/{candidate_no}/.
"""

import io
from pathlib import Path
from typing import Optional

import aiofiles
from PIL import Image as PilImage


def _settings():
    from config import get_settings
    return get_settings()


# ── Directory helpers ─────────────────────────────────────────────────────────

def get_candidate_dir(exam_id: str, center_code: str, candidate_no: str) -> Path:
    d = Path(_settings().BIOMETRICS_DIR) / exam_id / center_code / candidate_no
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_auth_dir(exam_id: str) -> Path:
    d = Path(_settings().BIOMETRICS_DIR) / "auth" / exam_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Write helpers (async) ─────────────────────────────────────────────────────

async def save_photo(exam_id: str, center_code: str, candidate_no: str,
                     photo_id: str, data: bytes) -> str:
    path = get_candidate_dir(exam_id, center_code, candidate_no) / f"photo_{photo_id}.jpg"
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)
    return str(path)


async def save_auth_photo(exam_id: str, photo_id: str, data: bytes) -> str:
    path = get_auth_dir(exam_id) / f"photo_{photo_id}.jpg"
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)
    return str(path)


async def save_fingerprint(exam_id: str, center_code: str, candidate_no: str,
                            fp_id: str, data: bytes) -> str:
    path = get_candidate_dir(exam_id, center_code, candidate_no) / f"fp_{fp_id}.bmp"
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)
    return str(path)


async def save_iris(exam_id: str, center_code: str, candidate_no: str,
                    iris_id: str, data: bytes) -> str:
    path = get_candidate_dir(exam_id, center_code, candidate_no) / f"iris_{iris_id}.bmp"
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)
    return str(path)


# ── Read helpers ──────────────────────────────────────────────────────────────

def read_file(file_path: Optional[str]) -> Optional[bytes]:
    if not file_path:
        return None
    try:
        return Path(file_path).read_bytes()
    except (FileNotFoundError, OSError):
        return None


def preferred_photo_bytes(photo) -> Optional[bytes]:
    """Return compressed bytes if available, otherwise original."""
    return read_file(photo.compressed_file_path) or read_file(photo.file_path)


def preferred_fp_bytes(fp) -> Optional[bytes]:
    return read_file(fp.compressed_file_path) or read_file(fp.file_path)


def preferred_iris_bytes(iris) -> Optional[bytes]:
    return read_file(iris.compressed_file_path) or read_file(iris.file_path)


# ── Compression helpers (used by cron jobs) ───────────────────────────────────

def compress_jpeg(data: bytes, quality: int = 40) -> tuple[bytes, str]:
    """Return (compressed_bytes, 'jpg'). Input can be JPEG or any Pillow-readable format."""
    img = PilImage.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), "jpg"


def bmp_to_jpeg(data: bytes, quality: int = 50) -> tuple[bytes, str]:
    """Convert BMP (fingerprint/iris) to JPEG. Returns (compressed_bytes, 'jpg')."""
    img = PilImage.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), "jpg"
