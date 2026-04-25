"""Load testing router.

All data written here is tagged is_load_test=True and can be deleted in bulk
via DELETE /load/delete or by running load_test/cleanup.sql directly.

No cron jobs, matching, or AWS calls are triggered by any endpoint here.
"""

import asyncio
import base64
import shutil
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db, async_session
from models import (
    CandidateCapture, Center, Fingerprint, Iris, Photo,
)
from utils.biometric_storage import preferred_photo_bytes

router = APIRouter(prefix="/load", tags=["load"])

_delete_tasks: dict = {}


# ── Auth ──────────────────────────────────────────────────────────────────────

async def require_load_token(authorization: str = Header(...)):
    token = get_settings().LOAD_TOKEN
    if not token:
        raise HTTPException(503, "Load testing disabled — set LOAD_TOKEN in .env")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or value != token:
        raise HTTPException(401, "Invalid load token")


# ── File storage ──────────────────────────────────────────────────────────────

def _load_dir(exam_id: str, center_code: str, candidate_no: str) -> Path:
    d = (
        Path(get_settings().BIOMETRICS_DIR)
        / "load_test"
        / exam_id
        / center_code
        / candidate_no
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _save_bytes(path: Path, data: bytes) -> str:
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)
    return str(path)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _center_code(db: AsyncSession, center_id: uuid.UUID) -> str:
    center = await db.get(Center, center_id)
    return center.code_plain if center else "unknown"


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.get("/")
async def load_root():
    return {"status": "load service running"}

@router.get("/students")
async def get_students(
    exam_id: uuid.UUID,
    center_id: uuid.UUID,
    shift_id: uuid.UUID,
    _: None = Depends(require_load_token),
    db: AsyncSession = Depends(get_db),
):
    """Return seed candidates (admin-uploaded rows) with their reference photo as base64.
    The load tester uses this list to cycle through real candidate numbers."""
    rows = (
        await db.execute(
            select(CandidateCapture)
            .where(
                CandidateCapture.exam_id == exam_id,
                CandidateCapture.center_id == center_id,
                CandidateCapture.shift_id == shift_id,
                CandidateCapture.new_photo_id.is_(None),
                CandidateCapture.is_load_test.is_(False),
            )
            .order_by(CandidateCapture.created_at)
        )
    ).scalars().all()

    # Batch-load photos to avoid N+1
    photo_ids = {r.photo_id for r in rows if r.photo_id}
    photos_by_id = {p.id: p for p in (await db.execute(
        select(Photo).where(Photo.id.in_(photo_ids))
    )).scalars().all()} if photo_ids else {}

    # Parallel disk reads
    _rows_with_photo = [r for r in rows if r.photo_id and r.photo_id in photos_by_id]
    _raws = await asyncio.gather(*[
        asyncio.to_thread(preferred_photo_bytes, photos_by_id[r.photo_id])
        for r in _rows_with_photo
    ])
    _photo_map = {r.candidate_no_plain: raw for r, raw in zip(_rows_with_photo, _raws)}

    return [
        {
            "candidate_no": row.candidate_no_plain,
            "name": row.name,
            "roll_no": row.roll_no_plain,
            "ref_photo_b64": (
                base64.b64encode(raw).decode()
                if (raw := _photo_map.get(row.candidate_no_plain))
                else None
            ),
        }
        for row in rows
    ]


@router.post("/add-photo")
async def add_photo(
    exam_id: uuid.UUID = Form(...),
    shift_id: uuid.UUID = Form(...),
    center_id: uuid.UUID = Form(...),
    candidate_no: str = Form(...),
    file: UploadFile = File(...),
    _: None = Depends(require_load_token),
    db: AsyncSession = Depends(get_db),
):
    """Write a Photo record + CandidateCapture submission row tagged as load test.
    photo_match_status is left NULL so cron match_pending_photos ignores it."""
    raw = await file.read()
    code = await _center_code(db, center_id)

    photo = Photo(is_load_test=True)
    db.add(photo)
    await db.flush()

    path = _load_dir(str(exam_id), code, candidate_no) / f"photo_{photo.id}.jpg"
    photo.file_path = await _save_bytes(path, raw)
    await db.flush()

    capture = CandidateCapture(
        exam_id=exam_id,
        shift_id=shift_id,
        center_id=center_id,
        candidate_no=candidate_no,
        candidate_no_plain=candidate_no,
        name="load_test",
        new_photo_id=photo.id,
        photo_match_status="match",   # intentionally not "pending" — skip cron
        attendance="present",
        is_load_test=True,
    )
    db.add(capture)
    await db.commit()
    return {"status": "ok"}


@router.post("/add-fingerprint")
async def add_fingerprint(
    exam_id: uuid.UUID = Form(...),
    shift_id: uuid.UUID = Form(...),
    center_id: uuid.UUID = Form(...),
    candidate_no: str = Form(...),
    file: UploadFile = File(...),
    _: None = Depends(require_load_token),
    db: AsyncSession = Depends(get_db),
):
    """Write a Fingerprint record tagged as load test. No CandidateCapture link,
    no template extraction (cron only processes CandidateMatch-referenced records)."""
    raw = await file.read()
    code = await _center_code(db, center_id)

    fp = Fingerprint(is_load_test=True)
    db.add(fp)
    await db.flush()

    path = _load_dir(str(exam_id), code, candidate_no) / f"fp_{fp.id}.bmp"
    fp.file_path = await _save_bytes(path, raw)
    await db.commit()
    return {"status": "ok"}


@router.post("/add-iris")
async def add_iris(
    exam_id: uuid.UUID = Form(...),
    shift_id: uuid.UUID = Form(...),
    center_id: uuid.UUID = Form(...),
    candidate_no: str = Form(...),
    file: UploadFile = File(...),
    _: None = Depends(require_load_token),
    db: AsyncSession = Depends(get_db),
):
    """Write an Iris record tagged as load test. No template extraction triggered."""
    raw = await file.read()
    code = await _center_code(db, center_id)

    iris = Iris(is_load_test=True)
    db.add(iris)
    await db.flush()

    path = _load_dir(str(exam_id), code, candidate_no) / f"iris_{iris.id}.bmp"
    iris.file_path = await _save_bytes(path, raw)
    await db.commit()
    return {"status": "ok"}


async def _run_delete(task_id: str) -> None:
    """Background coroutine: delete all load-test rows and files."""
    try:
        file_paths: list[str] = []
        counts: dict = {}

        async with async_session() as db:
            for model in (Photo, Fingerprint, Iris):
                rows = (
                    await db.execute(select(model).where(model.is_load_test.is_(True)))
                ).scalars().all()
                for r in rows:
                    for p in (r.file_path, r.compressed_file_path):
                        if p:
                            file_paths.append(p)

            res_cap = await db.execute(
                delete(CandidateCapture).where(CandidateCapture.is_load_test.is_(True))
            )
            res_photo = await db.execute(
                delete(Photo).where(Photo.is_load_test.is_(True))
            )
            res_fp = await db.execute(
                delete(Fingerprint).where(Fingerprint.is_load_test.is_(True))
            )
            res_iris = await db.execute(
                delete(Iris).where(Iris.is_load_test.is_(True))
            )
            await db.commit()
            counts = {
                "captures": res_cap.rowcount,
                "photos": res_photo.rowcount,
                "fingerprints": res_fp.rowcount,
                "irises": res_iris.rowcount,
            }

        # File deletion is blocking — run in thread pool
        load_root = Path(get_settings().BIOMETRICS_DIR) / "load_test"

        def _cleanup():
            for p in file_paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(load_root, ignore_errors=True)

        await asyncio.to_thread(_cleanup)
        _delete_tasks[task_id] = {"status": "done", "deleted": counts}

    except Exception as e:
        _delete_tasks[task_id] = {"status": "error", "error": str(e)}


@router.delete("/delete")
async def delete_load_data(_: None = Depends(require_load_token)):
    """Start background deletion of all load-test data. Returns task_id to poll."""
    task_id = str(uuid.uuid4())
    _delete_tasks[task_id] = {"status": "running"}
    asyncio.create_task(_run_delete(task_id))
    return {"task_id": task_id, "status": "running"}


@router.get("/delete-status/{task_id}")
async def delete_status(task_id: str, _: None = Depends(require_load_token)):
    st = _delete_tasks.get(task_id)
    if not st:
        raise HTTPException(404, "Task not found")
    return st
