"""Standalone APScheduler process — runs separately from API workers."""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import engine
from models import Base
from cron import (
    match_pending_photos, extract_fingerprint_templates, extract_iris_templates,
    compress_face_images, compress_fingerprint_images, compress_iris_images,
)
from utils.matching import init_http_clients, close_http_clients

logging.basicConfig(level=logging.INFO)
logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def run():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("Scheduler: DB tables ready")

    init_http_clients()

    scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 15})
    # Face matching: 25/run every 5s with asyncio.gather → ~90K/hour
    scheduler.add_job(match_pending_photos, "interval", seconds=5, id="match_photos")
    scheduler.add_job(extract_fingerprint_templates, "interval", seconds=6, id="extract_fp",
                      start_date="2000-01-01 00:00:10")
    scheduler.add_job(extract_iris_templates, "interval", seconds=4, id="extract_iris",
                      start_date="2000-01-01 00:00:20")
    # Compression jobs — staggered to avoid I/O spikes
    scheduler.add_job(compress_face_images, "interval", seconds=2, id="compress_face",
                      start_date="2000-01-01 00:00:05")
    scheduler.add_job(compress_fingerprint_images, "interval", seconds=2, id="compress_fp",
                      start_date="2000-01-01 00:00:15")
    scheduler.add_job(compress_iris_images, "interval", seconds=2, id="compress_iris",
                      start_date="2000-01-01 00:00:25")
    scheduler.start()
    logging.info("Scheduler: Cron jobs started")

    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        await close_http_clients()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
