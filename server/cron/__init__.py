"""Background cron jobs."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, union

from database import async_session
from models import CandidateCapture, CandidateMatch, Photo, Fingerprint, Iris
from utils.biometric_storage import (
    read_file, compress_jpeg, bmp_to_jpeg,
)
from utils.matching import match_photos, extract_fingerprint_template, extract_iris_template

logger = logging.getLogger("cron")


async def match_pending_photos():
    """Match up to 25 pending face photos (CandidateCapture) in parallel."""
    try:
        async with async_session() as db:
            rows = (await db.execute(
                select(CandidateCapture)
                .where(CandidateCapture.photo_match_status == "pending",
                       CandidateCapture.new_photo_id.isnot(None))
                .limit(25)
            )).scalars().all()
            if not rows:
                return

            tasks_args = []
            for cc in rows:
                if not cc.photo_id or not cc.new_photo_id:
                    continue
                orig = await db.get(Photo, cc.photo_id)
                new = await db.get(Photo, cc.new_photo_id)
                if not orig or not new:
                    continue
                orig_bytes = await asyncio.to_thread(read_file, orig.file_path)
                new_bytes = await asyncio.to_thread(read_file, new.file_path)
                if not orig_bytes or not new_bytes:
                    continue
                tasks_args.append((cc, orig_bytes, new_bytes))

            async def _process(cc, orig_bytes, new_bytes):
                try:
                    matched = await match_photos(orig_bytes, new_bytes)
                    cc.photo_match_status = "match" if matched else "mismatch"
                    cc.matched_at = datetime.utcnow()
                except Exception:
                    pass

            await asyncio.gather(*[_process(*args) for args in tasks_args])
            await db.commit()
            if tasks_args:
                logger.info("%d face photo(s) matched", len(tasks_args))
    except Exception as e:
        logger.error("match_pending_photos failed: %s", e)


async def extract_fingerprint_templates():
    """Extract templates only for Fingerprint records referenced in candidate_matches."""
    try:
        async with async_session() as db:
            ref_ids_q = union(
                select(CandidateMatch.fingerprint_id).where(
                    CandidateMatch.fingerprint_id.isnot(None)),
                select(CandidateMatch.new_fingerprint_id).where(
                    CandidateMatch.new_fingerprint_id.isnot(None)),
            )
            rows = (await db.execute(
                select(Fingerprint)
                .where(Fingerprint.id.in_(ref_ids_q), Fingerprint.template.is_(None))
                .limit(10)
            )).scalars().all()

            extracted = 0
            for fp in rows:
                data = await asyncio.to_thread(read_file, fp.file_path)
                if not data:
                    continue
                tmpl = await extract_fingerprint_template(data)
                if tmpl:
                    fp.template = tmpl
                    extracted += 1

            await db.commit()
            if extracted:
                logger.info("%d fingerprint template(s) extracted", extracted)
    except Exception as e:
        logger.error("extract_fingerprint_templates failed: %s", e)


async def extract_iris_templates():
    """Extract templates only for Iris records referenced in candidate_matches."""
    try:
        async with async_session() as db:
            ref_ids_q = union(
                select(CandidateMatch.iris_id).where(
                    CandidateMatch.iris_id.isnot(None)),
                select(CandidateMatch.new_iris_id).where(
                    CandidateMatch.new_iris_id.isnot(None)),
            )
            rows = (await db.execute(
                select(Iris)
                .where(Iris.id.in_(ref_ids_q), Iris.template.is_(None))
                .limit(16)
            )).scalars().all()

            extracted = 0
            for ir in rows:
                data = await asyncio.to_thread(read_file, ir.file_path)
                if not data:
                    continue
                tmpl = await extract_iris_template(data)
                if tmpl:
                    ir.template = tmpl
                    extracted += 1

            await db.commit()
            if extracted:
                logger.info("%d iris template(s) extracted", extracted)
    except Exception as e:
        logger.error("extract_iris_templates failed: %s", e)


async def compress_face_images():
    """JPEG quality reduction for face photos. 30/run."""
    try:
        async with async_session() as db:
            rows = (await db.execute(
                select(Photo)
                .where(Photo.compressed_file_path.is_(None), Photo.file_path.isnot(None))
                .limit(30)
            )).scalars().all()
            compressed = 0
            for photo in rows:
                raw = await asyncio.to_thread(read_file, photo.file_path)
                if not raw:
                    continue
                try:
                    data, _ = await asyncio.to_thread(lambda r=raw: compress_jpeg(r, quality=40))
                    orig = Path(photo.file_path)
                    comp_path = str(orig.parent / f"{orig.stem}_compressed.jpg")
                    await asyncio.to_thread(Path(comp_path).write_bytes, data)
                    photo.compressed_file_path = comp_path
                    compressed += 1
                except Exception:
                    pass
            await db.commit()
            if compressed:
                logger.info("%d face image(s) compressed", compressed)
    except Exception as e:
        logger.error("compress_face_images failed: %s", e)


async def compress_fingerprint_images():
    """BMP→JPG for fingerprints. 20/run, quality=50."""
    try:
        async with async_session() as db:
            rows = (await db.execute(
                select(Fingerprint)
                .where(Fingerprint.compressed_file_path.is_(None), Fingerprint.file_path.isnot(None))
                .limit(20)
            )).scalars().all()
            compressed = 0
            for fp in rows:
                raw = await asyncio.to_thread(read_file, fp.file_path)
                if not raw:
                    continue
                try:
                    data, _ = await asyncio.to_thread(lambda r=raw: bmp_to_jpeg(r, quality=50))
                    orig = Path(fp.file_path)
                    comp_path = str(orig.parent / f"{orig.stem}_compressed.jpg")
                    await asyncio.to_thread(Path(comp_path).write_bytes, data)
                    fp.compressed_file_path = comp_path
                    compressed += 1
                except Exception:
                    pass
            await db.commit()
            if compressed:
                logger.info("%d fingerprint image(s) compressed", compressed)
    except Exception as e:
        logger.error("compress_fingerprint_images failed: %s", e)


async def compress_iris_images():
    """BMP→JPG for iris images. 20/run, quality=50."""
    try:
        async with async_session() as db:
            rows = (await db.execute(
                select(Iris)
                .where(Iris.compressed_file_path.is_(None), Iris.file_path.isnot(None))
                .limit(20)
            )).scalars().all()
            compressed = 0
            for ir in rows:
                raw = await asyncio.to_thread(read_file, ir.file_path)
                if not raw:
                    continue
                try:
                    data, _ = await asyncio.to_thread(lambda r=raw: bmp_to_jpeg(r, quality=50))
                    orig = Path(ir.file_path)
                    comp_path = str(orig.parent / f"{orig.stem}_compressed.jpg")
                    await asyncio.to_thread(Path(comp_path).write_bytes, data)
                    ir.compressed_file_path = comp_path
                    compressed += 1
                except Exception:
                    pass
            await db.commit()
            if compressed:
                logger.info("%d iris image(s) compressed", compressed)
    except Exception as e:
        logger.error("compress_iris_images failed: %s", e)