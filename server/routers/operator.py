"""Operator endpoints."""

import asyncio
import base64
from collections import defaultdict
from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Header, Query, Request, Response, UploadFile
from sqlalchemy import select, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import (Operator, Exam, Center, Shift, Photo,
                    Fingerprint, Iris, CandidateCapture, CandidateMatch, DeviceSession)
from schemas import (OperatorLogin, TokenResponse, OperatorSessionLogin,
                     QrDataBody, BatchSyncRequest)
from utils.auth import (create_token, require_operator, require_operator_session, verify_password)
from utils.limiter import limiter
from utils.biometric_storage import (
    save_photo, save_auth_photo, save_fingerprint, save_iris, read_file,
)
from utils.logger import log_operator, log_auth
from utils.matching import (match_photos, match_fingerprints, match_iris,
                             extract_fingerprint_template, extract_iris_template)

router = APIRouter(prefix="/operator", tags=["Operator"])


# ── Sync-key auth dependency ──────────────────────────────────────────────────

async def _exam_by_sync_key(x_sync_key: str = Header(...), db: AsyncSession = Depends(get_db)) -> Exam:
    exam = await db.scalar(select(Exam).where(Exam.sync_key_plain == x_sync_key))
    if not exam:
        raise HTTPException(401, "Invalid sync key")
    return exam


# ── Lookup helpers ────────────────────────────────────────────────────────────

async def _get_center_code(db, center_id: UUID) -> str:
    center = await db.get(Center, center_id)
    return center.code_plain if center else "unknown"


async def _get_capture_seed(db, exam_id, shift_id, center_id, candidate_no: str) -> CandidateCapture:
    """Return the earliest CandidateCapture row (seed) for this candidate."""
    c = await db.scalar(
        select(CandidateCapture)
        .where(
            CandidateCapture.exam_id == exam_id,
            CandidateCapture.shift_id == shift_id,
            CandidateCapture.center_id == center_id,
            CandidateCapture.candidate_no_plain == candidate_no,
        )
        .order_by(CandidateCapture.created_at.asc())
    )
    if not c:
        raise HTTPException(404, "Candidate not found")
    return c


async def _get_match_row(db, exam_id, shift_id, center_id, candidate_no: str) -> CandidateMatch:
    c = await db.scalar(
        select(CandidateMatch).where(
            CandidateMatch.exam_id == exam_id,
            CandidateMatch.shift_id == shift_id,
            CandidateMatch.center_id == center_id,
            CandidateMatch.candidate_no_plain == candidate_no,
        )
    )
    if not c:
        c = await db.scalar(
            select(CandidateMatch).where(
                CandidateMatch.exam_id == exam_id,
                CandidateMatch.shift_id == shift_id,
                CandidateMatch.center_id == center_id,
                CandidateMatch.roll_no_plain == candidate_no,
            )
        )
    if not c:
        raise HTTPException(404, "Candidate not found")
    return c


async def _get_match_by_session(db, session: dict, candidate_no: str) -> CandidateMatch:
    return await _get_match_row(
        db, UUID(session["exam_id"]), UUID(session["shift_id"]),
        UUID(session["center_id"]), candidate_no,
    )


async def _read_b64(path: str) -> "str | None":
    try:
        raw = await asyncio.to_thread(read_file, path)
        return base64.b64encode(raw).decode() if raw else None
    except Exception:
        return None


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def operator_login(body: OperatorLogin, response: Response, db: AsyncSession = Depends(get_db)):
    op = await db.scalar(select(Operator).where(Operator.phone_plain == body.phone))
    if not op or not verify_password(body.password, op.password):
        raise HTTPException(401, "Invalid credentials")
    raw = base64.b64decode(body.photo_base64)
    photo = Photo()
    db.add(photo)
    await db.flush()
    file_path = await save_auth_photo(str(op.exam_id), str(photo.id), raw)
    photo.file_path = file_path
    await db.flush()
    await log_auth(db, mobile=body.phone, role="operator", photo_id=photo.id, name=body.name)
    await log_operator(db, str(op.id), "login", {"photo_id": str(photo.id)})
    token = create_token({"role": "operator", "sub": str(op.id), "exam_id": str(op.exam_id)},
                         expires_minutes=5)
    response.set_cookie("auth_token", token, httponly=True, samesite="lax", max_age=300)
    return TokenResponse(access_token=token)


@router.get("/exams")
async def get_exams(db: AsyncSession = Depends(get_db), op: dict = Depends(require_operator)):
    exam = await db.get(Exam, UUID(op["exam_id"]))
    if not exam:
        raise HTTPException(404, "Exam not found")

    if exam.type == "capture":
        pairs = (await db.execute(
            select(CandidateCapture.center_id, CandidateCapture.shift_id)
            .where(CandidateCapture.exam_id == exam.id)
            .distinct()
        )).all()
    else:
        pairs = (await db.execute(
            select(CandidateMatch.center_id, CandidateMatch.shift_id)
            .where(CandidateMatch.exam_id == exam.id)
            .distinct()
        )).all()

    center_ids = {p[0] for p in pairs}
    shift_ids  = {p[1] for p in pairs}
    centers_by_id = {c.id: c for c in (await db.execute(
        select(Center).where(Center.id.in_(center_ids))
    )).scalars().all()} if center_ids else {}
    shifts_by_id = {s.id: s for s in (await db.execute(
        select(Shift).where(Shift.id.in_(shift_ids))
    )).scalars().all()} if shift_ids else {}

    centers_map = {}
    for center_id, shift_id in pairs:
        center = centers_by_id.get(center_id)
        shift  = shifts_by_id.get(shift_id)
        if not center or not shift:
            continue
        cid = str(center.id)
        if cid not in centers_map:
            centers_map[cid] = {"id": cid, "code": center.code, "name": center.name, "shifts": []}
        centers_map[cid]["shifts"].append({
            "id": str(shift.id), "shift_code": shift.shift_code,
            "date": str(shift.date), "start_time": str(shift.start_time),
        })
    await log_operator(db, op["sub"], "get_exams", {"exam_id": op["exam_id"]})
    return {"exam_id": str(exam.id), "exam_name": exam.name, "sync_key": exam.sync_key,
            "qr_string": exam.qr_string, "qr_data": exam.qr_data, "type": exam.type,
            "centers": list(centers_map.values())}


@router.post("/session-login", response_model=TokenResponse)
async def operator_session_login(body: OperatorSessionLogin, response: Response,
                                  db: AsyncSession = Depends(get_db),
                                  op: dict = Depends(require_operator)):
    exam = await db.get(Exam, body.exam_id)
    matching_table = 2 if (exam and exam.type == "match") else 1
    await db.execute(
        pg_insert(DeviceSession)
        .values(
            device_id=body.device_id, operator_id=UUID(op["sub"]),
            exam_id=body.exam_id, center_id=body.center_id, shift_id=body.shift_id,
            matching_table=matching_table, last_heartbeat=datetime.utcnow(),
        )
        .on_conflict_do_update(
            constraint="uq_device_session",
            set_={"last_heartbeat": datetime.utcnow(), "operator_id": UUID(op["sub"])},
        )
    )
    token = create_token(
        {
            "role": "operator_session", "sub": op["sub"],
            "exam_id": str(body.exam_id), "shift_id": str(body.shift_id),
            "center_id": str(body.center_id), "device_id": body.device_id,
        },
        expires_minutes=1440,
    )
    await log_operator(db, op["sub"], "session_login", {"exam_id": str(body.exam_id)})
    response.set_cookie("auth_token", token, httponly=True, samesite="lax", max_age=86400)
    return TokenResponse(access_token=token)


# ── Device heartbeat ──────────────────────────────────────────────────────────

@router.post("/heartbeat")
async def heartbeat(db: AsyncSession = Depends(get_db),
                    session: dict = Depends(require_operator_session)):
    device_id = session.get("device_id")
    if device_id:
        await db.execute(
            update(DeviceSession)
            .where(DeviceSession.device_id == device_id,
                   DeviceSession.exam_id == UUID(session["exam_id"]))
            .values(last_heartbeat=datetime.utcnow())
        )
        await db.commit()
    return {"status": "ok"}


# ── Save QR data ──────────────────────────────────────────────────────────────

@router.post("/save-qr")
async def save_qr_data(body: QrDataBody, exam: Exam = Depends(_exam_by_sync_key),
                        db: AsyncSession = Depends(get_db)):
    exam.qr_data = body.qr_data
    await db.flush()
    return {"status": "ok"}


# ── Capture details ───────────────────────────────────────────────────────────

@router.get("/capture-details")
async def get_capture_details(
    page: int = Query(default=0, ge=0),
    per_page: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    session: dict = Depends(require_operator_session)
):
    exam_id   = UUID(session["exam_id"])
    shift_id  = UUID(session["shift_id"])
    center_id = UUID(session["center_id"])

    # Get distinct candidate_nos for this page only
    candidate_nos = [r[0] for r in (await db.execute(
        select(CandidateCapture.candidate_no_plain)
        .where(CandidateCapture.exam_id == exam_id,
               CandidateCapture.shift_id == shift_id,
               CandidateCapture.center_id == center_id)
        .distinct()
        .order_by(CandidateCapture.candidate_no_plain)
        .offset(page * per_page)
        .limit(per_page)
    )).all()]
    if not candidate_nos:
        return []

    all_rows = (await db.execute(
        select(CandidateCapture)
        .where(CandidateCapture.exam_id == exam_id,
               CandidateCapture.shift_id == shift_id,
               CandidateCapture.center_id == center_id,
               CandidateCapture.candidate_no_plain.in_(candidate_nos))
        .order_by(CandidateCapture.candidate_no_plain, CandidateCapture.created_at.asc())
    )).scalars().all()

    # Group by candidate_no_plain; seed = first row (created_at ASC)
    grouped: dict = defaultdict(list)
    for row in all_rows:
        grouped[row.candidate_no_plain].append(row)

    # Batch-load reference photos to avoid N+1
    ref_photo_ids = {rows[0].photo_id for rows in grouped.values() if rows[0].photo_id}
    photos_by_id = {p.id: p for p in (await db.execute(
        select(Photo).where(Photo.id.in_(ref_photo_ids))
    )).scalars().all()} if ref_photo_ids else {}

    # Parallel photo reads
    _photo_paths = {
        cno: photos_by_id[rows[0].photo_id].file_path
        for cno, rows in grouped.items()
        if rows[0].photo_id and rows[0].photo_id in photos_by_id
    }
    _cno_keys = list(_photo_paths)
    _b64_values = await asyncio.gather(*[_read_b64(_photo_paths[k]) for k in _cno_keys])
    photo_data_map = dict(zip(_cno_keys, _b64_values))

    results = []
    for cno, rows in grouped.items():
        seed = rows[0]
        any_attended = any(r.attendance == "present" for r in rows)
        has_photo = any(r.new_photo_id is not None for r in rows)
        has_fp    = any(r.fingerprint_id is not None for r in rows)
        has_iris  = any(r.iris_id is not None for r in rows)
        match_statuses = [r.photo_match_status for r in rows if r.new_photo_id]
        latest_match   = match_statuses[-1] if match_statuses else None

        results.append({
            "candidate_id":       str(seed.id),
            "candidate_no":       seed.candidate_no,
            "name":               seed.name,
            "roll_no":            seed.roll_no,
            "father_name":        seed.father_name,
            "mother_name":        seed.mother_name,
            "dob":                str(seed.dob) if seed.dob else None,
            "attended":           any_attended,
            "photo_data":         photo_data_map.get(cno),
            "has_photo":          has_photo,
            "has_fingerprint":    has_fp,
            "has_iris":           has_iris,
            "photo_match_status": latest_match,
        })

    await log_operator(db, session["sub"], "get_capture_details", {"count": len(results)})
    return results


# ── Add biometrics (sync_key auth, capture exams) ─────────────────────────────

@router.post("/add-photo")
async def add_photo(
    shift_id: UUID = Form(...), center_id: UUID = Form(...),
    candidate_no: str = Form(...), file: UploadFile = File(...),
    exam: Exam = Depends(_exam_by_sync_key), db: AsyncSession = Depends(get_db)
):
    if exam.type != "capture":
        raise HTTPException(400, "Photo upload not allowed for non-capture exam")

    # Find seed row to copy identity from
    seed = await db.scalar(
        select(CandidateCapture)
        .where(CandidateCapture.exam_id == exam.id,
               CandidateCapture.shift_id == shift_id,
               CandidateCapture.center_id == center_id,
               CandidateCapture.candidate_no_plain == candidate_no)
        .order_by(CandidateCapture.created_at.asc())
    )
    if not seed:
        raise HTTPException(404, "Candidate not found")

    center_code = await _get_center_code(db, center_id)
    raw = await file.read()
    photo = Photo()
    db.add(photo)
    await db.flush()
    photo.file_path = await save_photo(str(exam.id), center_code, candidate_no, str(photo.id), raw)
    await db.flush()

    # Check if any existing row for this candidate is attended
    any_attended = await db.scalar(
        select(CandidateCapture.id)
        .where(CandidateCapture.exam_id == exam.id,
               CandidateCapture.shift_id == shift_id,
               CandidateCapture.center_id == center_id,
               CandidateCapture.candidate_no_plain == candidate_no,
               CandidateCapture.attendance == "present")
        .limit(1)
    )

    new_row = CandidateCapture(
        exam_id=exam.id, shift_id=shift_id, center_id=center_id,
        candidate_no=seed.candidate_no, candidate_no_plain=seed.candidate_no_plain,
        name=seed.name, roll_no=seed.roll_no, roll_no_plain=seed.roll_no_plain,
        father_name=seed.father_name, mother_name=seed.mother_name, dob=seed.dob,
        photo_id=seed.photo_id,
        new_photo_id=photo.id,
        photo_match_status="pending",
        attendance="present" if any_attended else "absent",
        attendance_marked_at=datetime.utcnow() if any_attended else None,
    )
    db.add(new_row)
    await db.flush()
    return {"status": "ok"}


@router.post("/add-fingerprint")
async def add_fingerprint(
    shift_id: UUID = Form(...), center_id: UUID = Form(...),
    candidate_no: str = Form(...), file: UploadFile = File(...),
    exam: Exam = Depends(_exam_by_sync_key), db: AsyncSession = Depends(get_db)
):
    if exam.type != "capture":
        raise HTTPException(400, "Fingerprint upload not allowed for non-capture exam")

    center_code = await _get_center_code(db, center_id)
    raw = await file.read()
    fp = Fingerprint()
    db.add(fp)
    await db.flush()
    fp.file_path = await save_fingerprint(str(exam.id), center_code, candidate_no, str(fp.id), raw)
    await db.flush()

    # Update the latest submission row that has a photo but no fingerprint
    row = await db.scalar(
        select(CandidateCapture)
        .where(CandidateCapture.exam_id == exam.id,
               CandidateCapture.shift_id == shift_id,
               CandidateCapture.center_id == center_id,
               CandidateCapture.candidate_no_plain == candidate_no,
               CandidateCapture.new_photo_id.isnot(None),
               CandidateCapture.fingerprint_id.is_(None))
        .order_by(CandidateCapture.created_at.desc())
    )
    if not row:
        raise HTTPException(400, "No photo submission found — add photo before fingerprint")
    row.fingerprint_id = fp.id
    await db.flush()
    return {"status": "ok"}


@router.post("/add-iris")
async def add_iris(
    shift_id: UUID = Form(...), center_id: UUID = Form(...),
    candidate_no: str = Form(...), file: UploadFile = File(...),
    exam: Exam = Depends(_exam_by_sync_key), db: AsyncSession = Depends(get_db)
):
    if exam.type != "capture":
        raise HTTPException(400, "Iris upload not allowed for non-capture exam")

    center_code = await _get_center_code(db, center_id)
    raw = await file.read()
    iris = Iris()
    db.add(iris)
    await db.flush()
    iris.file_path = await save_iris(str(exam.id), center_code, candidate_no, str(iris.id), raw)
    await db.flush()

    row = await db.scalar(
        select(CandidateCapture)
        .where(CandidateCapture.exam_id == exam.id,
               CandidateCapture.shift_id == shift_id,
               CandidateCapture.center_id == center_id,
               CandidateCapture.candidate_no_plain == candidate_no,
               CandidateCapture.new_photo_id.isnot(None),
               CandidateCapture.iris_id.is_(None))
        .order_by(CandidateCapture.created_at.desc())
    )
    if not row:
        raise HTTPException(400, "No photo submission found — add photo before iris")
    row.iris_id = iris.id
    await db.flush()
    return {"status": "ok"}


# ── Batch biometric sync ──────────────────────────────────────────────────────

@router.post("/sync-batch")
@limiter.limit("120/minute")
async def sync_batch(request: Request, body: BatchSyncRequest,
                     exam: Exam = Depends(_exam_by_sync_key),
                     db: AsyncSession = Depends(get_db)):
    results = []
    for item in body.items:
        try:
            center_code = await _get_center_code(db, item.center_id)
            raw = base64.b64decode(item.data_base64)

            if item.type == "photo":
                if exam.type != "capture":
                    results.append({"candidate_no": item.candidate_no, "type": item.type,
                                     "status": "skipped_not_capture"})
                    continue
                seed = await db.scalar(
                    select(CandidateCapture)
                    .where(CandidateCapture.exam_id == exam.id,
                           CandidateCapture.shift_id == item.shift_id,
                           CandidateCapture.center_id == item.center_id,
                           CandidateCapture.candidate_no_plain == item.candidate_no)
                    .order_by(CandidateCapture.created_at.asc())
                )
                if not seed:
                    results.append({"candidate_no": item.candidate_no, "type": item.type,
                                     "status": "not_found"})
                    continue
                photo = Photo()
                db.add(photo)
                await db.flush()
                photo.file_path = await save_photo(str(exam.id), center_code, item.candidate_no,
                                                   str(photo.id), raw)
                await db.flush()
                any_attended = await db.scalar(
                    select(CandidateCapture.id)
                    .where(CandidateCapture.exam_id == exam.id,
                           CandidateCapture.shift_id == item.shift_id,
                           CandidateCapture.center_id == item.center_id,
                           CandidateCapture.candidate_no_plain == item.candidate_no,
                           CandidateCapture.attendance == "present")
                    .limit(1)
                )
                db.add(CandidateCapture(
                    exam_id=exam.id, shift_id=item.shift_id, center_id=item.center_id,
                    candidate_no=seed.candidate_no, candidate_no_plain=seed.candidate_no_plain,
                    name=seed.name, roll_no=seed.roll_no, roll_no_plain=seed.roll_no_plain,
                    father_name=seed.father_name, mother_name=seed.mother_name, dob=seed.dob,
                    photo_id=seed.photo_id, new_photo_id=photo.id, photo_match_status="pending",
                    attendance="present" if any_attended else "absent",
                    attendance_marked_at=datetime.utcnow() if any_attended else None,
                ))
                await db.flush()
                results.append({"candidate_no": item.candidate_no, "type": item.type, "status": "ok"})

            elif item.type == "fingerprint":
                if exam.type != "capture":
                    results.append({"candidate_no": item.candidate_no, "type": item.type,
                                     "status": "skipped_not_capture"})
                    continue
                row = await db.scalar(
                    select(CandidateCapture)
                    .where(CandidateCapture.exam_id == exam.id,
                           CandidateCapture.shift_id == item.shift_id,
                           CandidateCapture.center_id == item.center_id,
                           CandidateCapture.candidate_no_plain == item.candidate_no,
                           CandidateCapture.new_photo_id.isnot(None),
                           CandidateCapture.fingerprint_id.is_(None))
                    .order_by(CandidateCapture.created_at.desc())
                )
                if not row:
                    results.append({"candidate_no": item.candidate_no, "type": item.type,
                                     "status": "not_found"})
                    continue
                fp = Fingerprint()
                db.add(fp)
                await db.flush()
                fp.file_path = await save_fingerprint(str(exam.id), center_code, item.candidate_no,
                                                      str(fp.id), raw)
                row.fingerprint_id = fp.id
                await db.flush()
                results.append({"candidate_no": item.candidate_no, "type": item.type, "status": "ok"})

            elif item.type == "iris":
                if exam.type != "capture":
                    results.append({"candidate_no": item.candidate_no, "type": item.type,
                                     "status": "skipped_not_capture"})
                    continue
                row = await db.scalar(
                    select(CandidateCapture)
                    .where(CandidateCapture.exam_id == exam.id,
                           CandidateCapture.shift_id == item.shift_id,
                           CandidateCapture.center_id == item.center_id,
                           CandidateCapture.candidate_no_plain == item.candidate_no,
                           CandidateCapture.new_photo_id.isnot(None),
                           CandidateCapture.iris_id.is_(None))
                    .order_by(CandidateCapture.created_at.desc())
                )
                if not row:
                    results.append({"candidate_no": item.candidate_no, "type": item.type,
                                     "status": "not_found"})
                    continue
                iris = Iris()
                db.add(iris)
                await db.flush()
                iris.file_path = await save_iris(str(exam.id), center_code, item.candidate_no,
                                                  str(iris.id), raw)
                row.iris_id = iris.id
                await db.flush()
                results.append({"candidate_no": item.candidate_no, "type": item.type, "status": "ok"})

            else:
                results.append({"candidate_no": item.candidate_no, "type": item.type,
                                 "status": "unknown_type"})
        except Exception as e:
            results.append({"candidate_no": item.candidate_no, "type": item.type,
                             "status": "error", "detail": str(e)})

    return {"processed": len(results), "results": results}


# ── Single candidate lookup (match exam) ─────────────────────────────────────

@router.get("/candidate")
async def get_candidate(candidate_no: str, db: AsyncSession = Depends(get_db),
                         session: dict = Depends(require_operator_session)):
    exam = await db.get(Exam, UUID(session["exam_id"]))
    if not exam or exam.type != "match":
        raise HTTPException(400, "Endpoint for match exams only")

    cm = await _get_match_by_session(db, session, candidate_no)

    photo_data = None
    if cm.photo_id:
        p = await db.get(Photo, cm.photo_id)
        if p:
            photo_data = await _read_b64(p.file_path)

    await log_operator(db, session["sub"], "get_candidate", {"candidate_no": candidate_no})
    return {
        "candidate_id":            str(cm.id),
        "candidate_no":            cm.candidate_no,
        "name":                    cm.name,
        "roll_no":                 cm.roll_no,
        "father_name":             cm.father_name,
        "mother_name":             cm.mother_name,
        "dob":                     str(cm.dob) if cm.dob else None,
        "attended":                cm.attendance == "present",
        "photo_data":              photo_data,
        "photo_match_status":      cm.photo_match_status,
        "fingerprint_match_status": cm.fingerprint_match_status,
        "iris_match_status":       cm.iris_match_status,
    }


# ── MT1 / MT2 data endpoints ─────────────────────────────────────────────────

@router.get("/mt1")
async def get_mt1(db: AsyncSession = Depends(get_db),
                   session: dict = Depends(require_operator_session)):
    """Return CandidateCapture submission rows (those with a captured photo)."""
    rows = (await db.execute(
        select(CandidateCapture)
        .where(
            CandidateCapture.exam_id   == UUID(session["exam_id"]),
            CandidateCapture.shift_id  == UUID(session["shift_id"]),
            CandidateCapture.center_id == UUID(session["center_id"]),
            CandidateCapture.new_photo_id.isnot(None),
        )
        .order_by(CandidateCapture.candidate_no_plain, CandidateCapture.created_at.asc())
    )).scalars().all()

    result = [
        {
            "id":                 str(r.id),
            "candidate_no":       r.candidate_no,
            "name":               r.name,
            "attended":           r.attendance == "present",
            "photo_match_status": r.photo_match_status,
            "matched_at":         r.matched_at.isoformat() if r.matched_at else None,
            "has_photo":          r.new_photo_id is not None,
            "has_fingerprint":    r.fingerprint_id is not None,
            "has_iris":           r.iris_id is not None,
            "created_at":         r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    await log_operator(db, session["sub"], "get_mt1", {"exam_id": session["exam_id"]})
    return result


@router.get("/mt2")
async def get_mt2(db: AsyncSession = Depends(get_db),
                   session: dict = Depends(require_operator_session)):
    """Return CandidateMatch rows for this exam+shift+center."""
    rows = (await db.execute(
        select(CandidateMatch)
        .where(
            CandidateMatch.exam_id   == UUID(session["exam_id"]),
            CandidateMatch.shift_id  == UUID(session["shift_id"]),
            CandidateMatch.center_id == UUID(session["center_id"]),
        )
        .order_by(CandidateMatch.candidate_no_plain)
    )).scalars().all()

    result = [
        {
            "id":                        str(r.id),
            "candidate_no":              r.candidate_no,
            "name":                      r.name,
            "attended":                  r.attendance == "present",
            "photo_match_status":        r.photo_match_status,
            "matched_at":                r.matched_at.isoformat() if r.matched_at else None,
            "fingerprint_match_status":  r.fingerprint_match_status,
            "fingerprint_matched_at":    r.fingerprint_matched_at.isoformat() if r.fingerprint_matched_at else None,
            "iris_match_status":         r.iris_match_status,
            "iris_matched_at":           r.iris_matched_at.isoformat() if r.iris_matched_at else None,
            "created_at":                r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    await log_operator(db, session["sub"], "get_mt2", {"exam_id": session["exam_id"]})
    return result


# ── Attendance ────────────────────────────────────────────────────────────────

@router.get("/attendance")
async def get_attendance(db: AsyncSession = Depends(get_db),
                          session: dict = Depends(require_operator_session)):
    exam_id   = UUID(session["exam_id"])
    shift_id  = UUID(session["shift_id"])
    center_id = UUID(session["center_id"])
    exam = await db.get(Exam, exam_id)
    center = await db.get(Center, center_id)
    shift  = await db.get(Shift, shift_id)
    center_code = center.code if center else ""
    shift_code  = shift.shift_code if shift else None

    if not exam or exam.type == "capture":
        all_rows = (await db.execute(
            select(CandidateCapture)
            .where(CandidateCapture.exam_id == exam_id,
                   CandidateCapture.shift_id == shift_id,
                   CandidateCapture.center_id == center_id)
            .order_by(CandidateCapture.candidate_no_plain, CandidateCapture.created_at.asc())
        )).scalars().all()
        grouped: dict = defaultdict(list)
        for r in all_rows:
            grouped[r.candidate_no_plain].append(r)

        ref_photo_ids = {rows[0].photo_id for rows in grouped.values() if rows[0].photo_id}
        photos_by_id = {p.id: p for p in (await db.execute(
            select(Photo).where(Photo.id.in_(ref_photo_ids))
        )).scalars().all()} if ref_photo_ids else {}

        _att_paths = {
            cno: photos_by_id[rows[0].photo_id].file_path
            for cno, rows in grouped.items()
            if rows[0].photo_id and rows[0].photo_id in photos_by_id
        }
        _att_keys = list(_att_paths)
        _att_b64 = await asyncio.gather(*[_read_b64(_att_paths[k]) for k in _att_keys])
        _att_photo_map = dict(zip(_att_keys, _att_b64))

        result = []
        for cno, rows in grouped.items():
            seed = rows[0]
            attended = any(r.attendance == "present" for r in rows)
            result.append({
                "candidate_no": seed.candidate_no, "name": seed.name,
                "roll_no": seed.roll_no, "father_name": seed.father_name,
                "dob": str(seed.dob) if seed.dob else None,
                "attended": attended, "center_code": center_code, "shift_code": shift_code,
                "photo_data": _att_photo_map.get(cno),
            })
    else:
        rows = (await db.execute(
            select(CandidateMatch)
            .where(CandidateMatch.exam_id == exam_id,
                   CandidateMatch.shift_id == shift_id,
                   CandidateMatch.center_id == center_id)
        )).scalars().all()

        ref_photo_ids = {r.photo_id for r in rows if r.photo_id}
        photos_by_id = {p.id: p for p in (await db.execute(
            select(Photo).where(Photo.id.in_(ref_photo_ids))
        )).scalars().all()} if ref_photo_ids else {}

        _match_paths = {
            str(r.id): photos_by_id[r.photo_id].file_path
            for r in rows
            if r.photo_id and r.photo_id in photos_by_id
        }
        _match_ids = list(_match_paths)
        _match_b64 = await asyncio.gather(*[_read_b64(_match_paths[k]) for k in _match_ids])
        _match_photo_map = dict(zip(_match_ids, _match_b64))

        result = []
        for r in rows:
            result.append({
                "candidate_no": r.candidate_no, "name": r.name,
                "roll_no": r.roll_no, "father_name": r.father_name,
                "dob": str(r.dob) if r.dob else None,
                "attended": r.attendance == "present", "center_code": center_code,
                "shift_code": shift_code, "photo_data": _match_photo_map.get(str(r.id)),
            })

    await log_operator(db, session["sub"], "get_attendance", {"exam_id": session["exam_id"]})
    return result


# ── Check endpoints (operator_session auth, match exams) ─────────────────────

@router.post("/check-photo")
async def check_photo(
    candidate_no: str = Form(...), file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db), session: dict = Depends(require_operator_session)
):
    exam = await db.get(Exam, UUID(session["exam_id"]))
    if not exam or exam.type != "match":
        raise HTTPException(400, "Photo check not allowed for non-match exam")

    cm = await _get_match_by_session(db, session, candidate_no)
    if not cm.photo_id:
        raise HTTPException(400, "No reference photo for this candidate")

    ref_photo = await db.get(Photo, cm.photo_id)
    if not ref_photo:
        raise HTTPException(400, "Reference photo record missing")

    center_code = await _get_center_code(db, UUID(session["center_id"]))
    raw = await file.read()
    live_photo = Photo()
    db.add(live_photo)
    await db.flush()
    live_photo.file_path = await save_photo(
        session["exam_id"], center_code, candidate_no, str(live_photo.id), raw
    )
    await db.flush()

    ref_bytes, live_bytes = await asyncio.gather(
        asyncio.to_thread(read_file, ref_photo.file_path),
        asyncio.to_thread(read_file, live_photo.file_path),
    )
    matched = await match_photos(ref_bytes, live_bytes) if ref_bytes and live_bytes else False

    cm.attendance          = "present"
    cm.attendance_marked_at = datetime.utcnow()
    cm.new_photo_id        = live_photo.id
    cm.photo_match_status  = "match" if matched else "mismatch"
    cm.matched_at          = datetime.utcnow()
    await db.flush()
    await log_operator(db, session["sub"], "check_photo",
                       {"candidate_no": candidate_no, "matched": matched})
    return {"status": "ok", "matched": matched}


@router.post("/check-fingerprint")
async def check_fingerprint(
    candidate_no: str = Form(...), file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db), session: dict = Depends(require_operator_session)
):
    exam = await db.get(Exam, UUID(session["exam_id"]))
    if not exam or exam.type != "match":
        raise HTTPException(400, "Fingerprint check not allowed for non-match exam")

    cm = await _get_match_by_session(db, session, candidate_no)
    if cm.photo_match_status is None:
        raise HTTPException(400, "Photo must be checked first")
    if not cm.fingerprint_id:
        raise HTTPException(400, "No reference fingerprint for this candidate")

    ref_fp = await db.get(Fingerprint, cm.fingerprint_id)
    if not ref_fp:
        raise HTTPException(400, "Reference fingerprint record missing")
    if not ref_fp.template:
        raise HTTPException(400, "Reference fingerprint template not yet extracted; retry shortly")

    center_code = await _get_center_code(db, UUID(session["center_id"]))
    raw = await file.read()
    new_fp = Fingerprint()
    db.add(new_fp)
    await db.flush()
    new_fp.file_path = await save_fingerprint(
        session["exam_id"], center_code, candidate_no, str(new_fp.id), raw
    )
    await db.flush()

    fp_bytes = await asyncio.to_thread(read_file, new_fp.file_path)
    tmpl = await extract_fingerprint_template(fp_bytes) if fp_bytes else None
    matched = False
    if tmpl:
        new_fp.template = tmpl
        matched = await match_fingerprints(ref_fp.template, tmpl)

    cm.new_fingerprint_id       = new_fp.id
    cm.fingerprint_match_status = "match" if matched else "mismatch"
    cm.fingerprint_matched_at   = datetime.utcnow()
    await db.flush()
    await log_operator(db, session["sub"], "check_fingerprint",
                       {"candidate_no": candidate_no, "matched": matched})
    return {"status": "ok", "matched": matched}


@router.post("/check-iris")
async def check_iris(
    candidate_no: str = Form(...), file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db), session: dict = Depends(require_operator_session)
):
    exam = await db.get(Exam, UUID(session["exam_id"]))
    if not exam or exam.type != "match":
        raise HTTPException(400, "Iris check not allowed for non-match exam")

    cm = await _get_match_by_session(db, session, candidate_no)
    if cm.photo_match_status is None:
        raise HTTPException(400, "Photo must be checked first")
    if not cm.iris_id:
        raise HTTPException(400, "No reference iris for this candidate")

    ref_iris = await db.get(Iris, cm.iris_id)
    if not ref_iris:
        raise HTTPException(400, "Reference iris record missing")
    if not ref_iris.template:
        raise HTTPException(400, "Reference iris template not yet extracted; retry shortly")

    center_code = await _get_center_code(db, UUID(session["center_id"]))
    raw = await file.read()
    new_iris = Iris()
    db.add(new_iris)
    await db.flush()
    new_iris.file_path = await save_iris(
        session["exam_id"], center_code, candidate_no, str(new_iris.id), raw
    )
    await db.flush()

    iris_bytes = await asyncio.to_thread(read_file, new_iris.file_path)
    tmpl = await extract_iris_template(iris_bytes) if iris_bytes else None
    matched = False
    if tmpl:
        new_iris.template = tmpl
        matched = await match_iris(ref_iris.template, tmpl)

    cm.new_iris_id       = new_iris.id
    cm.iris_match_status = "match" if matched else "mismatch"
    cm.iris_matched_at   = datetime.utcnow()
    await db.flush()
    await log_operator(db, session["sub"], "check_iris",
                       {"candidate_no": candidate_no, "matched": matched})
    return {"status": "ok", "matched": matched}


# ── Sync attendance (sync_key auth, capture exams) ────────────────────────────

@router.post("/sync-attendance")
async def sync_attendance(
    items: List[dict],
    exam: Exam = Depends(_exam_by_sync_key),
    db: AsyncSession = Depends(get_db)
):
    """Mark attendance for multiple candidates.
    For capture exams: update all CandidateCapture rows for the candidate.
    For match exams: attendance is set on /check-photo, skip here.
    """
    results = []
    for item in items:
        candidate_no = item.get("candidate_no", "")
        shift_id     = item.get("shift_id")
        center_id    = item.get("center_id")
        try:
            if exam.type == "match":
                results.append({"candidate_no": candidate_no, "status": "skipped_match_exam"})
                continue
            if not shift_id or not center_id:
                results.append({"candidate_no": candidate_no, "status": "missing_fields"})
                continue
            updated = await db.execute(
                update(CandidateCapture)
                .where(
                    CandidateCapture.exam_id == exam.id,
                    CandidateCapture.shift_id == UUID(str(shift_id)),
                    CandidateCapture.center_id == UUID(str(center_id)),
                    CandidateCapture.candidate_no_plain == candidate_no,
                )
                .values(attendance="present", attendance_marked_at=datetime.utcnow())
            )
            if updated.rowcount == 0:
                results.append({"candidate_no": candidate_no, "status": "not_found"})
            else:
                results.append({"candidate_no": candidate_no, "status": "ok"})
        except Exception as e:
            results.append({"candidate_no": candidate_no, "status": "error", "detail": str(e)})
    await db.flush()
    return {"synced": sum(1 for r in results if r["status"] == "ok"), "results": results}


# ── Attendance summary ────────────────────────────────────────────────────────

@router.get("/attendance-summary")
async def get_attendance_summary(
    db: AsyncSession = Depends(get_db),
    session: dict = Depends(require_operator_session)
):
    exam_id   = UUID(session["exam_id"])
    shift_id  = UUID(session["shift_id"])
    center_id = UUID(session["center_id"])
    exam = await db.get(Exam, exam_id)

    if not exam or exam.type == "capture":
        # Distinct total from CandidateCapture
        all_rows = (await db.execute(
            select(CandidateCapture.candidate_no_plain, CandidateCapture.attendance,
                   CandidateCapture.new_photo_id, CandidateCapture.fingerprint_id,
                   CandidateCapture.iris_id)
            .where(CandidateCapture.exam_id == exam_id,
                   CandidateCapture.shift_id == shift_id,
                   CandidateCapture.center_id == center_id)
        )).all()

        grouped: dict = defaultdict(list)
        for cno, att, pid, fid, iid in all_rows:
            grouped[cno].append((att, pid, fid, iid))

        total = len(grouped)
        attended = sum(1 for rows in grouped.values() if any(a == "present" for a, *_ in rows))
        photo_captured = sum(1 for rows in grouped.values() if any(pid for _, pid, *_ in rows))
        fp_captured    = sum(1 for rows in grouped.values() if any(fid for _, _, fid, _ in rows))
        iris_captured  = sum(1 for rows in grouped.values() if any(iid for _, _, _, iid in rows))
    else:
        rows = (await db.execute(
            select(CandidateMatch.attendance, CandidateMatch.new_photo_id,
                   CandidateMatch.new_fingerprint_id, CandidateMatch.new_iris_id)
            .where(CandidateMatch.exam_id == exam_id,
                   CandidateMatch.shift_id == shift_id,
                   CandidateMatch.center_id == center_id)
        )).all()
        total    = len(rows)
        attended = sum(1 for a, *_ in rows if a == "present")
        photo_captured = sum(1 for _, p, *_ in rows if p)
        fp_captured    = sum(1 for _, _, f, _ in rows if f)
        iris_captured  = sum(1 for _, _, _, i in rows if i)

    await log_operator(db, session["sub"], "get_attendance_summary", {"exam_id": session["exam_id"]})
    return {
        "total":                total,
        "attended":             attended,
        "not_attended":         total - attended,
        "photo_captured":       photo_captured,
        "fingerprint_captured": fp_captured,
        "iris_captured":        iris_captured,
    }


# ── Exam QR regex ─────────────────────────────────────────────────────────────

@router.get("/exam-qr-regex")
async def get_exam_qr_regex(exam: Exam = Depends(_exam_by_sync_key),
                             db: AsyncSession = Depends(get_db)):
    return {"qr_string": exam.qr_string or ""}
