# Exam Biometrics Platform

A production-grade biometric identity verification and exam proctoring system built end-to-end — from requirements to deployed infrastructure. The platform verifies candidate identity across three biometric modalities (face, fingerprint, iris), supports 100K+ candidates, and runs fully offline on field devices.

---

## System Overview

```
                        ┌──────────────────────────────────────────────────┐
                        │                  PUBLIC LAYER                    │
                        │                                                  │
          ┌─────────────┴──┐  ┌──────────────────┐  ┌────────────────────┐│
          │  Admin Panel   │  │ Supervisor Panel  │  │   Android App      ││
          │  HTML/CSS/JS   │  │  HTML/CSS/JS      │  │   Kotlin           ││
          │  :3000         │  │  :3001            │  │   (Field Devices)  ││
          └───────┬────────┘  └────────┬──────────┘  └─────────┬──────────┘│
                  │                    │                        │           │
                  └──────────────┬─────┘────────────────────────┘           │
                                 │                                          │
                        ┌────────▼──────────┐                               │
                        │   Nginx :80        │                               │
                        │   Reverse Proxy    │                               │
                        └────────┬──────────┘                               │
                                 │                                          │
                        └────────────────────────────────────────────────────┘
                                 │
          ┌──────────────────────┼─────────────────────────────┐
          │                INTERNAL NETWORK                     │
          │                                                     │
    ┌─────▼──────┐   ┌──────────────────┐   ┌───────────────┐ │
    │  FastAPI   │   │   PostgreSQL 16   │   │  APScheduler  │ │
    │  :8000     │   │   + PgBouncer     │   │  (Scheduler   │ │
    │  4 workers │   │   :5432           │   │   container)  │ │
    └─────┬──────┘   └──────────────────┘   └───────────────┘ │
          │                                                     │
          │          ┌──────────────────┐   ┌───────────────┐  │
          └──────────►  SourceAFIS      │   │  Iris Server  │  │
                     │  Java :8080      │   │  Java :8080   │  │
                     │  Fingerprint     │   │  Neurotec SDK │  │
                     └──────────────────┘   └───────────────┘  │
          │                                                     │
          └─────────────────────────────────────────────────────┘
                        Shared Volume (/biometrics)
```

---

## Components

### 1. Backend API — FastAPI + PostgreSQL

The core of the platform. Handles all business logic, data storage, and coordination between services.

**Key responsibilities:**
- Multi-role authentication (Admin, Supervisor, Operator) with JWT and bcrypt
- Candidate management — bulk upload, search, role-based visibility
- Biometric file ingestion (photo, fingerprint, iris) with async storage
- Coordinating face matching via AWS Rekognition
- Delegating fingerprint extraction/matching to SourceAFIS service
- Delegating iris extraction to Iris Server
- Audit logging for every operator/admin/supervisor action
- Exam, center, and shift lifecycle management

**Database design highlights:**
- PostgreSQL 16 with Fernet-encrypted PII columns at rest
- Plaintext shadow columns (`_plain` suffix) for efficient querying without decryption on every row
- Partial indexes for unprocessed biometric records (cron job targets)
- Composite indexes on (exam_id, shift_id, center_id, candidate_no)
- PgBouncer connection pooling (transaction mode, up to 5000 connections)

**Security:**
- Fernet symmetric encryption for all PII (names, roll numbers, phone numbers)
- bcrypt password hashing
- Rate limiting via slowapi
- CORS restricted to configured origins
- Complete audit trail across all user roles

---

### 2. Android App — Kotlin (Offline-First)

Field-deployed Android app used by operators to capture and verify biometrics at exam centers.

**Architecture:**
- Offline-first with Room local database for candidate caching
- WorkManager sync queue — submissions retry automatically on reconnect
- Background heartbeat worker maintains operator session
- Retrofit + OkHttp with 401/403 interceptor that broadcasts session expiry and auto-redirects to login
- All activities extend `BaseActivity` which handles session expiry centrally

**Capture Flow:**
1. Operator logs in with phone + password — receives JWT
2. App fetches and caches candidate list for their assigned center/shift
3. Operator captures face photo (CameraX), fingerprint (Mantra MFS100V9), iris (Mantra MIS100V2)
4. Each biometric is uploaded to the server immediately or queued for retry
5. Server-side cron jobs process face matching (AWS Rekognition) and template extraction asynchronously

**Verification Flow:**
1. Candidate identified by QR code scan (ML Kit) or roll number input
2. Live biometric captured on-device
3. Face and fingerprint — sent to server for matching against stored reference
4. Iris — fetched from server as BMP, matched locally using `MarvisAuth.MatchImage()` (on-device, no round trip)
5. Match result posted back to server; candidate status updated in real time

**Hardware SDKs integrated:**
- Mantra MFS100V9 — USB fingerprint scanner (JNI)
- Neurotec MarvisAuth — USB iris scanner (JNI, armeabi-v7a + arm64-v8a)
- USB device permission handled at runtime; device auto-detected on activity start

**Key screens:**
- Login — phone + password, session management
- Home — candidate list with search
- Registration — face capture + fingerprint enroll + iris enroll
- Verification — face + fingerprint + iris match with live score display
- Operator panel — shift/center assignment, device status

---

### 3. SourceAFIS Service — Java Microservice

Internal fingerprint processing service wrapping the open-source [SourceAFIS](https://sourceafis.machinezoo.com/) library.

**Endpoints (internal only):**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/extract` | Extract fingerprint template from base64-encoded BMP |
| POST | `/match` | Compare two templates, return similarity score |
| GET | `/health` | Health check |

The server uses a `BlockingQueue<NBiometricClient>` pool to manage concurrent extractions safely, with a configurable thread pool and per-request timeout watchdog.

---

### 4. Iris Server — Java Microservice (Neurotec)

Internal iris processing service using the Neurotec Biometrics SDK (licensed).

**Endpoints (internal only):**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/extract` | Extract iris template from base64-encoded BMP |
| POST | `/match` | Compare two iris templates, return score |
| GET | `/health` | Health check |

Uses a `NBiometricClient` pool with `IrisMatcher` and `IrisExtractor` licenses. Matching threshold configurable (default 48). Graceful shutdown waits up to 5s for in-flight requests before disposing client instances.

---

### 5. Background Scheduler — APScheduler

Runs as a separate container (same codebase, different entrypoint) to avoid blocking API workers.

| Job | Interval | Batch | Function |
|-----|----------|-------|----------|
| Face matching | 5s | 25 | AWS Rekognition comparison against reference photo |
| Fingerprint extraction | 6s | 10 | SourceAFIS template extraction |
| Iris extraction | 4s | 16 | Neurotec template extraction |
| Face compression | 2s | 30 | JPEG recompression (quality 40) |
| Fingerprint compression | 2s | 20 | BMP → JPEG (quality 50) |
| Iris compression | 2s | 20 | BMP → JPEG (quality 50) |

Throughput: ~90,000 face match operations per hour.

---

### 6. Web Panels — Admin & Supervisor

Static HTML/CSS/JS panels served via Nginx.

**Admin Panel:**
- Exam create/edit/archive
- Center and shift management
- Bulk candidate CSV upload
- Live dashboard (per-exam stats)
- Full audit log viewer
- Biometric data export

**Supervisor Panel:**
- All-centers view for their assigned exam
- Live device count per center
- Candidate status overview per shift

---

## Services Summary

| Service | Port | Exposed | Stack |
|---|---|---|---|
| nginx | 80 | Yes (public) | Nginx Alpine |
| server | 8000 | Internal | FastAPI, Uvicorn (4 workers) |
| scheduler | — | Internal | Python, APScheduler |
| db | 5432 | Internal | PostgreSQL 16 Alpine |
| pgbouncer | 5432 | Internal | PgBouncer |
| sourceafis | 8080 | Internal | Java 21, SourceAFIS |
| iris-server | 8080 | Internal | Java 21, Neurotec SDK |
| admin-panel | 3000 | Internal | Nginx, HTML/JS |
| supervisor-panel | 3001 | Internal | Nginx, HTML/JS |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI 0.115, SQLAlchemy 2.0 (async), asyncpg |
| Database | PostgreSQL 16, PgBouncer, Fernet encryption |
| Mobile | Android (Kotlin), Room, WorkManager, Retrofit, CameraX, ML Kit |
| Face matching | AWS Rekognition (boto3) |
| Fingerprint | SourceAFIS (Java), Mantra MFS100V9 SDK |
| Iris | Neurotec MarvisAuth SDK (Java + Android JNI) |
| Scheduling | APScheduler 3.10 |
| Auth | JWT (python-jose), bcrypt (passlib) |
| Infrastructure | Docker Compose, Nginx, uvicorn |
| Image processing | Pillow, aiofiles, ReportLab |

---

## Quick Start

```bash
# 1. Generate encryption key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 2. Copy and fill environment variables
cp .env.example .env

# 3. Build and start all services
docker-compose up --build -d

# 4. Verify
#    API docs:          http://localhost:8000/docs
#    Admin panel:       http://localhost/
#    Supervisor panel:  http://localhost/sp/
#    Health:            http://localhost/health
```

**Required environment variables:**
```
ENCRYPTION_KEY=       # Fernet key from step 1
JWT_SECRET=           # Any strong random string
ADMIN_PHONE=          # Initial admin login
ADMIN_PASSWORD=       # Initial admin password
AWS_ACCESS_KEY_ID=    # For face matching (optional)
AWS_SECRET_ACCESS_KEY=
AWS_REGION=           # Default: ap-south-1
```

---

## API Structure

```
/admin/*        → Admin-only endpoints (exam, center, shift, candidate, logs)
/supervisor/*   → Supervisor endpoints (dashboard, candidate view)
/operator/*     → Operator endpoints (login, biometric upload, attendance)
/load/*         → Load test endpoints (gated by LOAD_TOKEN)
```

Full interactive docs available at `/docs` (Swagger UI) once running.

---

## What This Project Demonstrates

This is a complete product built by a single engineer — from database schema to deployed infrastructure, covering:

- **Requirements → Architecture**: Designed multi-service system to handle concurrent field operators across 1000+ exam centers
- **Security-first data design**: PII encrypted at rest, plaintext shadow columns for search performance, full audit trail
- **Hardware integration**: USB biometric device SDKs (fingerprint + iris) on Android with JNI native libs
- **Async at scale**: Non-blocking API workers + dedicated scheduler container processing 90K+ operations/hour
- **Offline-first mobile**: WorkManager retry queue, local Room cache, session expiry handling
- **Production deployment**: 9-service Docker Compose stack with health checks, connection pooling, and reverse proxy tuning
