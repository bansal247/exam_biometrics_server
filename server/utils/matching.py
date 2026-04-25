"""Biometric matching via external services."""

import asyncio
import base64
import logging

import boto3
import botocore.exceptions
import httpx

from config import get_settings

logger = logging.getLogger("matching")

# ── HTTP client singletons (initialised in lifespan, never recreated per-request) ──
_sourceafis: httpx.AsyncClient | None = None
_iris: httpx.AsyncClient | None = None

# ── boto3 client singleton (sync, used via run_in_executor) ─────────────────
_rekognition = None


def init_http_clients() -> None:
    """Create persistent HTTP clients. Call once at application startup."""
    global _sourceafis, _iris
    s = get_settings()
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    _sourceafis = httpx.AsyncClient(base_url=s.SOURCEAFIS_URL, timeout=30, limits=limits)
    _iris       = httpx.AsyncClient(base_url=s.IRIS_ENDPOINT,  timeout=60, limits=limits)


async def close_http_clients() -> None:
    """Close HTTP clients gracefully. Call once at application shutdown."""
    if _sourceafis:
        await _sourceafis.aclose()
    if _iris:
        await _iris.aclose()


def _get_rekognition():
    """Return the shared boto3 Rekognition client, creating it on first call."""
    global _rekognition
    if _rekognition is None:
        s = get_settings()
        _rekognition = boto3.client(
            "rekognition",
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
            region_name=s.AWS_REGION,
        )
    return _rekognition


# ── Photo matching (AWS Rekognition) ────────────────────────────────────────

async def match_photos(source_bytes: bytes, target_bytes: bytes) -> bool:
    s = get_settings()

    def _compare() -> bool:
        resp = _get_rekognition().compare_faces(
            SourceImage={"Bytes": source_bytes},
            TargetImage={"Bytes": target_bytes},
            SimilarityThreshold=s.FACE_MATCH_THRESHOLD,
        )
        return len(resp.get("FaceMatches", [])) > 0

    try:
        # boto3 is synchronous — run in a thread pool so the event loop stays free
        return await asyncio.get_event_loop().run_in_executor(None, _compare)
        # return True  # skip actual matching for now to speed up testing
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "InvalidParameterException":
            # No detectable face in image — expected, not an error
            logger.warning(f"match_photos: no face detected ({e})")
        else:
            logger.error(f"match_photos: {e}")
        return False
    except Exception as e:
        logger.error(f"match_photos: {e}")
        return False


# ── Fingerprint (SourceAFIS) ─────────────────────────────────────────────────

async def extract_fingerprint_template(bmp_bytes: bytes) -> bytes | None:
    try:
        resp = await _sourceafis.post("/extract", json={
            "image": base64.b64encode(bmp_bytes).decode(),
        })
        if resp.status_code == 200:
            tmpl = resp.json().get("template")
            if tmpl:
                return base64.b64decode(tmpl)
    except Exception as e:
        logger.error(f"extract_fingerprint_template: {e}")
    return None


async def match_fingerprints(template1: bytes, template2: bytes) -> bool:
    try:
        resp = await _sourceafis.post("/match", json={
            "probe":     base64.b64encode(template1).decode(),
            "candidate": base64.b64encode(template2).decode(),
        })
        if resp.status_code == 200:
            score = resp.json().get("score", 0)
            return float(score) >= get_settings().FINGERPRINT_MATCH_THRESHOLD
    except Exception as e:
        logger.error(f"match_fingerprints: {e}")
    return False


# ── Iris ─────────────────────────────────────────────────────────────────────

async def extract_iris_template(bmp_bytes: bytes) -> bytes | None:
    try:
        resp = await _iris.post("/extract", json={
            "img": base64.b64encode(bmp_bytes).decode(),
        })
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "OK" and data.get("template"):
                return base64.b64decode(data["template"])
    except Exception as e:
        logger.error(f"extract_iris_template: {e}")
    return None


async def match_iris(template1: bytes, template2: bytes) -> bool:
    try:
        resp = await _iris.post("/match", json={
            "probe":     base64.b64encode(template1).decode(),
            "candidate": base64.b64encode(template2).decode(),
        })
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "OK":
                return int(data.get("score", 0)) >= get_settings().IRIS_MATCH_THRESHOLD
    except Exception as e:
        logger.error(f"match_iris: {e}")
    return False
