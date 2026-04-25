"""CLI script to export all biometric data for an exam.

Memory model:
- At most PDF_BATCH ORM rows are in memory at once (loaded per DB page)
- Only the photo/fp/iris objects referenced by that page are loaded
- Image bytes are read from disk only during PDF flush, then freed
- Peak RAM per batch ≈ PDF_BATCH × images_per_candidate × avg_image_size

Usage (inside the Docker container):
    python utils/generate_zip.py --exam-id <uuid>
    python utils/generate_zip.py --exam-id <uuid> --output /biometrics/exports/myexam
    python utils/generate_zip.py --exam-id <uuid> --zip
    python utils/generate_zip.py --exam-id <uuid> --include-load-test   # include load-test candidates
"""

import argparse
import asyncio
import shutil
import sys
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import UUID

PDF_BATCH = 200  # rows per DB page and per PDF part

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, load_only

from config import get_settings
from models import (
    Exam, Center, Shift, Photo, Fingerprint, Iris,
    CandidateCapture, CandidateMatch,
)
from utils.export import generate_matching_csv, generate_matching_pdf


# ── File helpers ───────────────────────────────────────────────────────────────

def _preferred_src(obj) -> str | None:
    if obj is None:
        return None
    return (obj.compressed_file_path or None) or obj.file_path


def _copy_file(obj, dest: Path) -> str | None:
    """Copy biometric file to dest folder; return str(dest) or None."""
    src = _preferred_src(obj)
    if not src:
        return None
    src_path = Path(src)
    if not src_path.exists():
        return None
    try:
        shutil.copy2(src_path, dest)
        return str(dest)
    except OSError:
        return None


def _flush_pdf_batch(out_dir, folder, title, headers, batch, part, is_multipart, image_cols, exam_name):
    """Read image bytes from stored paths, generate PDF part, write to disk."""
    pdf_rows = []
    for row in batch:
        pdf_row = list(row)
        for i in image_cols:
            p = pdf_row[i]
            if p and isinstance(p, str):
                path = Path(p)
                pdf_row[i] = path.read_bytes() if path.exists() else None
        pdf_rows.append(pdf_row)

    name = f"report_part{part}.pdf" if is_multipart else "report.pdf"
    (out_dir / folder / name).write_bytes(
        generate_matching_pdf(title, headers, pdf_rows, image_cols, exam_name=exam_name)
    )


# ── Biometric loader helpers ───────────────────────────────────────────────────

async def _load_photos(db, ids: set) -> dict:
    if not ids:
        return {}
    rows = (await db.execute(
        select(Photo).where(Photo.id.in_(ids))
        .options(load_only(Photo.id, Photo.file_path, Photo.compressed_file_path))
    )).scalars().all()
    return {p.id: p for p in rows}


async def _load_fps(db, ids: set) -> dict:
    if not ids:
        return {}
    rows = (await db.execute(
        select(Fingerprint).where(Fingerprint.id.in_(ids))
        .options(load_only(Fingerprint.id, Fingerprint.file_path, Fingerprint.compressed_file_path))
    )).scalars().all()
    return {f.id: f for f in rows}


async def _load_irises(db, ids: set) -> dict:
    if not ids:
        return {}
    rows = (await db.execute(
        select(Iris).where(Iris.id.in_(ids))
        .options(load_only(Iris.id, Iris.file_path, Iris.compressed_file_path))
    )).scalars().all()
    return {i.id: i for i in rows}


# ── Per-candidate processing ───────────────────────────────────────────────────

def _process_match_row(out_dir, folder, cm, photos, fps, irises, center_code, shift_code):
    cno     = cm.candidate_no_plain
    img_dir = out_dir / folder / "images"

    ref_photo = _copy_file(photos.get(cm.photo_id),           img_dir / f"ref_photo_{cno}.jpg")
    cap_photo = _copy_file(photos.get(cm.new_photo_id),        img_dir / f"photo_{cno}.jpg")
    ref_fp    = _copy_file(fps.get(cm.fingerprint_id),         img_dir / f"fp_ref_{cno}.bmp")
    live_fp   = _copy_file(fps.get(cm.new_fingerprint_id),     img_dir / f"fp2_{cno}.bmp")
    ref_iris  = _copy_file(irises.get(cm.iris_id),             img_dir / f"iris_ref_{cno}.bmp")
    live_iris = _copy_file(irises.get(cm.new_iris_id),         img_dir / f"iris2_{cno}.bmp")

    csv_row = [
        cno, cm.roll_no_plain or "", cm.name or "",
        center_code, shift_code,
        str(cm.attendance == "present"),
        f"images/{Path(ref_photo).name}" if ref_photo else "",
        f"images/{Path(cap_photo).name}" if cap_photo else "",
        cm.photo_match_status or "",
        f"images/{Path(ref_fp).name}"    if ref_fp    else "",
        f"images/{Path(live_fp).name}"   if live_fp   else "",
        cm.fingerprint_match_status or "",
        f"images/{Path(ref_iris).name}"  if ref_iris  else "",
        f"images/{Path(live_iris).name}" if live_iris else "",
        cm.iris_match_status or "",
    ]
    pdf_row = list(csv_row)
    pdf_row[6]  = ref_photo
    pdf_row[7]  = cap_photo
    pdf_row[9]  = ref_fp
    pdf_row[10] = live_fp
    pdf_row[12] = ref_iris
    pdf_row[13] = live_iris
    return csv_row, pdf_row


# ── Match exam export ──────────────────────────────────────────────────────────

async def _export_match(db, exam, pairs, out_dir, include_load_test: bool):
    csv_headers = [
        "candidate_no", "roll_no", "name", "center_code", "shift_code",
        "attended", "ref_photo", "captured_photo", "photo_match_status",
        "fp_ref", "fp2_live", "fp_match_status",
        "iris_ref", "iris2_live", "iris_match_status",
    ]
    image_cols = [6, 7, 9, 10, 12, 13]

    for gi, (center_id, shift_id) in enumerate(pairs):
        center = await db.get(Center, center_id)
        shift  = await db.get(Shift,  shift_id)
        center_code = center.code_plain
        shift_code  = shift.shift_code or str(shift.id)[:8]
        folder      = f"{center_code}_{shift_code}"
        title       = f"Report — {center_code} / {shift_code}"
        (out_dir / folder / "images").mkdir(parents=True, exist_ok=True)

        lt_filter = [] if include_load_test else [CandidateMatch.is_load_test == False]  # noqa: E712
        total = (await db.execute(
            select(func.count()).select_from(CandidateMatch)
            .where(CandidateMatch.exam_id == exam.id,
                   CandidateMatch.center_id == center_id,
                   CandidateMatch.shift_id  == shift_id,
                   *lt_filter)
        )).scalar()
        is_multipart = total > PDF_BATCH

        csv_data = []
        pdf_part = 1
        offset   = 0

        while True:
            batch = (await db.execute(
                select(CandidateMatch)
                .where(CandidateMatch.exam_id   == exam.id,
                       CandidateMatch.center_id == center_id,
                       CandidateMatch.shift_id  == shift_id,
                       *lt_filter)
                .order_by(CandidateMatch.candidate_no_plain)
                .offset(offset).limit(PDF_BATCH)
            )).scalars().all()
            if not batch:
                break

            photo_ids = {pid for cm in batch for pid in (cm.photo_id, cm.new_photo_id) if pid}
            fp_ids    = {fid for cm in batch for fid in (cm.fingerprint_id, cm.new_fingerprint_id) if fid}
            iris_ids  = {iid for cm in batch for iid in (cm.iris_id, cm.new_iris_id) if iid}

            photos  = await _load_photos(db, photo_ids)
            fps     = await _load_fps(db, fp_ids)
            irises  = await _load_irises(db, iris_ids)

            pdf_batch = []
            for cm in batch:
                csv_row, pdf_row = _process_match_row(
                    out_dir, folder, cm, photos, fps, irises, center_code, shift_code)
                csv_data.append(csv_row)
                pdf_batch.append(pdf_row)

            _flush_pdf_batch(out_dir, folder, title, csv_headers,
                             pdf_batch, pdf_part, is_multipart, image_cols, exam.name)

            del batch, photos, fps, irises, pdf_batch
            pdf_part += 1
            offset   += PDF_BATCH

        (out_dir / folder / "data.csv").write_bytes(
            generate_matching_csv(csv_headers, csv_data, []))
        print(f"  [{gi+1}/{len(pairs)}]  {folder}  ({total} candidates)", flush=True)


# ── Capture exam export ────────────────────────────────────────────────────────

async def _export_capture(db, exam, pairs, out_dir, include_load_test: bool):
    csv_headers = [
        "candidate_no", "roll_no", "name", "center_code", "shift_code",
        "attended", "ref_photo", "captured_photo", "photo_match_status",
        "fingerprint", "iris",
    ]
    image_cols = [6, 7, 9, 10]

    for gi, (center_id, shift_id) in enumerate(pairs):
        center = await db.get(Center, center_id)
        shift  = await db.get(Shift,  shift_id)
        center_code = center.code_plain
        shift_code  = shift.shift_code or str(shift.id)[:8]
        folder      = f"{center_code}_{shift_code}"
        title       = f"Report — {center_code} / {shift_code}"
        img_dir     = out_dir / folder / "images"
        img_dir.mkdir(parents=True, exist_ok=True)

        lt_filter = [] if include_load_test else [CandidateCapture.is_load_test == False]  # noqa: E712

        # Get distinct candidate_no_plain values (ordered) for pagination
        cno_rows = (await db.execute(
            select(distinct(CandidateCapture.candidate_no_plain))
            .where(CandidateCapture.exam_id   == exam.id,
                   CandidateCapture.center_id == center_id,
                   CandidateCapture.shift_id  == shift_id,
                   *lt_filter)
            .order_by(CandidateCapture.candidate_no_plain)
        )).scalars().all()

        total        = len(cno_rows)
        is_multipart = total > PDF_BATCH
        csv_data     = []
        pdf_part     = 1

        for page_start in range(0, total, PDF_BATCH):
            page_cnos = cno_rows[page_start : page_start + PDF_BATCH]

            # Load ALL rows (seed + submissions) for these PDF_BATCH candidates
            all_cc = (await db.execute(
                select(CandidateCapture)
                .where(CandidateCapture.exam_id   == exam.id,
                       CandidateCapture.center_id == center_id,
                       CandidateCapture.shift_id  == shift_id,
                       CandidateCapture.candidate_no_plain.in_(page_cnos),
                       *lt_filter)
                .order_by(CandidateCapture.candidate_no_plain,
                          CandidateCapture.created_at.asc())
            )).scalars().all()

            # Collect biometric IDs for this batch only
            photo_ids = set()
            fp_ids    = set()
            iris_ids  = set()
            for cc in all_cc:
                if cc.photo_id:       photo_ids.add(cc.photo_id)
                if cc.new_photo_id:   photo_ids.add(cc.new_photo_id)
                if cc.fingerprint_id: fp_ids.add(cc.fingerprint_id)
                if cc.iris_id:        iris_ids.add(cc.iris_id)

            photos  = await _load_photos(db, photo_ids)
            fps     = await _load_fps(db, fp_ids)
            irises  = await _load_irises(db, iris_ids)

            # Group seed + submissions per candidate_no_plain
            cno_map: dict = {}
            for cc in all_cc:
                cno = cc.candidate_no_plain
                if cno not in cno_map:
                    cno_map[cno] = {"seed": None, "subs": []}
                if cc.new_photo_id is None:
                    if cno_map[cno]["seed"] is None:
                        cno_map[cno]["seed"] = cc
                else:
                    cno_map[cno]["subs"].append(cc)

            pdf_batch = []
            for cno in page_cnos:
                g      = cno_map.get(cno)
                if not g:
                    continue
                seed   = g["seed"]
                subs   = g["subs"]
                latest = subs[-1] if subs else None
                ref    = seed or (subs[0] if subs else None)
                if ref is None:
                    continue

                attended = any(s.attendance == "present" for s in subs) or (
                    seed and seed.attendance == "present")
                ps = (latest.photo_match_status or "") if latest else ""

                ref_photo = _copy_file(photos.get(seed.photo_id if seed else None),
                                       img_dir / f"ref_photo_{cno}.jpg")
                cap_photo = _copy_file(photos.get(latest.new_photo_id if latest else None),
                                       img_dir / f"photo_{cno}.jpg")
                fp        = _copy_file(fps.get(latest.fingerprint_id if latest else None),
                                       img_dir / f"fp_{cno}.bmp")
                iris      = _copy_file(irises.get(latest.iris_id if latest else None),
                                       img_dir / f"iris_{cno}.bmp")

                csv_data.append([
                    cno, ref.roll_no_plain or "", ref.name or "",
                    center_code, shift_code, str(attended),
                    f"images/ref_photo_{cno}.jpg" if ref_photo else "",
                    f"images/photo_{cno}.jpg"     if cap_photo else "",
                    ps,
                    f"images/fp_{cno}.bmp"        if fp   else "",
                    f"images/iris_{cno}.bmp"       if iris else "",
                ])
                pdf_batch.append([
                    cno, ref.roll_no_plain or "", ref.name or "",
                    center_code, shift_code, str(attended),
                    ref_photo, cap_photo, ps, fp, iris,
                ])

            _flush_pdf_batch(out_dir, folder, title, csv_headers,
                             pdf_batch, pdf_part, is_multipart, image_cols, exam.name)

            del all_cc, photos, fps, irises, pdf_batch, cno_map
            pdf_part += 1

        (out_dir / folder / "data.csv").write_bytes(
            generate_matching_csv(csv_headers, csv_data, []))
        print(f"  [{gi+1}/{len(pairs)}]  {folder}  ({total} candidates)", flush=True)


# ── Orchestration ──────────────────────────────────────────────────────────────

async def _run(exam_id: UUID, out_dir: Path, make_zip: bool, include_load_test: bool):
    settings = get_settings()
    engine   = create_async_engine(settings.database_url, pool_pre_ping=True)
    Session  = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        exam = await db.get(Exam, exam_id)
        if not exam:
            print(f"ERROR: Exam {exam_id} not found.", file=sys.stderr)
            sys.exit(1)

        is_match = exam.type == "match"
        print(f"Exam : {exam.name}  (type={exam.type})")

        Model    = CandidateMatch if is_match else CandidateCapture
        lt_filter = [] if include_load_test else [Model.is_load_test == False]  # noqa: E712
        pairs = (await db.execute(
            select(Model.center_id, Model.shift_id)
            .where(Model.exam_id == exam_id, *lt_filter)
            .distinct()
        )).all()

        if not pairs:
            print("ERROR: No candidates found for this exam.", file=sys.stderr)
            sys.exit(1)

        print(f"Groups: {len(pairs)} center+shift combinations")
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"Output: {out_dir}")
        print("Exporting…")

        try:
            if is_match:
                await _export_match(db, exam, pairs, out_dir, include_load_test)
            else:
                await _export_capture(db, exam, pairs, out_dir, include_load_test)
        except Exception:
            traceback.print_exc()
            sys.exit(1)

    await engine.dispose()
    print(f"Done.  Files written to {out_dir}")

    if make_zip:
        zip_path = out_dir.with_suffix(".zip")
        print(f"Zipping → {zip_path} …")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for f in sorted(out_dir.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(out_dir.parent))
        size_mb = zip_path.stat().st_size / 1024 / 1024
        print(f"ZIP done.  {size_mb:.1f} MB → {zip_path}")


def main():
    parser = argparse.ArgumentParser(description="Export exam biometrics to a folder.")
    parser.add_argument("--exam-id", required=True, help="UUID of the exam to export")
    parser.add_argument("--output",  help="Output folder (default: BIOMETRICS_DIR/exports/<exam_id>_<date>)")
    parser.add_argument("--zip", action="store_true", help="Zip the output folder after export")
    parser.add_argument("--include-load-test", action="store_true",
                        help="Include load-test candidates in the export (excluded by default)")
    args = parser.parse_args()

    exam_id = UUID(args.exam_id)

    if args.output:
        out_dir = Path(args.output)
    else:
        settings = get_settings()
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        out_dir  = Path(settings.BIOMETRICS_DIR) / "exports" / f"{args.exam_id}_{date_str}"

    asyncio.run(_run(exam_id, out_dir, args.zip, args.include_load_test))


if __name__ == "__main__":
    main()
