"""Admin endpoints."""

import asyncio
import base64
import io
import uuid as uuid_module
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db
from models import (
    Exam, Shift, Center, Operator, Supervisor, Photo,
    Fingerprint, Iris, CandidateCapture, CandidateMatch, DeviceSession,
    AuthLog, AdminLog, SupervisorLog, OperatorLog,
)
from schemas import (
    AdminLogin, TokenResponse, CenterCreate, CenterOut,
    ShiftCreate, ShiftOut, ExamCreate, ExamOut, ExamEdit,
    CandidateAdd, CandidateAttendanceOut, CandidateCaptureMatchOut,
    CandidateMatchDetailOut, CenterSummaryRow,
    ResolveDuplicateBody, AuthLogOut,
)
from utils.auth import create_token, require_admin, hash_password
from utils.biometric_storage import (
    read_file, preferred_photo_bytes, preferred_fp_bytes, preferred_iris_bytes,
    save_photo, save_fingerprint, save_iris,
)
from utils.export import generate_csv, generate_pdf, generate_matching_csv, generate_matching_pdf
from utils.logger import log_admin, log_auth

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _b64_photo(photos: dict, photo_id) -> Optional[str]:
    if not photo_id:
        return None
    p = photos.get(photo_id)
    data = preferred_photo_bytes(p) if p else None
    return base64.b64encode(data).decode() if data else None


def _b64_fp(fps: dict, fp_id) -> Optional[str]:
    if not fp_id:
        return None
    f = fps.get(fp_id)
    data = preferred_fp_bytes(f) if f else None
    return base64.b64encode(data).decode() if data else None


def _b64_iris(irises: dict, iris_id) -> Optional[str]:
    if not iris_id:
        return None
    i = irises.get(iris_id)
    data = preferred_iris_bytes(i) if i else None
    return base64.b64encode(data).decode() if data else None


async def _b64_map(objs: dict, bytes_fn) -> dict:
    """Read all model objects' files in parallel; return {id: base64_str_or_None}."""
    if not objs:
        return {}
    ids = list(objs.keys())

    async def _safe(obj):
        try:
            data = await asyncio.to_thread(bytes_fn, obj)
            return base64.b64encode(data).decode() if data else None
        except Exception:
            return None

    results = await asyncio.gather(*[_safe(objs[k]) for k in ids])
    return dict(zip(ids, results))


async def _attendance_data(db, exam_id: UUID, center_id=None, shift_id=None):
    """Return list of CandidateAttendanceOut. Used by admin and supervisor."""
    exam = await db.get(Exam, exam_id)
    if not exam:
        return []

    if exam.type == "capture":
        q = (select(CandidateCapture, Center, Shift)
             .join(Center, CandidateCapture.center_id == Center.id)
             .join(Shift, CandidateCapture.shift_id == Shift.id)
             .where(CandidateCapture.exam_id == exam_id))
        if center_id:
            q = q.where(CandidateCapture.center_id == center_id)
        if shift_id:
            q = q.where(CandidateCapture.shift_id == shift_id)
        rows = (await db.execute(q)).all()

        # One entry per distinct (center, shift, candidate_no_plain)
        seen: dict = {}
        for cc, ctr, shf in rows:
            key = (cc.center_id, cc.shift_id, cc.candidate_no_plain)
            if key not in seen:
                seen[key] = {"cc": cc, "ctr": ctr, "shf": shf,
                             "attended": False, "marked_at": None}
            if cc.attendance == "present":
                seen[key]["attended"] = True
                seen[key]["marked_at"] = cc.attendance_marked_at

        photo_ids = {v["cc"].photo_id for v in seen.values() if v["cc"].photo_id}
        photos = {}
        if photo_ids:
            photos = {p.id: p for p in (await db.execute(
                select(Photo).where(Photo.id.in_(photo_ids))
            )).scalars().all()}

        photo_b64 = await _b64_map(photos, preferred_photo_bytes)
        return [
            CandidateAttendanceOut(
                candidate_no=v["cc"].candidate_no,
                name=v["cc"].name,
                roll_no=v["cc"].roll_no,
                father_name=v["cc"].father_name,
                dob=v["cc"].dob,
                attended=v["attended"],
                attendance_marked_at=v["marked_at"],
                center_code=v["ctr"].code,
                shift_code=v["shf"].shift_code,
                photo_data=photo_b64.get(v["cc"].photo_id),
            )
            for v in seen.values()
        ]
    else:
        q = (select(CandidateMatch, Center, Shift)
             .join(Center, CandidateMatch.center_id == Center.id)
             .join(Shift, CandidateMatch.shift_id == Shift.id)
             .where(CandidateMatch.exam_id == exam_id))
        if center_id:
            q = q.where(CandidateMatch.center_id == center_id)
        if shift_id:
            q = q.where(CandidateMatch.shift_id == shift_id)
        rows = (await db.execute(q)).all()

        photo_ids = {cm.photo_id for cm, _, _ in rows if cm.photo_id}
        photos = {}
        if photo_ids:
            photos = {p.id: p for p in (await db.execute(
                select(Photo).where(Photo.id.in_(photo_ids))
            )).scalars().all()}

        photo_b64 = await _b64_map(photos, preferred_photo_bytes)
        return [
            CandidateAttendanceOut(
                candidate_no=cm.candidate_no,
                name=cm.name,
                roll_no=cm.roll_no,
                father_name=cm.father_name,
                dob=cm.dob,
                attended=cm.attendance == "present",
                attendance_marked_at=cm.attendance_marked_at,
                center_code=ctr.code,
                shift_code=shf.shift_code,
                photo_data=photo_b64.get(cm.photo_id),
            )
            for cm, ctr, shf in rows
        ]


async def _build_match_rows(db, exam_id: UUID, center_id=None, shift_id=None):
    """Return matching status rows. Used by admin and supervisor."""
    exam = await db.get(Exam, exam_id)
    if not exam:
        return []

    if exam.type == "capture":
        q = (select(CandidateCapture, Center, Shift)
             .join(Center, CandidateCapture.center_id == Center.id)
             .join(Shift, CandidateCapture.shift_id == Shift.id)
             .where(CandidateCapture.exam_id == exam_id)
             .order_by(CandidateCapture.candidate_no_plain, CandidateCapture.created_at.asc()))
        if center_id:
            q = q.where(CandidateCapture.center_id == center_id)
        if shift_id:
            q = q.where(CandidateCapture.shift_id == shift_id)
        rows = (await db.execute(q)).all()

        # Group by (center_id, shift_id, candidate_no_plain)
        groups: dict = {}
        for cc, ctr, shf in rows:
            key = (cc.center_id, cc.shift_id, cc.candidate_no_plain)
            if key not in groups:
                groups[key] = {"ctr": ctr, "shf": shf, "seed": None, "subs": []}
            if cc.new_photo_id is None:
                if groups[key]["seed"] is None:
                    groups[key]["seed"] = cc
            else:
                groups[key]["subs"].append(cc)

        photo_ids: set = set()
        fp_ids: set = set()
        iris_ids: set = set()
        for g in groups.values():
            seed = g["seed"]
            if seed and seed.photo_id:
                photo_ids.add(seed.photo_id)
            for sub in g["subs"]:
                if sub.new_photo_id:
                    photo_ids.add(sub.new_photo_id)
                if sub.fingerprint_id:
                    fp_ids.add(sub.fingerprint_id)
                if sub.iris_id:
                    iris_ids.add(sub.iris_id)

        photos = {p.id: p for p in (await db.execute(select(Photo).where(Photo.id.in_(photo_ids)))).scalars().all()} if photo_ids else {}
        fps = {f.id: f for f in (await db.execute(select(Fingerprint).where(Fingerprint.id.in_(fp_ids)))).scalars().all()} if fp_ids else {}
        irises = {i.id: i for i in (await db.execute(select(Iris).where(Iris.id.in_(iris_ids)))).scalars().all()} if iris_ids else {}

        photo_b64 = await _b64_map(photos, preferred_photo_bytes)
        fp_b64 = await _b64_map(fps, preferred_fp_bytes)
        iris_b64 = await _b64_map(irises, preferred_iris_bytes)

        result = []
        for g in groups.values():
            seed = g["seed"]
            subs = g["subs"]
            ctr, shf = g["ctr"], g["shf"]
            # Latest submission is last (ordered by created_at asc)
            latest = subs[-1] if subs else None
            ref = seed or (subs[0] if subs else None)
            if ref is None:
                continue
            attended = any(s.attendance == "present" for s in subs)
            attended = attended or (seed.attendance == "present" if seed else False)
            result.append(CandidateCaptureMatchOut(
                capture_id=latest.id if latest else ref.id,
                candidate_no=ref.candidate_no,
                name=ref.name,
                roll_no=ref.roll_no,
                dob=ref.dob,
                attended=attended,
                center_code=ctr.code,
                shift_code=shf.shift_code,
                photo_data=photo_b64.get(seed.photo_id if seed else None),
                captured_photo_data=photo_b64.get(latest.new_photo_id if latest else None),
                photo_match_status=latest.photo_match_status if latest else None,
                fingerprint_data=fp_b64.get(latest.fingerprint_id if latest else None),
                iris_data=iris_b64.get(latest.iris_id if latest else None),
            ))
        return result

    else:
        q = (select(CandidateMatch, Center, Shift)
             .join(Center, CandidateMatch.center_id == Center.id)
             .join(Shift, CandidateMatch.shift_id == Shift.id)
             .where(CandidateMatch.exam_id == exam_id))
        if center_id:
            q = q.where(CandidateMatch.center_id == center_id)
        if shift_id:
            q = q.where(CandidateMatch.shift_id == shift_id)
        rows = (await db.execute(q)).all()

        photo_ids: set = set()
        fp_ids: set = set()
        iris_ids: set = set()
        for cm, _, _ in rows:
            for pid in [cm.photo_id, cm.new_photo_id]:
                if pid:
                    photo_ids.add(pid)
            for fid in [cm.fingerprint_id, cm.new_fingerprint_id]:
                if fid:
                    fp_ids.add(fid)
            for iid in [cm.iris_id, cm.new_iris_id]:
                if iid:
                    iris_ids.add(iid)

        photos = {p.id: p for p in (await db.execute(select(Photo).where(Photo.id.in_(photo_ids)))).scalars().all()} if photo_ids else {}
        fps = {f.id: f for f in (await db.execute(select(Fingerprint).where(Fingerprint.id.in_(fp_ids)))).scalars().all()} if fp_ids else {}
        irises = {i.id: i for i in (await db.execute(select(Iris).where(Iris.id.in_(iris_ids)))).scalars().all()} if iris_ids else {}

        photo_b64 = await _b64_map(photos, preferred_photo_bytes)
        fp_b64 = await _b64_map(fps, preferred_fp_bytes)
        iris_b64 = await _b64_map(irises, preferred_iris_bytes)

        return [
            CandidateMatchDetailOut(
                candidate_id=cm.id,
                candidate_no=cm.candidate_no,
                name=cm.name,
                roll_no=cm.roll_no,
                dob=cm.dob,
                attended=cm.attendance == "present",
                attendance_marked_at=cm.attendance_marked_at,
                center_code=ctr.code,
                shift_code=shf.shift_code,
                photo_data=photo_b64.get(cm.photo_id),
                captured_photo_data=photo_b64.get(cm.new_photo_id),
                photo_match_status=cm.photo_match_status,
                fingerprint_data=fp_b64.get(cm.fingerprint_id),
                live_fingerprint_data=fp_b64.get(cm.new_fingerprint_id),
                fingerprint_match_status=cm.fingerprint_match_status,
                iris_data=iris_b64.get(cm.iris_id),
                live_iris_data=iris_b64.get(cm.new_iris_id),
                iris_match_status=cm.iris_match_status,
            )
            for cm, ctr, shf in rows
        ]


async def _centers_summary_data(db, exam_id: UUID, shift_id=None):
    """Return list of CenterSummaryRow. Used by admin and supervisor."""
    exam = await db.get(Exam, exam_id)
    if not exam:
        return []

    active_threshold = datetime.utcnow() - timedelta(seconds=120)
    active_raw = (await db.execute(
        select(DeviceSession.center_id, DeviceSession.shift_id,
               func.count(DeviceSession.id).label("cnt"))
        .where(DeviceSession.exam_id == exam_id,
               DeviceSession.last_heartbeat > active_threshold)
        .group_by(DeviceSession.center_id, DeviceSession.shift_id)
    )).all()
    active_map = {(r.center_id, r.shift_id): r.cnt for r in active_raw}

    if exam.type == "capture":
        # Per-candidate aggregation (distinct candidates may have multiple rows)
        inner_q = (
            select(
                CandidateCapture.center_id,
                CandidateCapture.shift_id,
                CandidateCapture.candidate_no_plain,
                func.max(case(
                    (CandidateCapture.attendance == "present", 1), else_=0
                )).label("attended"),
                func.max(case(
                    (CandidateCapture.photo_match_status == "match", 1), else_=0
                )).label("is_match"),
                func.max(case(
                    (CandidateCapture.photo_match_status == "mismatch", 1), else_=0
                )).label("is_mismatch"),
                func.count(case(
                    (CandidateCapture.new_photo_id.isnot(None), 1), else_=None
                )).label("sub_count"),
            )
            .where(CandidateCapture.exam_id == exam_id)
            .group_by(CandidateCapture.center_id, CandidateCapture.shift_id,
                      CandidateCapture.candidate_no_plain)
        )
        if shift_id:
            inner_q = inner_q.where(CandidateCapture.shift_id == shift_id)
        inner = inner_q.subquery()

        outer_q = (
            select(
                inner.c.center_id,
                inner.c.shift_id,
                func.count().label("total"),
                func.sum(inner.c.attended).label("present"),
                func.sum(inner.c.is_match).label("matched"),
                func.sum(inner.c.is_mismatch).label("mismatched"),
                func.count(case((inner.c.sub_count > 1, 1), else_=None)).label("duplicates"),
            )
            .group_by(inner.c.center_id, inner.c.shift_id)
        )
        agg_rows = (await db.execute(outer_q)).all()
        if not agg_rows:
            return []

    else:
        group_q = (
            select(
                CandidateMatch.center_id,
                CandidateMatch.shift_id,
                func.count().label("total"),
                func.count(case((CandidateMatch.attendance == "present", 1), else_=None)).label("present"),
                func.count(case((CandidateMatch.photo_match_status == "match", 1), else_=None)).label("matched"),
                func.count(case((CandidateMatch.photo_match_status == "mismatch", 1), else_=None)).label("mismatched"),
                func.count(case((CandidateMatch.fingerprint_match_status == "match", 1), else_=None)).label("fp_matched"),
                func.count(case((CandidateMatch.fingerprint_match_status == "mismatch", 1), else_=None)).label("fp_mismatched"),
                func.count(case((CandidateMatch.iris_match_status == "match", 1), else_=None)).label("iris_matched"),
                func.count(case((CandidateMatch.iris_match_status == "mismatch", 1), else_=None)).label("iris_mismatched"),
            )
            .where(CandidateMatch.exam_id == exam_id)
            .group_by(CandidateMatch.center_id, CandidateMatch.shift_id)
        )
        if shift_id:
            group_q = group_q.where(CandidateMatch.shift_id == shift_id)
        agg_rows = (await db.execute(group_q)).all()
        if not agg_rows:
            return []

    # Batch-load center and shift metadata
    ctr_ids = {r.center_id for r in agg_rows}
    shf_ids = {r.shift_id for r in agg_rows}
    centers = {c.id: c for c in (await db.execute(
        select(Center).where(Center.id.in_(ctr_ids))
    )).scalars().all()}
    shifts = {s.id: s for s in (await db.execute(
        select(Shift).where(Shift.id.in_(shf_ids))
    )).scalars().all()}

    result = []
    for r in agg_rows:
        ctr = centers.get(r.center_id)
        shf = shifts.get(r.shift_id)
        present = int(r.present or 0)
        total = int(r.total or 0)
        if exam.type == "capture":
            result.append(CenterSummaryRow(
                center_id=r.center_id, center_name=ctr.name if ctr else "",
                center_code=ctr.code if ctr else "",
                shift_id=r.shift_id, shift_code=shf.shift_code if shf else None,
                supervisor_name=ctr.supervisor_name if ctr else None,
                vendor_name=ctr.vendor_name if ctr else None,
                total=total, present=present, absent=total - present,
                matched=int(r.matched or 0), mismatched=int(r.mismatched or 0),
                duplicates=int(r.duplicates or 0),
                active_devices=active_map.get((r.center_id, r.shift_id), 0),
            ))
        else:
            result.append(CenterSummaryRow(
                center_id=r.center_id, center_name=ctr.name if ctr else "",
                center_code=ctr.code if ctr else "",
                shift_id=r.shift_id, shift_code=shf.shift_code if shf else None,
                supervisor_name=ctr.supervisor_name if ctr else None,
                vendor_name=ctr.vendor_name if ctr else None,
                total=total, present=present, absent=total - present,
                matched=int(r.matched or 0), mismatched=int(r.mismatched or 0), duplicates=0,
                fp_matched=int(r.fp_matched or 0), fp_mismatched=int(r.fp_mismatched or 0),
                iris_matched=int(r.iris_matched or 0), iris_mismatched=int(r.iris_mismatched or 0),
                active_devices=active_map.get((r.center_id, r.shift_id), 0),
            ))
    return result


async def _delete_capture_submission(db, row: CandidateCapture):
    """Delete a CandidateCapture submission row plus its biometric records and disk files."""

    photo_id = row.new_photo_id
    fingerprint_id = row.fingerprint_id
    iris_id = row.iris_id

    # ✅ 1. DELETE ROW FIRST (break FK)
    await db.delete(row)
    await db.flush()

    # ✅ 2. Now safe to delete photo
    if photo_id:
        photo = await db.get(Photo, photo_id)
        if photo:
            for path in [photo.file_path, photo.compressed_file_path]:
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except OSError:
                        pass
            await db.delete(photo)
            await db.flush()

    # ✅ 3. Fingerprint
    if fingerprint_id:
        fp = await db.get(Fingerprint, fingerprint_id)
        if fp:
            for path in [fp.file_path, fp.compressed_file_path]:
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except OSError:
                        pass
            await db.delete(fp)
            await db.flush()

    # ✅ 4. Iris
    if iris_id:
        iris = await db.get(Iris, iris_id)
        if iris:
            for path in [iris.file_path, iris.compressed_file_path]:
                if path:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except OSError:
                        pass
            await db.delete(iris)
            await db.flush()


# ── Login ──────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def admin_login(body: AdminLogin, response: Response, db: AsyncSession = Depends(get_db)):
    s = get_settings()
    if body.id != s.ADMIN_ID or body.password != s.ADMIN_PASSWORD:
        raise HTTPException(401, "Invalid credentials")
    token = create_token({"role": "admin", "sub": body.id}, expires_minutes=1440)
    response.set_cookie("auth_token", token, httponly=True, samesite="lax", max_age=86400)
    await log_auth(db, mobile=body.id, role="admin")
    await log_admin(db, body.id, "login", "Admin logged in")
    return TokenResponse(access_token=token)


@router.get("/me")
async def admin_me(admin: dict = Depends(require_admin)):
    return {"sub": admin["sub"], "role": admin["role"]}


@router.post("/logout")
async def admin_logout(response: Response):
    response.set_cookie("auth_token", "", max_age=0, httponly=True, samesite="lax")
    return {"status": "logged_out"}


# ── Centers ────────────────────────────────────────────────────────────────────

@router.post("/centers", response_model=CenterOut)
async def create_center(body: CenterCreate, db: AsyncSession = Depends(get_db),
                         admin: dict = Depends(require_admin)):
    if await db.scalar(select(Center).where(Center.code_plain == body.code)):
        raise HTTPException(400, "Center code already exists")
    c = Center(code=body.code, code_plain=body.code, name=body.name, address=body.address,
               supervisor_name=body.supervisor_name, vendor_name=body.vendor_name)
    db.add(c)
    await db.flush()
    await log_admin(db, admin["sub"], "create_center", {"code": body.code})
    return CenterOut(id=c.id, code=body.code, name=body.name, address=body.address,
                     supervisor_name=body.supervisor_name, vendor_name=body.vendor_name,
                     created_at=c.created_at)


@router.get("/centers", response_model=list[CenterOut])
async def get_centers(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    rows = (await db.execute(select(Center))).scalars().all()
    return [CenterOut(id=r.id, code=r.code, name=r.name, address=r.address,
                      supervisor_name=r.supervisor_name, vendor_name=r.vendor_name,
                      created_at=r.created_at)
            for r in rows]


# ── Shifts ─────────────────────────────────────────────────────────────────────

@router.post("/shifts", response_model=ShiftOut)
async def create_shift(body: ShiftCreate, db: AsyncSession = Depends(get_db),
                        admin: dict = Depends(require_admin)):
    if await db.scalar(select(Shift).where(Shift.date == body.date,
                                           Shift.start_time == body.start_time)):
        raise HTTPException(400, "Shift with this date+time already exists")
    s = Shift(shift_code=body.shift_code, date=body.date, start_time=body.start_time)
    db.add(s)
    await db.flush()
    await log_admin(db, admin["sub"], "create_shift",
                    {"shift_code": body.shift_code, "date": str(body.date), "time": str(body.start_time)})
    return ShiftOut(id=s.id, shift_code=s.shift_code, date=s.date, start_time=s.start_time,
                    created_at=s.created_at)


@router.get("/shifts", response_model=list[ShiftOut])
async def get_shifts(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    rows = (await db.execute(select(Shift))).scalars().all()
    return [ShiftOut(id=r.id, shift_code=r.shift_code, date=r.date, start_time=r.start_time,
                     created_at=r.created_at) for r in rows]


# ── Exams ──────────────────────────────────────────────────────────────────────

@router.post("/exams", response_model=ExamOut)
async def create_exam(body: ExamCreate, db: AsyncSession = Depends(get_db),
                       admin: dict = Depends(require_admin)):
    name_lower = body.name.strip().lower()
    if await db.scalar(select(Exam).where(Exam.name_lower == name_lower)):
        raise HTTPException(400, "Exam name already exists")
    sync_key = Fernet.generate_key().decode()
    exam = Exam(name=body.name, name_lower=name_lower, type=body.type,
                qr_string=body.qr_string, sync_key=sync_key, sync_key_plain=sync_key)
    db.add(exam)
    await db.flush()
    for i in range(body.num_supervisors):
        if await db.scalar(select(Supervisor).where(Supervisor.phone_plain == body.supervisor_phones[i])):
            raise HTTPException(400, f"Supervisor phone {body.supervisor_phones[i]} already exists")
        db.add(Supervisor(phone=body.supervisor_phones[i], phone_plain=body.supervisor_phones[i],
                          password=hash_password(body.supervisor_passwords[i]), exam_id=exam.id))
    for i in range(body.num_operators):
        if await db.scalar(select(Operator).where(Operator.phone_plain == body.operator_phones[i])):
            raise HTTPException(400, f"Operator phone {body.operator_phones[i]} already exists")
        db.add(Operator(phone=body.operator_phones[i], phone_plain=body.operator_phones[i],
                        password=hash_password(body.operator_passwords[i]), exam_id=exam.id))
    await db.flush()
    await log_admin(db, admin["sub"], "create_exam", {"name": body.name, "type": body.type})
    return ExamOut(id=exam.id, name=exam.name, type=exam.type, qr_string=exam.qr_string,
                   qr_data=exam.qr_data, archived=exam.archived, archived_at=exam.archived_at,
                   sync_key=exam.sync_key, created_at=exam.created_at)


@router.put("/exams/{exam_id}", response_model=ExamOut)
async def edit_exam(exam_id: UUID, body: ExamEdit, db: AsyncSession = Depends(get_db),
                     admin: dict = Depends(require_admin)):
    exam = await db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Exam not found")
    if body.archived is not None:
        exam.archived = body.archived
        exam.archived_at = datetime.utcnow() if body.archived else None
    if body.qr_string is not None:
        exam.qr_string = body.qr_string
    await db.flush()
    await log_admin(db, admin["sub"], "edit_exam", {"exam_id": str(exam_id)})
    return ExamOut(id=exam.id, name=exam.name, type=exam.type, qr_string=exam.qr_string,
                   qr_data=exam.qr_data, archived=exam.archived, archived_at=exam.archived_at,
                   sync_key=exam.sync_key, created_at=exam.created_at)


@router.get("/exams", response_model=list[ExamOut])
async def get_exams(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    rows = (await db.execute(select(Exam))).scalars().all()
    return [ExamOut(id=r.id, name=r.name, type=r.type, qr_string=r.qr_string, qr_data=r.qr_data,
                    archived=r.archived, archived_at=r.archived_at, sync_key=r.sync_key,
                    created_at=r.created_at) for r in rows]


# ── Add Candidate ──────────────────────────────────────────────────────────────

@router.post("/candidates")
async def add_candidate(body: CandidateAdd, db: AsyncSession = Depends(get_db),
                         admin: dict = Depends(require_admin)):
    exam = await db.get(Exam, body.exam_id)
    if not exam:
        raise HTTPException(404, "Exam not found")

    # Upsert Center
    center = await db.scalar(select(Center).where(Center.code_plain == body.center_code))
    if not center:
        center = Center(code=body.center_code, code_plain=body.center_code,
                        name=body.center_name, address=body.center_address,
                        supervisor_name=body.supervisor_name, vendor_name=body.vendor_name)
        db.add(center)
        await db.flush()

    # Upsert Shift
    shift = await db.scalar(select(Shift).where(Shift.date == body.shift_date,
                                                Shift.start_time == body.shift_start_time))
    if not shift:
        shift = Shift(shift_code=body.shift_code, date=body.shift_date,
                      start_time=body.shift_start_time)
        db.add(shift)
        await db.flush()

    if exam.type == "capture":
        # Check for existing seed row; update identity if found, else create
        existing = await db.scalar(
            select(CandidateCapture).where(
                CandidateCapture.exam_id == body.exam_id,
                CandidateCapture.shift_id == shift.id,
                CandidateCapture.center_id == center.id,
                CandidateCapture.candidate_no_plain == body.candidate_no,
                CandidateCapture.new_photo_id.is_(None),
            )
        )
        photo_id = None
        if body.photo_base64:
            photo = Photo()
            db.add(photo)
            await db.flush()
            photo_data = base64.b64decode(body.photo_base64)
            photo.file_path = await save_photo(
                str(body.exam_id), body.center_code, body.candidate_no, str(photo.id), photo_data
            )
            photo_id = photo.id

        if existing:
            existing.name = body.name
            existing.roll_no = body.roll_no
            existing.roll_no_plain = body.roll_no
            existing.father_name = body.father_name
            existing.mother_name = body.mother_name
            existing.dob = body.dob
            if photo_id:
                existing.photo_id = photo_id
            row_id = existing.id
        else:
            seed = CandidateCapture(
                exam_id=body.exam_id, shift_id=shift.id, center_id=center.id,
                candidate_no=body.candidate_no, candidate_no_plain=body.candidate_no,
                name=body.name, roll_no=body.roll_no, roll_no_plain=body.roll_no,
                father_name=body.father_name, mother_name=body.mother_name, dob=body.dob,
                photo_id=photo_id,
            )
            db.add(seed)
            await db.flush()
            row_id = seed.id

    else:
        # Match exam: upsert CandidateMatch (unique on exam+shift+center+candidate_no_plain)
        existing = await db.scalar(
            select(CandidateMatch).where(
                CandidateMatch.exam_id == body.exam_id,
                CandidateMatch.shift_id == shift.id,
                CandidateMatch.center_id == center.id,
                CandidateMatch.candidate_no_plain == body.candidate_no,
            )
        )

        photo_id = None
        if body.photo_base64:
            photo = Photo()
            db.add(photo)
            await db.flush()
            photo_data = base64.b64decode(body.photo_base64)
            photo.file_path = await save_photo(
                str(body.exam_id), body.center_code, body.candidate_no, str(photo.id), photo_data
            )
            photo_id = photo.id

        fp_id = None
        if body.fingerprint_base64:
            fp = Fingerprint()
            db.add(fp)
            await db.flush()
            fp_data = base64.b64decode(body.fingerprint_base64)
            fp.file_path = await save_fingerprint(
                str(body.exam_id), body.center_code, body.candidate_no, str(fp.id), fp_data
            )
            fp_id = fp.id

        iris_id = None
        if body.iris_base64:
            iris = Iris()
            db.add(iris)
            await db.flush()
            iris_data = base64.b64decode(body.iris_base64)
            iris.file_path = await save_iris(
                str(body.exam_id), body.center_code, body.candidate_no, str(iris.id), iris_data
            )
            iris_id = iris.id

        if existing:
            existing.name = body.name
            existing.roll_no = body.roll_no
            existing.roll_no_plain = body.roll_no
            existing.father_name = body.father_name
            existing.mother_name = body.mother_name
            existing.dob = body.dob
            if photo_id:
                old_photo_id = existing.photo_id
                existing.photo_id = photo_id
                await db.flush()
                if old_photo_id:
                    old_photo = await db.get(Photo, old_photo_id)
                    if old_photo:
                        for path in [old_photo.file_path, old_photo.compressed_file_path]:
                            if path:
                                try:
                                    Path(path).unlink(missing_ok=True)
                                except OSError:
                                    pass
                        await db.delete(old_photo)
                        await db.flush()
            if fp_id:
                old_fp_id = existing.fingerprint_id
                existing.fingerprint_id = fp_id
                await db.flush()
                if old_fp_id:
                    old_fp = await db.get(Fingerprint, old_fp_id)
                    if old_fp:
                        for path in [old_fp.file_path, old_fp.compressed_file_path]:
                            if path:
                                try:
                                    Path(path).unlink(missing_ok=True)
                                except OSError:
                                    pass
                        await db.delete(old_fp)
                        await db.flush()
            if iris_id:
                old_iris_id = existing.iris_id
                existing.iris_id = iris_id
                await db.flush()
                if old_iris_id:
                    old_iris = await db.get(Iris, old_iris_id)
                    if old_iris:
                        for path in [old_iris.file_path, old_iris.compressed_file_path]:
                            if path:
                                try:
                                    Path(path).unlink(missing_ok=True)
                                except OSError:
                                    pass
                        await db.delete(old_iris)
                        await db.flush()
            row_id = existing.id
        else:
            cm = CandidateMatch(
                exam_id=body.exam_id, shift_id=shift.id, center_id=center.id,
                candidate_no=body.candidate_no, candidate_no_plain=body.candidate_no,
                name=body.name, roll_no=body.roll_no, roll_no_plain=body.roll_no,
                father_name=body.father_name, mother_name=body.mother_name, dob=body.dob,
                photo_id=photo_id, fingerprint_id=fp_id, iris_id=iris_id,
            )
            db.add(cm)
            await db.flush()
            row_id = cm.id

    await log_admin(db, admin["sub"], "add_candidate",
                    {"exam_id": str(body.exam_id), "candidate_no": body.candidate_no})
    return {"status": "ok", "candidate_id": str(row_id)}


# ── Attendance ─────────────────────────────────────────────────────────────────

@router.get("/attendance")
async def get_attendance(exam_id: UUID, center_id: UUID | None = None, shift_id: UUID | None = None,
                          db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    result = await _attendance_data(db, exam_id, center_id, shift_id)
    await log_admin(db, admin["sub"], "get_attendance", {"exam_id": str(exam_id)})
    return result


# ── Matching ───────────────────────────────────────────────────────────────────

@router.get("/matching")
async def get_matching(exam_id: UUID, center_id: UUID | None = None, shift_id: UUID | None = None,
                        db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    results = await _build_match_rows(db, exam_id, center_id, shift_id)
    await log_admin(db, admin["sub"], "get_matching", {"exam_id": str(exam_id)})
    return results


# ── Duplicates ─────────────────────────────────────────────────────────────────

async def _duplicates_data(db: AsyncSession, exam_id: UUID,
                            center_id: UUID | None = None,
                            shift_id: UUID | None = None) -> list:
    """Return all submission rows for candidates with >1 submission (capture exam only)."""
    exam = await db.get(Exam, exam_id)
    if not exam or exam.type != "capture":
        return []

    # Find candidate_no_plain values with >1 submission row
    dup_q = (select(CandidateCapture.candidate_no_plain,
                    CandidateCapture.shift_id, CandidateCapture.center_id)
             .where(CandidateCapture.exam_id == exam_id,
                    CandidateCapture.new_photo_id.isnot(None))
             .group_by(CandidateCapture.candidate_no_plain,
                       CandidateCapture.shift_id, CandidateCapture.center_id)
             .having(func.count(CandidateCapture.id) > 1))
    if center_id:
        dup_q = dup_q.where(CandidateCapture.center_id == center_id)
    if shift_id:
        dup_q = dup_q.where(CandidateCapture.shift_id == shift_id)
    dup_keys = (await db.execute(dup_q)).all()
    if not dup_keys:
        return []

    # Fetch all submission rows for those candidates in one query
    rows_all = (await db.execute(
        select(CandidateCapture, Center, Shift)
        .join(Center, CandidateCapture.center_id == Center.id)
        .join(Shift, CandidateCapture.shift_id == Shift.id)
        .where(
            CandidateCapture.exam_id == exam_id,
            CandidateCapture.new_photo_id.isnot(None),
            or_(*(
                (
                    (CandidateCapture.candidate_no_plain == cno) &
                    (CandidateCapture.shift_id == sid) &
                    (CandidateCapture.center_id == cid)
                )
                for cno, sid, cid in dup_keys
            )),
        )
        .order_by(CandidateCapture.created_at.asc())
    )).all()

    photo_ids: set = set()
    fp_ids: set = set()
    iris_ids: set = set()
    for cc, _, _ in rows_all:
        if cc.photo_id:
            photo_ids.add(cc.photo_id)
        if cc.new_photo_id:
            photo_ids.add(cc.new_photo_id)
        if cc.fingerprint_id:
            fp_ids.add(cc.fingerprint_id)
        if cc.iris_id:
            iris_ids.add(cc.iris_id)

    photos = {p.id: p for p in (await db.execute(select(Photo).where(Photo.id.in_(photo_ids)))).scalars().all()} if photo_ids else {}
    fps = {f.id: f for f in (await db.execute(select(Fingerprint).where(Fingerprint.id.in_(fp_ids)))).scalars().all()} if fp_ids else {}
    irises = {i.id: i for i in (await db.execute(select(Iris).where(Iris.id.in_(iris_ids)))).scalars().all()} if iris_ids else {}

    photo_b64 = await _b64_map(photos, preferred_photo_bytes)
    fp_b64 = await _b64_map(fps, preferred_fp_bytes)
    iris_b64 = await _b64_map(irises, preferred_iris_bytes)

    return [
        CandidateCaptureMatchOut(
            capture_id=cc.id,
            candidate_no=cc.candidate_no,
            name=cc.name,
            roll_no=cc.roll_no,
            dob=cc.dob,
            attended=cc.attendance == "present",
            center_code=ctr.code,
            shift_code=shf.shift_code,
            photo_data=photo_b64.get(cc.photo_id),
            captured_photo_data=photo_b64.get(cc.new_photo_id),
            photo_match_status=cc.photo_match_status,
            fingerprint_data=fp_b64.get(cc.fingerprint_id),
            iris_data=iris_b64.get(cc.iris_id),
        )
        for cc, ctr, shf in rows_all
    ]


@router.get("/matching/duplicates")
async def get_duplicates(exam_id: UUID, center_id: UUID | None = None, shift_id: UUID | None = None,
                          db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    return await _duplicates_data(db, exam_id, center_id, shift_id)


# ── Centers Summary ────────────────────────────────────────────────────────────

@router.get("/centers-summary", response_model=list[CenterSummaryRow])
async def centers_summary(exam_id: UUID, shift_id: UUID | None = None,
                           db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    return await _centers_summary_data(db, exam_id, shift_id)


# ── Duplicate resolution ───────────────────────────────────────────────────────

@router.post("/matching/resolve-duplicate")
async def resolve_duplicate(body: ResolveDuplicateBody, db: AsyncSession = Depends(get_db),
                              admin: dict = Depends(require_admin)):
    keep = await db.get(CandidateCapture, body.keep_capture_id)
    if not keep or keep.new_photo_id is None:
        raise HTTPException(400, "Invalid keep_capture_id — must be a submission row")

    others = (await db.execute(
        select(CandidateCapture).where(
            CandidateCapture.exam_id == keep.exam_id,
            CandidateCapture.shift_id == keep.shift_id,
            CandidateCapture.center_id == keep.center_id,
            CandidateCapture.candidate_no_plain == keep.candidate_no_plain,
            CandidateCapture.new_photo_id.isnot(None),
            CandidateCapture.id != keep.id,
        )
    )).scalars().all()

    for row in others:
        await _delete_capture_submission(db, row)

    await log_admin(db, admin["sub"], "resolve_duplicate",
                    {"kept": str(body.keep_capture_id), "removed": len(others)})
    return {"status": "ok", "removed": len(others)}


# ── Downloads ──────────────────────────────────────────────────────────────────

@router.get("/attendance/download")
async def download_attendance(exam_id: UUID, format: str = Query("csv", regex="^(csv|pdf)$"),
                               center_id: UUID | None = None, shift_id: UUID | None = None,
                               db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    rows = await _attendance_data(db, exam_id, center_id, shift_id)
    headers = ["Candidate No", "Name", "Roll No", "Father", "DOB",
               "Attended", "Center", "Shift"]
    data = [
        [r.candidate_no, r.name, r.roll_no or "", r.father_name or "",
         str(r.dob) if r.dob else "", str(r.attended), r.center_code, r.shift_code or ""]
        for r in rows
    ]
    await log_admin(db, admin["sub"], f"download_attendance_{format}", {"exam_id": str(exam_id)})
    content = generate_csv(headers, data) if format == "csv" else generate_pdf("Attendance Report", headers, data)
    mt = "text/csv" if format == "csv" else "application/pdf"
    return StreamingResponse(io.BytesIO(content), media_type=mt,
                             headers={"Content-Disposition": f"attachment; filename=attendance.{format}"})


@router.get("/matching/download")
async def download_matching(exam_id: UUID, format: str = Query("csv", regex="^(csv|pdf)$"),
                             center_id: UUID | None = None, shift_id: UUID | None = None,
                             db: AsyncSession = Depends(get_db), admin: dict = Depends(require_admin)):
    exam = await db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Exam not found")

    await log_admin(db, admin["sub"], f"download_matching_{format}", {"exam_id": str(exam_id)})

    if exam.type == "capture":
        q = (select(CandidateCapture, Center, Shift)
             .join(Center, CandidateCapture.center_id == Center.id)
             .join(Shift, CandidateCapture.shift_id == Shift.id)
             .where(CandidateCapture.exam_id == exam_id)
             .order_by(CandidateCapture.candidate_no_plain, CandidateCapture.created_at.asc()))
        if center_id:
            q = q.where(CandidateCapture.center_id == center_id)
        if shift_id:
            q = q.where(CandidateCapture.shift_id == shift_id)
        rows = (await db.execute(q)).all()

        groups: dict = {}
        for cc, ctr, shf in rows:
            key = (cc.center_id, cc.shift_id, cc.candidate_no_plain)
            if key not in groups:
                groups[key] = {"ctr": ctr, "shf": shf, "seed": None, "subs": []}
            if cc.new_photo_id is None:
                if groups[key]["seed"] is None:
                    groups[key]["seed"] = cc
            else:
                groups[key]["subs"].append(cc)

        photo_ids: set = set()
        fp_ids: set = set()
        iris_ids: set = set()
        for g in groups.values():
            seed = g["seed"]
            if seed and seed.photo_id:
                photo_ids.add(seed.photo_id)
            for sub in g["subs"]:
                if sub.new_photo_id:
                    photo_ids.add(sub.new_photo_id)
                if sub.fingerprint_id:
                    fp_ids.add(sub.fingerprint_id)
                if sub.iris_id:
                    iris_ids.add(sub.iris_id)

        photos = {p.id: p for p in (await db.execute(select(Photo).where(Photo.id.in_(photo_ids)))).scalars().all()} if photo_ids else {}
        fps = {f.id: f for f in (await db.execute(select(Fingerprint).where(Fingerprint.id.in_(fp_ids)))).scalars().all()} if fp_ids else {}
        irises = {i.id: i for i in (await db.execute(select(Iris).where(Iris.id.in_(iris_ids)))).scalars().all()} if iris_ids else {}

        headers = ["Candidate No", "Name", "Attended",
                   "Ref Photo", "Captured Photo", "Photo Match",
                   "Fingerprint", "Iris", "Center", "Shift"]
        image_cols = [3, 4, 6, 7]
        pdf_col_widths = [2.5, 4.3, 1.6, 1.5, 1.5, 2.0, 1.5, 1.5, 2.5, 2.6]
        data = []
        for g in groups.values():
            seed = g["seed"]
            subs = g["subs"]
            ctr, shf = g["ctr"], g["shf"]
            latest = subs[-1] if subs else None
            ref = seed or (subs[0] if subs else None)
            if ref is None:
                continue
            attended = any(s.attendance == "present" for s in subs) or (seed and seed.attendance == "present")
            ref_photo = preferred_photo_bytes(photos[seed.photo_id]) if seed and seed.photo_id and seed.photo_id in photos else None
            cap_photo = preferred_photo_bytes(photos[latest.new_photo_id]) if latest and latest.new_photo_id and latest.new_photo_id in photos else None
            fp_bytes = preferred_fp_bytes(fps[latest.fingerprint_id]) if latest and latest.fingerprint_id and latest.fingerprint_id in fps else None
            iris_bytes = preferred_iris_bytes(irises[latest.iris_id]) if latest and latest.iris_id and latest.iris_id in irises else None
            ps = latest.photo_match_status or "" if latest else ""
            data.append([ref.candidate_no, ref.name, str(attended),
                         ref_photo, cap_photo, ps, fp_bytes, iris_bytes,
                         ctr.code, shf.shift_code or ""])

    else:
        q = (select(CandidateMatch, Center, Shift)
             .join(Center, CandidateMatch.center_id == Center.id)
             .join(Shift, CandidateMatch.shift_id == Shift.id)
             .where(CandidateMatch.exam_id == exam_id))
        if center_id:
            q = q.where(CandidateMatch.center_id == center_id)
        if shift_id:
            q = q.where(CandidateMatch.shift_id == shift_id)
        rows = (await db.execute(q)).all()

        photo_ids: set = set()
        fp_ids: set = set()
        iris_ids: set = set()
        for cm, _, _ in rows:
            for pid in [cm.photo_id, cm.new_photo_id]:
                if pid:
                    photo_ids.add(pid)
            for fid in [cm.fingerprint_id, cm.new_fingerprint_id]:
                if fid:
                    fp_ids.add(fid)
            for iid in [cm.iris_id, cm.new_iris_id]:
                if iid:
                    iris_ids.add(iid)

        photos = {p.id: p for p in (await db.execute(select(Photo).where(Photo.id.in_(photo_ids)))).scalars().all()} if photo_ids else {}
        fps = {f.id: f for f in (await db.execute(select(Fingerprint).where(Fingerprint.id.in_(fp_ids)))).scalars().all()} if fp_ids else {}
        irises = {i.id: i for i in (await db.execute(select(Iris).where(Iris.id.in_(iris_ids)))).scalars().all()} if iris_ids else {}

        headers = ["Candidate No", "Name", "Attended",
                   "Ref Photo", "Captured Photo", "Photo Match",
                   "Ref Fingerprint", "Live Fingerprint", "Fingerprint Match",
                   "Ref Iris", "Live Iris", "Iris Match",
                   "Center", "Shift"]
        image_cols = [3, 4, 6, 7, 9, 10]
        pdf_col_widths = [2.1, 3.0, 1.5, 1.5, 1.5, 1.6, 1.5, 1.5, 1.9, 1.5, 1.5, 1.5, 1.6, 2.0]
        data = []
        for cm, ctr, shf in rows:
            ref_photo = preferred_photo_bytes(photos[cm.photo_id]) if cm.photo_id and cm.photo_id in photos else None
            cap_photo = preferred_photo_bytes(photos[cm.new_photo_id]) if cm.new_photo_id and cm.new_photo_id in photos else None
            ref_fp = preferred_fp_bytes(fps[cm.fingerprint_id]) if cm.fingerprint_id and cm.fingerprint_id in fps else None
            live_fp = preferred_fp_bytes(fps[cm.new_fingerprint_id]) if cm.new_fingerprint_id and cm.new_fingerprint_id in fps else None
            ref_iris = preferred_iris_bytes(irises[cm.iris_id]) if cm.iris_id and cm.iris_id in irises else None
            live_iris = preferred_iris_bytes(irises[cm.new_iris_id]) if cm.new_iris_id and cm.new_iris_id in irises else None
            data.append([cm.candidate_no, cm.name, str(cm.attendance == "present"),
                         ref_photo, cap_photo, cm.photo_match_status or "",
                         ref_fp, live_fp, cm.fingerprint_match_status or "",
                         ref_iris, live_iris, cm.iris_match_status or "",
                         ctr.code, shf.shift_code or ""])

    if format == "csv":
        content = generate_matching_csv(headers, data, image_cols)
        mt = "text/csv"
    else:
        content = generate_matching_pdf("Matching Report", headers, data, image_cols,
                                         exam_name=exam.name, col_widths=pdf_col_widths)
        mt = "application/pdf"
    return StreamingResponse(io.BytesIO(content), media_type=mt,
                             headers={"Content-Disposition": f"attachment; filename=matching.{format}"})


@router.delete("/matching1/{capture_id}")
async def delete_matching1(capture_id: UUID, db: AsyncSession = Depends(get_db),
                            admin: dict = Depends(require_admin)):
    row = await db.get(CandidateCapture, capture_id)
    if not row:
        raise HTTPException(404, "Not found")
    if row.new_photo_id is None:
        raise HTTPException(400, "Cannot delete a seed row — only submission rows can be removed")
    await _delete_capture_submission(db, row)
    await log_admin(db, admin["sub"], "delete_capture_submission", {"capture_id": str(capture_id)})
    return {"status": "deleted"}


# ── Logs ───────────────────────────────────────────────────────────────────────

@router.get("/logs/auth")
async def get_auth_logs(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    logs = (await db.execute(
        select(AuthLog).order_by(AuthLog.created_at.desc())
    )).scalars().all()

    photo_ids = {log.photo_id for log in logs if log.photo_id}
    photos_by_id = {p.id: p for p in (await db.execute(
        select(Photo).where(Photo.id.in_(photo_ids))
    )).scalars().all()} if photo_ids else {}

    photo_b64 = await _b64_map(photos_by_id, preferred_photo_bytes)
    return [
        AuthLogOut(id=log.id, mobile=log.mobile, role=log.role,
                   name=log.name, created_at=log.created_at,
                   photo_data=photo_b64.get(log.photo_id))
        for log in logs
    ]


@router.get("/logs/admin")
async def get_admin_logs(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    return (await db.execute(select(AdminLog).order_by(AdminLog.created_at.desc()))).scalars().all()


@router.get("/logs/supervisor")
async def get_supervisor_logs(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    return (await db.execute(select(SupervisorLog).order_by(SupervisorLog.created_at.desc()))).scalars().all()


@router.get("/logs/operator")
async def get_operator_logs(db: AsyncSession = Depends(get_db), _: dict = Depends(require_admin)):
    return (await db.execute(select(OperatorLog).order_by(OperatorLog.created_at.desc()))).scalars().all()
