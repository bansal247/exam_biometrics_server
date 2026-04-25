"""Exam Biometrics API Server."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import get_settings
from database import engine
from models import Base
from routers import admin, supervisor, operator, load
from utils.limiter import limiter
from utils.matching import init_http_clients, close_http_clients

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("DB tables ready")

    init_http_clients()
    logging.info("HTTP clients ready")

    yield

    await close_http_clients()
    await engine.dispose()


app = FastAPI(title="Exam Biometrics API", version="1.0.0", lifespan=lifespan)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — restrict to configured origins (admin panel only).
# The Android app communicates directly and does not need CORS.
_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
if not _origins:
    _origins = ["http://localhost", "http://127.0.0.1"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Sync-Key", "Cookie"],
)

app.include_router(admin.router)
app.include_router(supervisor.router)
app.include_router(operator.router)
app.include_router(load.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
