"""Supervisor endpoints."""

import io
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Supervisor, Exam, DeviceSession, CandidateCapture, CandidateMatch, Center, Shift
from schemas import UserLogin, TokenResponse, CenterSummaryRow, ResolveDuplicateBody
from utils.auth import create_token, require_supervisor, verify_password
from utils.export import generate_csv, generate_pdf, generate_matching_csv, generate_matching_pdf
from utils.logger import log_supervisor, log_auth

from routers.admin import (
    _attendance_data, _build_match_rows, _centers_summary_data,
    _delete_capture_submission, _duplicates_data,
)

router = APIRouter(prefix="/supervisor", tags=["Supervisor"])


@router.post("/login", response_model=TokenResponse)
async def supervisor_login(body: UserLogin, response: Response, db: AsyncSession = Depends(get_db)):
    sup = await db.scalar(select(Supervisor).where(Supervisor.phone_plain == body.phone))
    if not sup or not verify_password(body.password, sup.password):
        raise HTTPException(401, "Invalid credentials")
    token = create_token({"role": "supervisor", "sub": str(sup.id), "exam_id": str(sup.exam_id)},
                         expires_minutes=1440)
    response.set_cookie("auth_token", token, httponly=True, samesite="lax", max_age=86400)
    await log_auth(db, mobile=body.phone, role="supervisor")
    await log_supervisor(db, str(sup.id), "login", "Supervisor logged in")
    return TokenResponse(access_token=token)


@router.get("/exam")
async def get_exam(db: AsyncSession = Depends(get_db), sup: dict = Depends(require_supervisor)):
    exam = await db.get(Exam, UUID(sup["exam_id"]))
    if not exam:
        raise HTTPException(404, "Exam not found")
    return {"exam_id": str(exam.id), "name": exam.name, "type": exam.type}


@router.post("/logout")
async def supervisor_logout(response: Response):
    response.set_cookie("auth_token", "", max_age=0, httponly=True, samesite="lax")
    return {"status": "ok"}


@router.get("/attendance")
async def get_attendance(exam_id: UUID, center_id: UUID | None = None, shift_id: UUID | None = None,
                          db: AsyncSession = Depends(get_db), sup: dict = Depends(require_supervisor)):
    result = await _attendance_data(db, exam_id, center_id, shift_id)
    await log_supervisor(db, sup["sub"], "get_attendance", {"exam_id": str(exam_id)})
    return result


@router.get("/matching/duplicates")
async def get_duplicates(exam_id: UUID, center_id: UUID | None = None, shift_id: UUID | None = None,
                          db: AsyncSession = Depends(get_db), sup: dict = Depends(require_supervisor)):
    if str(exam_id) != sup["exam_id"]:
        raise HTTPException(403, "Not your exam")
    result = await _duplicates_data(db, exam_id, center_id, shift_id)
    await log_supervisor(db, sup["sub"], "get_duplicates", {"exam_id": str(exam_id)})
    return result


@router.get("/matching")
async def get_matching(exam_id: UUID, center_id: UUID | None = None, shift_id: UUID | None = None,
                        db: AsyncSession = Depends(get_db), sup: dict = Depends(require_supervisor)):
    results = await _build_match_rows(db, exam_id, center_id, shift_id)
    await log_supervisor(db, sup["sub"], "get_matching", {"exam_id": str(exam_id)})
    return results


@router.get("/centers-summary", response_model=list[CenterSummaryRow])
async def centers_summary(shift_id: UUID | None = None,
                           db: AsyncSession = Depends(get_db), sup: dict = Depends(require_supervisor)):
    exam_id = UUID(sup["exam_id"])
    return await _centers_summary_data(db, exam_id, shift_id)


@router.post("/matching/resolve-duplicate")
async def resolve_duplicate(body: ResolveDuplicateBody, db: AsyncSession = Depends(get_db),
                              sup: dict = Depends(require_supervisor)):
    from models import CandidateCapture
    keep = await db.get(CandidateCapture, body.keep_capture_id)
    if not keep or keep.new_photo_id is None:
        raise HTTPException(400, "Invalid keep_capture_id — must be a submission row")

    from sqlalchemy import select as sa_select
    others = (await db.execute(
        sa_select(CandidateCapture).where(
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

    await log_supervisor(db, sup["sub"], "resolve_duplicate",
                         {"kept": str(body.keep_capture_id), "removed": len(others)})
    return {"status": "ok", "removed": len(others)}


@router.get("/attendance/download")
async def download_attendance(exam_id: UUID, format: str = Query("csv", regex="^(csv|pdf)$"),
                               center_id: UUID | None = None, shift_id: UUID | None = None,
                               db: AsyncSession = Depends(get_db), sup: dict = Depends(require_supervisor)):
    rows = await _attendance_data(db, exam_id, center_id, shift_id)
    headers = ["Candidate No", "Name", "Roll No", "Father", "DOB",
               "Attended", "Center", "Shift"]
    data = [
        [r.candidate_no, r.name, r.roll_no or "", r.father_name or "",
         str(r.dob) if r.dob else "", str(r.attended), r.center_code, r.shift_code or ""]
        for r in rows
    ]
    await log_supervisor(db, sup["sub"], f"download_attendance_{format}", {"exam_id": str(exam_id)})
    content = generate_csv(headers, data) if format == "csv" else generate_pdf("Attendance", headers, data)
    mt = "text/csv" if format == "csv" else "application/pdf"
    return StreamingResponse(io.BytesIO(content), media_type=mt,
                             headers={"Content-Disposition": f"attachment; filename=attendance.{format}"})


@router.get("/matching/download")
async def download_matching(exam_id: UUID, format: str = Query("csv", regex="^(csv|pdf)$"),
                             center_id: UUID | None = None, shift_id: UUID | None = None,
                             db: AsyncSession = Depends(get_db), sup: dict = Depends(require_supervisor)):
    from routers.admin import download_matching as admin_download_matching
    # Delegate to the shared admin download implementation, returning a StreamingResponse
    exam = await db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(404, "Exam not found")

    await log_supervisor(db, sup["sub"], f"download_matching_{format}", {"exam_id": str(exam_id)})

    # Re-use admin download logic by building data the same way
    from utils.biometric_storage import preferred_photo_bytes, preferred_fp_bytes, preferred_iris_bytes
    from sqlalchemy import select as sa_select
    from models import Photo, Fingerprint, Iris
    from collections import defaultdict

    if exam.type == "capture":
        q = (sa_select(CandidateCapture, Center, Shift)
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

        photos = {p.id: p for p in (await db.execute(sa_select(Photo).where(Photo.id.in_(photo_ids)))).scalars().all()} if photo_ids else {}
        fps = {f.id: f for f in (await db.execute(sa_select(Fingerprint).where(Fingerprint.id.in_(fp_ids)))).scalars().all()} if fp_ids else {}
        irises = {i.id: i for i in (await db.execute(sa_select(Iris).where(Iris.id.in_(iris_ids)))).scalars().all()} if iris_ids else {}

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
        q = (sa_select(CandidateMatch, Center, Shift)
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

        photos = {p.id: p for p in (await db.execute(sa_select(Photo).where(Photo.id.in_(photo_ids)))).scalars().all()} if photo_ids else {}
        fps = {f.id: f for f in (await db.execute(sa_select(Fingerprint).where(Fingerprint.id.in_(fp_ids)))).scalars().all()} if fp_ids else {}
        irises = {i.id: i for i in (await db.execute(sa_select(Iris).where(Iris.id.in_(iris_ids)))).scalars().all()} if iris_ids else {}

        headers = ["Candidate No", "Name", "Attended",
                   "Ref Photo", "Captured Photo", "Photo Match",
                   "Ref Fingerprint", "Live Fingerprint", "Fingerprint Match",
                   "Ref Iris", "Live Iris", "Iris Match",
                   "Center", "Shift"]
        image_cols = [3, 4, 6, 7, 9, 10]
        pdf_col_widths = [2.1, 3.0, 1.5, 1.5, 1.5, 1.6, 1.5, 1.5, 1.9, 1.5, 1.5, 1.5, 1.6, 2.0]
        data = []
        for cm, ctr, shf in rows:
            from utils.biometric_storage import preferred_photo_bytes as ppb, preferred_fp_bytes as pfb, preferred_iris_bytes as pib
            ref_photo = ppb(photos[cm.photo_id]) if cm.photo_id and cm.photo_id in photos else None
            cap_photo = ppb(photos[cm.new_photo_id]) if cm.new_photo_id and cm.new_photo_id in photos else None
            ref_fp = pfb(fps[cm.fingerprint_id]) if cm.fingerprint_id and cm.fingerprint_id in fps else None
            live_fp = pfb(fps[cm.new_fingerprint_id]) if cm.new_fingerprint_id and cm.new_fingerprint_id in fps else None
            ref_iris = pib(irises[cm.iris_id]) if cm.iris_id and cm.iris_id in irises else None
            live_iris = pib(irises[cm.new_iris_id]) if cm.new_iris_id and cm.new_iris_id in irises else None
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
